"""Source links use exact code and retain login notices in persisted reports."""

import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import nbformat
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_pipeline_video import FakeSandbox, PrivateCache, Sink, UnavailableProposer

from app import storage
from app.api import router
from app.broker import Broker
from app.config import Settings
from app.models import CheckStatus
from app.pipeline import PipelineDeps, run_check

WATCH = "https://www.youtube.com/watch?v=CbpsDMwFG2g"
NOTEBOOK = "https://github.com/author/tutorial/blob/main/demo.ipynb"
LOGIN_NOTE = "The linked code is behind a login"
KEY = "youtube-test-secret"


def config(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "providers": [],
        "youtube_api_key": KEY,
        "video_reader_base_url": "https://video.test/v1",
        "video_reader_api_key": "reader-test-secret",
        "video_reader_model": "test-model",
        "video_max_minutes": 1,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def metadata(description: str) -> dict[str, object]:
    return {
        "items": [
            {"snippet": {"description": description, "channelId": "author", "title": "Video"}}
        ]
    }


def notebook() -> bytes:
    return nbformat.writes(
        nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("x = 1")])
    ).encode()


def video_reply() -> httpx.Response:
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
                                            "code": "x = 2",
                                            "partial": False,
                                        }
                                    ]
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    )


@pytest.fixture(autouse=True)
def public_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])


def test_description_notebook_avoids_video_reader(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=metadata(f"Code: {NOTEBOOK}."))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json={"items": []})  # hand-made, replace with capture
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, content=notebook())
        pytest.fail(f"Unexpected request to {request.url.host}")

    sink, sandbox = Sink(), FakeSandbox()
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        run_check(
            "links",
            WATCH,
            PipelineDeps(config(), lambda _: sandbox, UnavailableProposer(), http, PrivateCache()),
            sink,
        )
    assert sink.events[-1].type == "done"
    assert sandbox.codes == ["x = 1"]
    assert sink.events[0].type == "extracted"
    assert sink.events[0].title == "Video"
    steps = [event.step for event in sink.events if event.type == "step_final"]
    assert all(not step.low_confidence and step.video_timestamp is None for step in steps)
    assert all(request.url.host != "video.test" for request in requests)
    assert KEY not in caplog.text + "".join(event.model_dump_json() for event in sink.events)


@pytest.mark.parametrize("fallback_fails", [False, True])
def test_login_notice_survives_recorder_reload(tmp_path: Path, fallback_fails: bool) -> None:
    path = str(tmp_path / "linked-check.db")
    settings = config(database_path=path)
    storage.init_db(path)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=metadata(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json={"items": []})  # hand-made, replace with capture
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(302, headers={"Location": "https://accounts.google.com/login"})
        if request.url.host == "video.test":
            return (
                httpx.Response(429, headers={"Retry-After": "100"})
                if fallback_fails
                else video_reply()
            )
        pytest.fail(f"Login destination must not be followed: {request.url.host}")

    class InlineExecutor:
        def submit(self, function: Callable[..., None], *args: object) -> None:
            function(*args)

    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.broker = Broker()
    app.state.executor = InlineExecutor()
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        app.state.deps = PipelineDeps(
            settings, lambda _: FakeSandbox(), UnavailableProposer(), http, PrivateCache()
        )
        with TestClient(app) as client:
            submitted = client.post("/api/checks", json={"url": WATCH})
            assert submitted.status_code == 200
            check_id = submitted.json()["check_id"]
            response = client.get(f"/api/checks/{check_id}")
            assert response.status_code == 200
    loaded = storage.get_report(path, check_id)
    assert loaded is not None
    assert loaded.source == WATCH
    assert LOGIN_NOTE in loaded.model_dump_json()
    assert all(request.url.host != "accounts.google.com" for request in requests)
    assert any(request.url.host == "video.test" for request in requests)
    if fallback_fails:
        assert loaded.status == CheckStatus.FAILED
        assert "quota" in loaded.error
    else:
        assert loaded.status == CheckStatus.DONE
        assert loaded.steps[0].low_confidence
        assert loaded.steps[0].warnings[-1].category == "SourceWarning"


def test_youtube_key_redacted_from_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(KEY)

    monkeypatch.setattr("app.pipeline.VideoReader.extract", fail)
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as http:
        run_check(
            "secret",
            WATCH,
            PipelineDeps(
                config(), lambda _: FakeSandbox(), UnavailableProposer(), http, PrivateCache()
            ),
            sink,
        )
    assert sink.events[-1].type == "failed"
    assert KEY not in caplog.text + sink.events[-1].model_dump_json()


def test_transcribed_code_does_not_expose_youtube_key() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            return httpx.Response(500)
        response = video_reply()
        data = response.json()
        text = data["steps"][0]["content"][0]
        text["text"] = json.dumps(
            {
                "blocks": [
                    {
                        "start_seconds": 0,
                        "kind": "python",
                        "code": f'credential = "{KEY}"',
                        "partial": False,
                    }
                ]
            }
        )
        return httpx.Response(200, json=data)

    sink, sandbox = Sink(), FakeSandbox()
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        run_check(
            "secret-code",
            WATCH,
            PipelineDeps(config(), lambda _: sandbox, UnavailableProposer(), http, PrivateCache()),
            sink,
        )
    assert sink.events[-1].type == "done"
    assert KEY not in "".join(event.model_dump_json() for event in sink.events)
    assert KEY not in "".join(sandbox.codes)
