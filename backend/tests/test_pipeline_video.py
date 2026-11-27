"""Video extraction reaches sandbox decisions and the public event contract."""

import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import storage
from app.api import router
from app.broker import Broker
from app.config import Settings
from app.events import CheckEvent
from app.llm import LLMUnavailableError
from app.models import Environment, ErrorInfo, ExecResult, Label, ProbeResult, RepairProposal
from app.pipeline import PipelineDeps, run_check

WATCH_URL = "https://www.youtube.com/watch?v=CbpsDMwFG2g"
MISREAD = "This step may have been misread from the video."


@pytest.fixture(autouse=True)
def public_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])


class Sink:
    def __init__(self) -> None:
        self.events: list[CheckEvent] = []

    def emit(self, event: CheckEvent) -> None:
        self.events.append(event)


class PrivateCache:
    def get(self, key: str) -> str | None:
        pytest.fail("A video check must not read the shared cache")

    def put(self, key: str, library: str, version: str | None, source: str, content: str) -> None:
        pytest.fail("A video check must not write the shared cache")


class UnavailableProposer:
    def propose(self, messages: list[dict[str, str]]) -> tuple[RepairProposal, str]:
        raise LLMUnavailableError(["test provider unavailable"])


class FakeSandbox:
    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.codes: list[str] = []
        self.closed = False

    def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
        return Environment(versions={package: "1.0" for package in packages})

    def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
        self.codes.append(code)
        return ExecResult(
            id=step_id,
            status="error" if self.broken else "ok",
            error=ErrorInfo(type="ValueError", message="bad tutorial value")
            if self.broken
            else None,
        )

    def probe(self, probe_id: str, target: str, name: str) -> ProbeResult:
        return ProbeResult(id=probe_id, status="ok", target_found=False)

    def close(self) -> None:
        self.closed = True


def settings(**overrides: object) -> Settings:
    overrides.setdefault("video_reads", 1)
    return Settings(
        _env_file=None,
        providers=[],
        video_reader_base_url="https://video.test/v1",
        video_reader_api_key="configured-video-secret",
        video_reader_model="test-model",
        video_max_minutes=1,
        **overrides,
    )


def reply(code: str = "x = 1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "blocks": [
                                        {
                                            "start_seconds": 12,
                                            "kind": "python",
                                            "code": code,
                                            "partial": False,
                                        },
                                        {
                                            "start_seconds": 14,
                                            "kind": "file",
                                            "code": 'MY_TOKEN="visible-secret"',
                                            "partial": False,
                                        },
                                    ]
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    )


def run_video(
    respond: Callable[[httpx.Request], httpx.Response],
    *,
    broken: bool = False,
    config: Settings | None = None,
) -> tuple[Sink, FakeSandbox]:
    sink, sandbox = Sink(), FakeSandbox(broken)

    def with_oembed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oembed":
            return httpx.Response(404)
        return respond(request)

    with httpx.Client(transport=httpx.MockTransport(with_oembed)) as http:
        deps = PipelineDeps(
            config or settings(),
            lambda _: sandbox,
            UnavailableProposer(),
            http,
            PrivateCache(),
        )
        run_check("video-check", "https://youtu.be/CbpsDMwFG2g?t=9", deps, sink)
    return sink, sandbox


def test_video_pipeline_routes_without_downloading_and_copies_metadata() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://video.test/v1/interactions"
        assert json.loads(request.content)["input"][1]["uri"] == WATCH_URL
        return reply()

    sink, sandbox = run_video(respond)
    assert len(requests) == 1
    assert sink.events[-1].type == "done"
    assert sandbox.codes == ["x = 1"]
    assert sandbox.closed
    extracted = sink.events[0]
    assert extracted.type == "extracted"
    assert [block.video_timestamp for block in extracted.blocks] == [12, 14]
    assert all(block.low_confidence for block in extracted.blocks)
    finals = [event.step for event in sink.events if event.type == "step_final"]
    assert [step.video_timestamp for step in finals] == [12, 14]
    assert all(step.low_confidence for step in finals)
    assert finals[0].label == Label.WORKS
    assert finals[1].label is None
    assert "visible-secret" not in "".join(event.model_dump_json() for event in sink.events)


def test_video_broken_note_preserves_reason_when_repairs_unavailable() -> None:
    sink, _ = run_video(lambda _: reply("int('bad')"), broken=True)
    result = next(event for event in sink.events if event.type == "step_result")
    final = next(event.step for event in sink.events if event.type == "step_final")
    assert result.label == final.label == Label.BROKEN
    assert result.reason.endswith(MISREAD)
    assert "bad tutorial value" in result.reason
    assert "bad tutorial value" in final.reason
    assert "every LLM provider was unavailable" in final.reason
    assert final.reason.endswith(MISREAD)
    assert final.reason.count(MISREAD) == 1


def test_video_repair_cache_is_separate_for_each_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repair.loop import RepairContext, RepairOutcome
    from app.step import Step

    caches = []

    def repair(
        session: object, step: Step, context: RepairContext, proposer: object, **kwargs: object
    ) -> RepairOutcome:
        assert context.doc_cache.get("video-derived-query") is None
        context.doc_cache.put("video-derived-query", "test", None, "tavily", "private evidence")
        assert context.doc_cache.get("video-derived-query") == "private evidence"
        caches.append(context.doc_cache)
        return RepairOutcome(None)

    monkeypatch.setattr("app.pipeline.repair_step", repair)
    for _ in range(2):
        sink, _ = run_video(lambda _: reply("int('bad')"), broken=True)
        assert sink.events[-1].type == "done"
    assert caches[0] is not caches[1]


