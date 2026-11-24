"""HTTP submission, persisted reports and SSE replay."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

import nbformat
from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import storage
from app.events import StoredEvent
from app.extract import ExtractionError, PastedCode, check_url
from app.extract.paste import PASTE_SOURCE
from app.extract.routing import route_url
from app.models import CheckReport, CheckStatus, SourceKind
from app.pipeline import run_check
from app.recorder import RecorderSink

router = APIRouter(prefix="/api")


class URLSubmission(BaseModel):
    url: str


MAX_PASTE_CHARS = 200_000


class CodeSubmission(BaseModel):
    code: str
    title: str | None = None


class CheckCreated(BaseModel):
    check_id: str


def _report(request: Request, check_id: str) -> CheckReport:
    report = storage.get_report(request.app.state.settings.database_path, check_id)
    if report is None:
        raise HTTPException(404, "Check not found")
    return report


def _submit(request: Request, source: str | tuple[str, bytes] | PastedCode) -> CheckCreated:
    if isinstance(source, str):
        route = route_url(source)
        if route.kind == "video":
            source = route.fetch_urls[0]
    if isinstance(source, PastedCode):
        source_kind, shown = SourceKind.PASTE, PASTE_SOURCE
    elif isinstance(source, str):
        source_kind, shown = SourceKind.URL, source
    else:
        source_kind, shown = SourceKind.NOTEBOOK, source[0]
    check_id = uuid4().hex
    state = request.app.state
    storage.create_check(
        state.settings.database_path,
        CheckReport(
            id=check_id,
            source_kind=source_kind,
            source=shown,
            status=CheckStatus.RUNNING,
        ),
    )
    sink = RecorderSink(state.settings.database_path, check_id, state.broker)
    try:
        state.executor.submit(run_check, check_id, source, state.deps, sink)
    except RuntimeError:
        from app.events import FailedEvent

        sink.emit(FailedEvent(error="Check worker is unavailable"))
        raise HTTPException(503, "Check worker is unavailable") from None
    return CheckCreated(check_id=check_id)


@router.post("/checks", response_model=CheckCreated)
def submit_url(body: URLSubmission, request: Request) -> CheckCreated:
    try:
        check_url(body.url)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from None
    return _submit(request, body.url)


@router.post("/checks/code", response_model=CheckCreated)
def submit_code(body: CodeSubmission, request: Request) -> CheckCreated:
    if not body.code.strip():
        raise HTTPException(422, "Paste some code to check")
    if len(body.code) > MAX_PASTE_CHARS:
        raise HTTPException(422, "The pasted text is over the 200,000 character limit")
    return _submit(request, PastedCode(body.code, body.title))


@router.post("/checks/notebook", response_model=CheckCreated)
async def submit_notebook(request: Request, file: Annotated[UploadFile, File()]) -> CheckCreated:
    try:
        if not file.filename or not file.filename.lower().endswith(".ipynb"):
            raise HTTPException(422, "Upload must be an .ipynb file")
        limit = request.app.state.settings.max_upload_mb * 1024 * 1024
        data = bytearray()
        while chunk := await file.read(65536):
            if len(data) + len(chunk) > limit:
                raise HTTPException(422, "Notebook exceeds the upload size limit")
            data.extend(chunk)
        try:
            notebook = nbformat.reads(data.decode("utf-8-sig"), as_version=4)
            nbformat.validate(notebook)
        except Exception:
            raise HTTPException(422, "Upload must contain a valid Jupyter notebook") from None
        return _submit(request, (file.filename, bytes(data)))
    finally:
        await file.close()


@router.get("/checks", response_model=list[CheckReport])
def recent_checks(request: Request) -> list[CheckReport]:
    return storage.list_recent(request.app.state.settings.database_path)


@router.get("/checks/{check_id}", response_model=CheckReport)
def get_check(check_id: str, request: Request) -> CheckReport:
    return _report(request, check_id)


def _stored(request: Request, check_id: str, after: int = 0) -> list[StoredEvent]:
    return [
        StoredEvent(seq=seq, check_id=check_id, event=json.loads(payload))
        for seq, _, payload in storage.list_events(
            request.app.state.settings.database_path, check_id, after
        )
    ]


@router.get("/checks/{check_id}/events/log", response_model=list[StoredEvent])
def events_log(check_id: str, request: Request) -> list[StoredEvent]:
    _report(request, check_id)
    return _stored(request, check_id)


def _sse(event: StoredEvent) -> str:
    return f"id: {event.seq}\nevent: {event.event.type}\ndata: {event.event.model_dump_json()}\n\n"


@router.get("/checks/{check_id}/events")
async def events(
    check_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    _report(request, check_id)
    try:
        after = int(last_event_id) if last_event_id is not None else 0
        if after < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "Last-Event-ID must be a non-negative integer") from None

    async def stream() -> AsyncIterator[str]:
        cursor = after
        subscription = request.app.state.broker.subscribe(check_id)
        try:
            for stored in _stored(request, check_id, cursor):
                cursor = stored.seq
                yield _sse(stored)
                if stored.event.type in ("done", "failed"):
                    return
            if _report(request, check_id).status != CheckStatus.RUNNING:
                # Completion can commit between the first replay query and this status read.
                for stored in _stored(request, check_id, cursor):
                    cursor = stored.seq
                    yield _sse(stored)
                return
            while not await request.is_disconnected():
                try:
                    stored = await asyncio.wait_for(anext(subscription), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:
                    return
                if stored.seq > cursor:
                    cursor = stored.seq
                    yield _sse(stored)
                if stored.event.type in ("done", "failed"):
                    return
        finally:
            await subscription.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
