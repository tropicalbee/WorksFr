from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.broker import Broker
from app.config import Settings, get_settings
from app.llm import LLMClient
from app.pipeline import PipelineDeps
from app.repair.loop import LLMProposer
from app.sandbox.docker_sandbox import DockerSandbox
from app.storage import SqliteDocCache, init_db


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(settings.database_path)
        executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_checks)
        http = httpx.Client(timeout=30)
        llm = LLMClient(settings)
        app.state.settings = settings
        app.state.executor = executor
        app.state.broker = Broker()
        app.state.deps = PipelineDeps(
            settings,
            lambda check_id: DockerSandbox(settings, check_id),
            LLMProposer(llm),
            http,
            SqliteDocCache(settings.database_path),
        )
        try:
            yield
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            llm.close()
            http.close()

    app = FastAPI(title="WorksFr", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.get("/api/health")(health)
    return app


def health() -> dict[str, str]:
    return {"status": "ok"}


app = create_app()
