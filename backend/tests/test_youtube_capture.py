"""Capture uses hand-made official API shapes until Claude saves real replies."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from urllib.parse import quote

import httpx
import pytest

from app.config import Settings

VIDEO_ID = "CbpsDMwFG2g"
KEY = "youtube-secret-key"
# hand-made, replace with capture: YouTube videos.list and commentThreads.list shapes.
VIDEOS = {
    "kind": "youtube#videoListResponse",
    "items": [{"id": VIDEO_ID, "snippet": {"description": "Example", "channelId": "author"}}],
}
COMMENTS = {
    "kind": "youtube#commentThreadListResponse",
    "items": [
        {
            "snippet": {
                "topLevelComment": {
                    "snippet": {"authorChannelId": {"value": "author"}, "textOriginal": "code"}
                }
            }
        }
    ],
}


@pytest.fixture
def capture_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    from scripts import capture_youtube_meta

    monkeypatch.setattr(capture_youtube_meta, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        capture_youtube_meta,
        "Settings",
        lambda: Settings(_env_file=None, providers=[], youtube_api_key=KEY),
    )
    return capture_youtube_meta


def mock_http(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[httpx.Request], httpx.Response],
) -> None:
    client_class = httpx.Client
    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(responder), **kwargs),
    )


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=4",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtube.com/shorts/{VIDEO_ID}",
    ],
)
def test_capture_preserves_raw_replies(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    url: str,
) -> None:
    calls: list[httpx.Request] = []
    video_bytes = json.dumps(VIDEOS, indent=2).encode() + b"\n"
    comment_bytes = json.dumps(COMMENTS, indent=2).encode() + b"\n"

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        logging.getLogger("httpx").info("Metadata request reached the transport")
        assert request.method == "GET"
        assert request.url.params["key"] == KEY
        assert request.url.params["part"] == "snippet"
        assert request.url.host == "www.googleapis.com"
        if request.url.path.endswith("/videos"):
            assert request.url.params["id"] == VIDEO_ID
            return httpx.Response(200, content=video_bytes)
        assert request.url.path.endswith("/commentThreads")
        assert request.url.params["videoId"] == VIDEO_ID
        assert request.url.params["maxResults"] == "50"
        assert request.url.params["order"] == "relevance"
        return httpx.Response(200, content=comment_bytes)

    mock_http(capture_module, monkeypatch, respond)
    caplog.set_level(logging.DEBUG)
    assert capture_module.main([url]) == 0
    assert len(calls) == 2
    assert (tmp_path / f"{VIDEO_ID}.videos.json").read_bytes() == video_bytes
    assert (tmp_path / f"{VIDEO_ID}.comments.json").read_bytes() == comment_bytes
    output = capsys.readouterr()
    assert KEY not in output.out + output.err + caplog.text
    assert "Metadata request reached the transport" in caplog.text
    assert all(KEY.encode() not in path.read_bytes() for path in tmp_path.iterdir())


@pytest.mark.parametrize("suffix", ["videos", "comments"])
def test_capture_refuses_overwrite_before_any_request(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
) -> None:
    existing = tmp_path / f"{VIDEO_ID}.{suffix}.json"
    existing.write_text("preserved", encoding="utf-8")
    mock_http(capture_module, monkeypatch, lambda _: pytest.fail("No request for existing capture"))
    assert capture_module.main([f"https://youtu.be/{VIDEO_ID}"]) == 1
    assert existing.read_text(encoding="utf-8") == "preserved"
    assert len(list(tmp_path.iterdir())) == 1


def test_capture_missing_key_and_bad_url_do_not_request(
    capture_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_http(capture_module, monkeypatch, lambda _: pytest.fail("No request without config"))
    monkeypatch.setattr(capture_module, "Settings", lambda: Settings(_env_file=None, providers=[]))
    assert capture_module.main([f"https://youtu.be/{VIDEO_ID}"]) == 2
    assert capture_module.main(["https://youtube.com/watch?v=short"]) == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["http", "transport", "invalid_json"])
def test_capture_failures_write_nothing_and_hide_key(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "transport":
            raise httpx.ConnectError(KEY, request=request)
        if failure == "http":
            return httpx.Response(403, json={"error": {"message": KEY}})
        return httpx.Response(200, text=KEY)

    mock_http(capture_module, monkeypatch, respond)
    caplog.set_level(logging.DEBUG)
    assert capture_module.main([f"https://youtu.be/{VIDEO_ID}"]) == 1
    assert list(tmp_path.iterdir()) == []
    output = capsys.readouterr()
    assert KEY not in output.out + output.err + caplog.text


@pytest.mark.parametrize("encoding", ["raw", "unicode", "url"])
def test_capture_refuses_replies_containing_the_key(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    encoding: str,
) -> None:
    key = "yt-secret+/more"
    monkeypatch.setattr(
        capture_module,
        "Settings",
        lambda: Settings(_env_file=None, providers=[], youtube_api_key=key),
    )
    echoed = key if encoding == "raw" else quote(key, safe="")
    body = json.dumps({"echo": echoed})
    if encoding == "unicode":
        body = json.dumps({"echo": key}).replace("yt", r"\u0079t")
    mock_http(capture_module, monkeypatch, lambda _: httpx.Response(200, text=body))
    caplog.set_level(logging.DEBUG)
    assert capture_module.main([f"https://youtu.be/{VIDEO_ID}"]) == 1
    assert list(tmp_path.iterdir()) == []
    output = capsys.readouterr()
    assert key not in output.out + output.err + caplog.text
    assert quote(key, safe="") not in output.out + output.err + caplog.text


def test_capture_records_comments_disabled_reply(
    capture_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disabled = {"error": {"errors": [{"reason": "commentsDisabled"}], "code": 403}}

    def respond(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=VIDEOS)
            if request.url.path.endswith("/videos")
            else httpx.Response(403, json=disabled)
        )

    mock_http(capture_module, monkeypatch, respond)
    assert capture_module.main([f"https://youtu.be/{VIDEO_ID}"]) == 0
    assert json.loads((tmp_path / f"{VIDEO_ID}.comments.json").read_text()) == disabled


def test_other_comment_failure_does_not_leave_partial_capture(
    capture_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=VIDEOS)
            if request.url.path.endswith("/videos")
            else httpx.Response(500, json={"error": {"message": KEY}})
        )

    mock_http(capture_module, monkeypatch, respond)
    assert capture_module.main([f"https://youtu.be/{VIDEO_ID}"]) == 1
    assert list(tmp_path.iterdir()) == []


def test_youtube_key_is_secret_and_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert Settings(_env_file=None, providers=[]).youtube_api_key.get_secret_value() == ""
    monkeypatch.setenv("YOUTUBE_API_KEY", KEY)
    config = Settings(_env_file=None, providers=[])
    assert config.youtube_api_key.get_secret_value() == KEY
    assert KEY not in repr(config)
