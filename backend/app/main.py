import ipaddress
from collections.abc import AsyncIterator, Collection
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from secrets import compare_digest

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import router
from app.broker import Broker
from app.config import Settings, get_settings
from app.extract.safe_network import make_safe_client
from app.llm import LLMClient
from app.package_size import package_size
from app.pipeline import PipelineDeps
from app.repair.loop import LLMProposer
from app.sandbox.docker_sandbox import DockerSandbox
from app.storage import SqliteDocCache, init_db, mark_interrupted_checks_failed


class RequireAllowedOrigin:
    def __init__(self, app: ASGIApp, allowed_origins: Collection[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origins = Headers(scope=scope).getlist("origin")
            if any(origin not in self.allowed_origins for origin in origins):
                response = JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RequireLocalPeer:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode()

    @staticmethod
    def _loopback(scope: Scope) -> bool:
        client = scope.get("client")
        if not client:
            return False
        try:
            address = ipaddress.ip_address(client[0])
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        return address.is_loopback

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not self._loopback(scope):
            scheme, _, supplied = Headers(scope=scope).get("authorization", "").partition(" ")
            if not (
                self.token
                and scheme.casefold() == "bearer"
                and compare_digest(self.token, supplied.encode())
            ):
                if scope["type"] == "websocket" and "websocket.http.response" not in scope.get(
                    "extensions", {}
                ):
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    denied = JSONResponse(
                        {"detail": "Requests from other machines need the API token"},
                        status_code=403,
                    )
                    await denied(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                media_type = headers.get("content-type", "").partition(";")[0].strip().lower()
                if media_type != "text/html":
                    headers.setdefault(
                        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
                    )
            await send(message)

        await self.app(scope, receive, add_headers if scope["type"] == "http" else send)


class WorksFrApp(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        # Cover error responses produced by Starlette's outer error handler too.
        return SecurityHeaders(super().build_middleware_stack())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(settings.database_path)
        mark_interrupted_checks_failed(settings.database_path)
        executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_checks)
        http = make_safe_client(timeout=30)
        llm = LLMClient(settings)
        doc_cache = SqliteDocCache(settings.database_path)
        app.state.settings = settings
        app.state.executor = executor
        app.state.broker = Broker()
        app.state.deps = PipelineDeps(
            settings,
            lambda check_id: DockerSandbox(
                settings,
                check_id,
                measure_package=lambda package, index: package_size(
                    package, http, doc_cache, index
                ),
            ),
            LLMProposer(llm),
            http,
            doc_cache,
            proposer_factory=lambda on_wait: LLMProposer(llm, on_wait=on_wait),
        )
        try:
            yield
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            llm.close()
            http.close()

    app = WorksFrApp(title="WorksFr", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequireAllowedOrigin, allowed_origins=settings.cors_origins)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    app.add_middleware(RequireLocalPeer, token=settings.api_token.get_secret_value())
    app.include_router(router)
    app.get("/api/health")(health)
    return app


def health() -> dict[str, str]:
    return {"status": "ok"}


app = create_app()