def test_video_429_wait_event_precedes_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.extract import video

    monkeypatch.setattr(video.time, "sleep", lambda _: None)
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return reply()

    sink, _ = run_video(respond)
    assert calls == 2
    notice = sink.events[0]
    assert notice.type == "rate_limit_wait"
    assert (notice.provider, notice.seconds) == ("video", 2)
    assert sink.events[1].type == "extracted"
    assert sink.events[-1].type == "done"


def test_video_pacing_is_shared_across_checks() -> None:
    from app.extract.video import VideoReader

    now = 0.0
    sent: list[float] = []

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oembed":
            return httpx.Response(404)
        sent.append(now)
        return reply()

    config = settings(video_reader_min_interval_seconds=3)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        deps = PipelineDeps(
            config, lambda _: FakeSandbox(), UnavailableProposer(), http, PrivateCache()
        )
        deps.video_reader = VideoReader(config, http, sleep=sleep, monotonic=lambda: now)
        sinks = [Sink(), Sink()]
        for idx, sink in enumerate(sinks):
            run_check(f"video-{idx}", WATCH_URL, deps, sink)
    assert sent == [0, 3]
    assert sinks[0].events[0].type == "extracted"
    assert sinks[1].events[0].type == "rate_limit_wait"
    assert sinks[1].events[0].seconds == 3
    assert all(sink.events[-1].type == "done" for sink in sinks)


def test_video_missing_config_is_failed_event() -> None:
    config = Settings(_env_file=None, providers=[])
    sink, sandbox = run_video(lambda _: pytest.fail("No request without config"), config=config)
    assert len(sink.events) == 1
    assert sink.events[0].type == "failed"
    assert sink.events[0].error == (
        "Reading videos needs VIDEO_READER_BASE_URL, VIDEO_READER_API_KEY and "
        "VIDEO_READER_MODEL in backend/.env."
    )
    assert not sandbox.codes


def test_video_key_is_redacted_from_unexpected_pipeline_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("configured-video-secret")

    monkeypatch.setattr("app.pipeline.VideoReader.extract", fail)
    caplog.set_level(logging.ERROR)
    sink, _ = run_video(lambda _: pytest.fail("Unexpected request"))
    assert sink.events[-1].type == "failed"
    assert "configured-video-secret" not in caplog.text
    assert "configured-video-secret" not in sink.events[-1].model_dump_json()


def test_openapi_carries_video_metadata() -> None:
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    step = schema["components"]["schemas"]["StepReport"]["properties"]
    assert step["low_confidence"]["default"] is False
    assert step["video_timestamp"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]


@pytest.mark.parametrize(
    "url,is_video",
    [
        ("https://youtu.be/CbpsDMwFG2g?t=9", True),
        ("https://youtube.com/shorts/CbpsDMwFG2g?si=tracking", True),
        ("https://youtube.com/watch?v=CbpsDMwFG2g&t=9", True),
        ("https://tutorial.test/example?version=2", False),
    ],
)
def test_api_recorder_persists_video_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, is_video: bool
) -> None:
    class InlineExecutor:
        def submit(self, function: Callable[..., None], *args: object) -> None:
            function(*args)

    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    db_path = str(tmp_path / "video-check.db")
    storage.init_db(db_path)
    config = settings(database_path=db_path)
    sandbox = FakeSandbox()
    app = FastAPI()
    app.include_router(router)
    app.state.settings = config
    app.state.broker = Broker()
    app.state.executor = InlineExecutor()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: reply() if is_video else httpx.Response(200, text="<pre>x = 1</pre>")
        )
    ) as http:
        app.state.deps = PipelineDeps(
            config, lambda _: sandbox, UnavailableProposer(), http, PrivateCache()
        )
        with TestClient(app) as client:
            submitted = client.post("/api/checks", json={"url": url})
            assert submitted.status_code == 200
            check_id = submitted.json()["check_id"]
            response = client.get(f"/api/checks/{check_id}")
            assert response.status_code == 200
            report = response.json()
            assert report["status"] == "done"
            assert report["source"] == (WATCH_URL if is_video else url)
            assert [step["video_timestamp"] for step in report["steps"]] == (
                [12, 14] if is_video else [None]
            )
            assert all(step["low_confidence"] is is_video for step in report["steps"])
            log = client.get(f"/api/checks/{check_id}/events/log").json()
            assert log[-1]["event"]["type"] == "done"
            assert [
                event["event"]["step"] for event in log if event["event"]["type"] == "step_final"
            ] == report["steps"]
    reloaded = storage.get_report(db_path, check_id)
    assert reloaded is not None
    assert reloaded.source == (WATCH_URL if is_video else url)
    assert reloaded.steps[0].video_timestamp == (12 if is_video else None)
    assert reloaded.steps[0].low_confidence is is_video
    assert sandbox.codes == ["x = 1"]
    assert sandbox.closed


def test_video_broken_note_names_how_many_reads_saw_a_misread_step() -> None:
    codes = iter(["int('bad')", "int('bad2')"])
    sink, _ = run_video(lambda _: reply(next(codes)), broken=True, config=settings(video_reads=2))
    extracted = sink.events[0]
    assert extracted.blocks[0].read_agreement == 1
    finals = [event.step for event in sink.events if event.type == "step_final"]
    python = finals[0]
    assert python.read_agreement == 1
    assert python.reason.endswith(
        "This step may have been misread from the video (only 1 of 2 reads saw it)."
    )
    assert "1 of 2" in python.reason


def test_video_broken_note_is_unchanged_when_every_read_agreed() -> None:
    sink, _ = run_video(lambda _: reply("int('bad')"), broken=True, config=settings(video_reads=2))
    final = next(event.step for event in sink.events if event.type == "step_final")
    assert final.read_agreement == 2
    assert final.reason.endswith(MISREAD)
