"""Description-link checks use hand-made replies and MockTransport only."""

import json
import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.extract.youtube_links import candidate_links, extract_youtube_links, fetch_metadata

WATCH = "https://www.youtube.com/watch?v=CbpsDMwFG2g"
NOTEBOOK = "https://github.com/author/tutorial/blob/main/demo.ipynb"
RAW = "https://raw.githubusercontent.com/author/tutorial/main/demo.ipynb"
KEY = "youtube-key-with+special/chars"


def videos(description: str) -> dict[str, Any]:
    # hand-made, replace with capture
    return {"items": [{"snippet": {"description": description, "channelId": "author"}}]}


def comments(*entries: tuple[str, str]) -> dict[str, Any]:
    # hand-made, replace with capture
    return {
        "items": [
            {
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "authorChannelId": {"value": author},
                            "textDisplay": text,
                        }
                    }
                }
            }
            for author, text in entries
        ]
    }


def notebook() -> bytes:
    return json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "id": "linked-code",
                    "cell_type": "code",
                    "source": "x = 1",
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                }
            ],
        }
    ).encode()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    return Settings(_env_file=None, providers=[], youtube_api_key=KEY)


def test_description_notebook_uses_normal_extraction_and_exact_steps(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/youtube/v3/videos":
            assert request.url.params["key"] == KEY
            assert request.url.params["part"] == "snippet"
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path == "/youtube/v3/commentThreads":
            assert request.url.params["maxResults"] == "50"
            assert request.url.params["order"] == "relevance"
            return httpx.Response(200, json=comments())
        assert str(request.url) == RAW
        return httpx.Response(200, content=notebook())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_youtube_links("https://youtu.be/CbpsDMwFG2g", settings, http)
    assert result.extraction is not None
    assert result.extraction.source == WATCH
    assert result.extraction.blocks[0].runnable_code == "x = 1"
    assert not result.extraction.blocks[0].low_confidence
    assert result.extraction.blocks[0].video_timestamp is None
    assert len(requests) == 3


def test_only_authors_comments_supply_links(settings: Settings) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos("No links"))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(
                200,
                json=comments(
                    ("stranger", "https://evil.test/other.ipynb"),
                    ("author", f'<a href="{NOTEBOOK}">Notebook</a>'),
                ),
            )
        assert str(request.url) == RAW
        return httpx.Response(200, content=notebook())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_youtube_links(WATCH, settings, http)
    assert result.extraction is not None
    assert not any("evil.test" in url for url in requests)


def test_comments_disabled_preserves_description(settings: Settings) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(403, json={"error": {"errors": [{"reason": "commentsDisabled"}]}})
        return httpx.Response(200, content=notebook())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is not None


def test_no_key_makes_no_requests(settings: Settings) -> None:
    settings.youtube_api_key = SecretStr("")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No request without a key"))
    ) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None


@pytest.mark.parametrize("status", [400, 401, 403, 500, 302])
def test_api_errors_silently_skip_source_one(settings: Settings, status: int) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text=KEY))
    ) as http:
        result = extract_youtube_links(WATCH, settings, http)
    assert result.extraction is None
    assert not result.login_blocked


@pytest.mark.parametrize(
    "target", ["https://accounts.google.com/o/oauth2/auth", "/LOGIN", "/sign_in", "/signin"]
)
def test_login_redirect_is_blocked_before_following(settings: Settings, target: str) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        assert str(request.url) == RAW
        return httpx.Response(302, headers={"Location": target})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_youtube_links(WATCH, settings, http)
    assert result.extraction is None and result.login_blocked
    assert len(requests) == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(200, text='<form><input type="PASSWORD"></form>'),
    ],
)
def test_login_response_is_recorded_and_next_link_is_tried(
    settings: Settings, response: httpx.Response
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(f"{NOTEBOOK} https://code.test/demo.ipynb"))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        if str(request.url) == RAW:
            return response
        assert str(request.url) == "https://code.test/demo.ipynb"
        return httpx.Response(200, content=notebook())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_youtube_links(WATCH, settings, http)
    assert result.extraction is not None and result.login_blocked


