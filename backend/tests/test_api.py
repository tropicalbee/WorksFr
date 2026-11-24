import tempfile
from concurrent.futures import Future
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from support.local_sandbox import LocalRunnerSandbox
from test_pipeline import Cache, Proposer, notebook

from app.config import Settings
from app.events import DoneEvent, StepStartedEvent
from app.main import create_app
from app.models import CheckReport, CheckStatus, Score, SourceKind
from app.pipeline import PipelineDeps
from app.recorder import RecorderSink
from app.storage import create_check

ROOT = Path(__file__).resolve().parents[2]


class InlineExecutor:
    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(
        "app.extract.fetch.resolve_host",
        lambda host: ["93.184.216.34"] if host == "public.test" else ["127.0.0.1"],
    )
    settings = Settings(
        _env_file=None, providers=[], database_path=str(tmp_path / "checks.db"), max_upload_mb=1
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<pre>x = 1</pre>"))
    ) as http:
        app = create_app(settings)
        with TestClient(app) as client:
            app.state.executor.shutdown(wait=False)
            app.state.executor = InlineExecutor()
            app.state.deps = PipelineDeps(
                settings,
                lambda check_id: LocalRunnerSandbox(
                    settings, runner_path=ROOT / "sandbox/runner.py"
                ),
                Proposer(),
                http,
                Cache(),
            )
            yield client


def test_post_report_and_replay_log(client: TestClient) -> None:
    response = client.post("/api/checks/notebook", files={"file": ("test.ipynb", notebook())})
    assert response.status_code == 200
    check_id = response.json()["check_id"]
    report = client.get(f"/api/checks/{check_id}")
    assert report.json()["status"] == "done"
    assert len(report.json()["steps"]) == 5
    log = client.get(f"/api/checks/{check_id}/events/log").json()
    assert log[0]["event"]["type"] == "extracted"
    assert log[-1]["event"]["type"] == "done"
    stream = client.get(f"/api/checks/{check_id}/events")
    assert "event: done\n" in stream.text
    assert stream.text.count("\ndata:") == len(log)
    replay = client.get(f"/api/checks/{check_id}/events", headers={"Last-Event-ID": "1"})
    assert "id: 1\n" not in replay.text
    assert client.get("/api/checks").json()[0]["steps"] == []
    assert (
        client.post("/api/checks", json={"url": "https://public.test/tutorial"}).status_code == 200
    )
    assert client.get("/api/health").json() == {"status": "ok"}


def test_rejections_and_not_found(client: TestClient) -> None:
    assert client.post("/api/checks", json={"url": "http://127.0.0.1"}).status_code == 422
    for filename, data in (
        ("wrong.txt", b"{}"),
        ("bad.ipynb", b"invalid"),
        ("big.ipynb", b"x" * (1024 * 1024 + 1)),
        ("bad.ipynb", b"{}"),
    ):
        assert (
            client.post("/api/checks/notebook", files={"file": (filename, data)}).status_code == 422
        )
    assert client.get("/api/checks/missing").status_code == 404
    assert client.get("/api/checks/missing/events").status_code == 404


def test_download_route_is_removed(client: TestClient) -> None:
    create_check(
        client.app.state.settings.database_path,
        CheckReport(
            id="removed-download",
            source_kind=SourceKind.URL,
            source="https://public.test",
            status=CheckStatus.RUNNING,
        ),
    )
    assert client.get("/api/checks/removed-download").status_code == 200
    assert client.get("/api/checks/removed-download/download").status_code == 404
    assert not any("download" in path for path in client.get("/openapi.json").json()["paths"])


def test_sse_completion_during_replay_is_not_lost(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api as api

    create_check(
        client.app.state.settings.database_path,
        CheckReport(
            id="live",
            source_kind=SourceKind.URL,
            source="https://public.test",
            status=CheckStatus.RUNNING,
        ),
    )
    sink = RecorderSink(client.app.state.settings.database_path, "live", client.app.state.broker)
    sink.emit(StepStartedEvent(idx=0))
    original = api._stored
    finished = False

    def concurrent_completion(request, check_id, after=0):
        nonlocal finished
        rows = original(request, check_id, after)
        if not finished:
            finished = True
            sink.emit(DoneEvent(score=Score()))
        return rows

    monkeypatch.setattr(api, "_stored", concurrent_completion)
    response = client.get("/api/checks/live/events")
    assert response.text.count("event: step_started") == 1
    assert response.text.count("event: done") == 1
    assert (
        client.get("/api/checks/live/events", headers={"Last-Event-ID": "bad"}).status_code == 422
    )
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert all(name in schemas for name in ("StoredEvent", "DoneEvent", "RepairAttemptEvent"))


def test_paste_route_returns_a_check_id(client: TestClient) -> None:
    response = client.post(
        "/api/checks/code", json={"code": "x = 1\n\nprint(x)\n", "title": "My paste"}
    )
    assert response.status_code == 200
    check_id = response.json()["check_id"]
    report = client.get(f"/api/checks/{check_id}").json()
    assert report["source_kind"] == "paste"
    assert report["source"] == "Pasted code"
    assert report["title"] == "My paste"
    assert report["status"] == "done"
    assert len(report["steps"]) == 2
    assert client.post("/api/checks/code", json={"code": "x = 1"}).status_code == 200


def test_paste_route_rejects_empty_and_oversized_code(client: TestClient) -> None:
    for code in ("", "  \n\t", "x" * 200_001):
        response = client.post("/api/checks/code", json={"code": code})
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)
    assert client.post("/api/checks/code", json={"code": "x" * 200_000}).status_code == 200