def test_links_filter_rewrite_deduplicate_and_limit() -> None:
    texts = [
        "https://bit.ly/thing.ipynb https://x.com/demo.ipynb https://youtu.be/CbpsDMwFG2g "
        "https://colab.research.google.com/github/author/tutorial/blob/main/demo.ipynb). "
        f"{NOTEBOOK}, https://nbviewer.org/github/author/tutorial/blob/main/other.ipynb "
        "https://gist.github.com/author/12345 "
        "https://github.com/author/tutorial https://code.test/final.ipynb https://code.test/extra.ipynb"
    ]
    assert candidate_links(texts) == [
        NOTEBOOK,
        "https://github.com/author/tutorial/blob/main/other.ipynb",
        "https://gist.github.com/author/12345",
        "https://github.com/author/tutorial",
        "https://code.test/final.ipynb",
    ]


def test_shortener_redirect_is_not_followed(settings: Settings) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        assert str(request.url) == RAW
        return httpx.Response(302, headers={"Location": "https://bit.ly/target"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None
    assert calls == 3


def test_metadata_query_key_never_appears_in_debug_logs(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text=KEY))
    ) as http:
        assert fetch_metadata(WATCH, KEY, http) is None
    assert KEY not in caplog.text
    assert "youtube-key-with%2Bspecial%2Fchars" not in caplog.text


def test_at_most_five_links_are_fetched(settings: Settings) -> None:
    fetched: list[str] = []
    links = [f"https://code.test/{index}.ipynb" for index in range(6)]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(" ".join(links)))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        fetched.append(str(request.url))
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None
    assert fetched == links[:5]


@pytest.mark.parametrize("redirect", [False, True])
def test_existing_ssrf_guard_blocks_private_link_targets(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, redirect: bool
) -> None:
    monkeypatch.setattr(
        "app.extract.fetch.resolve_host",
        lambda host: ["127.0.0.1"] if host == "private.test" else ["93.184.216.34"],
    )
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/videos"):
            link = NOTEBOOK if redirect else "https://private.test/demo.ipynb"
            return httpx.Response(200, json=videos(link))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        assert redirect and str(request.url) == RAW
        return httpx.Response(302, headers={"Location": "https://private.test/demo.ipynb"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None
    assert calls == (3 if redirect else 2)


def test_linked_fetches_enforce_size_limits(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.extract.youtube_links.MAX_DOWNLOAD_BYTES", 10)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        return httpx.Response(200, content=notebook())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None


def test_linked_fetches_never_send_client_credentials_or_cookies(settings: Settings) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, content=notebook())

    with httpx.Client(
        transport=httpx.MockTransport(respond),
        auth=("private", "credential"),
        cookies={"session": "private-session"},
    ) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is not None


def test_other_comment_api_failures_skip_description_links(settings: Settings) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        assert request.url.path.endswith("/commentThreads")
        return httpx.Response(500, json={"error": {}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None
    assert calls == 2


def test_unicode_escaped_key_echo_in_metadata_is_rejected(settings: Settings) -> None:
    body = json.dumps(videos(KEY))
    escaped = "".join(f"\\u{ord(character):04x}" for character in KEY)
    body = body.replace(KEY, escaped)
    assert KEY not in body
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as http:
        assert fetch_metadata(WATCH, KEY, http) is None


@pytest.mark.parametrize("encoded", [False, True])
def test_linked_code_echoing_configured_key_is_not_returned(
    settings: Settings, encoded: bool
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        data = json.loads(notebook())
        value = quote(KEY, safe="") if encoded else KEY
        data["cells"][0]["source"] = f"secret = {value!r}"
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None


@pytest.mark.parametrize("form_encoded", [False, True])
def test_url_encoded_key_echo_in_metadata_is_rejected(form_encoded: bool) -> None:
    key = "youtube key+with/slash"
    encoded = quote_plus(key, safe="") if form_encoded else quote(key, safe="")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=videos(encoded)))
    ) as http:
        assert fetch_metadata(WATCH, key, http) is None


def test_missing_author_channel_does_not_admit_other_comments(settings: Settings) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            data = videos("")
            data["items"][0]["snippet"]["channelId"] = None
            return httpx.Response(200, json=data)
        assert request.url.path.endswith("/commentThreads")
        data = comments(("stranger", NOTEBOOK))
        del data["items"][0]["snippet"]["topLevelComment"]["snippet"]["authorChannelId"]
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None


def test_misnamed_video_is_rejected_before_reading_body(settings: Settings) -> None:
    class VideoBody(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            pytest.fail("Video bytes must never be read")
            yield b""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=videos(NOTEBOOK))
        if request.url.path.endswith("/commentThreads"):
            return httpx.Response(200, json=comments())
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, stream=VideoBody())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert extract_youtube_links(WATCH, settings, http).extraction is None
