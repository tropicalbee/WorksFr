"""Video transcription checks use saved replies and mocked HTTP, never live videos."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.extract.errors import ExtractionError
from app.extract.video import (
    VideoBlock,
    VideoReader,
    deduplicate,
    extract_video,
    merge_reads,
    parse_reply,
    redact_code,
)
from app.models import SourceFormat, SourceKind, StepKind
from scripts.capture_video_reply import PROMPT

URL = "https://www.youtube.com/watch?v=CbpsDMwFG2g"
KEY = "reader-configured-secret"
FIXTURES = Path(__file__).parent / "fixtures" / "video"
MISSING = (
    "Reading videos needs VIDEO_READER_BASE_URL, VIDEO_READER_API_KEY and "
    "VIDEO_READER_MODEL in backend/.env."
)
PRIVATE = (
    "This video isn't public, so it can't be read. Upload the notebook or paste the code instead."
)
NO_CODE = "No code was visible in this video."
QUOTA = (
    "Gemini, which reads the video, has used up its free quota (tried: {models}). "
    "This is a limit on Google's side, not a problem with your link. It resets daily; "
    "try again later, or check a written tutorial."
)
OVERLOADED = (
    "Gemini, which reads the video, is overloaded right now (HTTP 503; tried: {models}). "
    "This is on Google's side, not a problem with your link. Try again in a few minutes."
)
PRIMARY_MODEL = "reader-primary"
FALLBACK_MODEL = "reader-fallback"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(
        _env_file=None,
        providers=[],
        video_reader_base_url="https://reader.test/v1beta/",
        video_reader_api_key=KEY,
        video_reader_model="reader-test-model",
        video_chunk_seconds=300,
        video_max_minutes=15,
        video_reads=1,
    )


@pytest.fixture
def fallback_settings(settings: Settings) -> Settings:
    settings.video_reader_model = f"{PRIMARY_MODEL},{FALLBACK_MODEL}"
    return settings


def block(code: str = "x = 1", *, at: int = 0, kind: str = "python", **kw: Any) -> dict[str, Any]:
    return {"start_seconds": at, "kind": kind, "code": code, "partial": False, **kw}


def reply(blocks: list[dict[str, Any]] | None = None, *, text: str | None = None) -> dict[str, Any]:
    content = text if text is not None else json.dumps({"blocks": blocks or []})
    return {
        "status": "completed",
        "steps": [
            {"type": "thought", "signature": "ignored"},
            {"type": "model_output", "content": [{"type": "text", "text": content}]},
        ],
    }


def end_marker() -> httpx.Response:
    # The exact reply Gemini's Interactions API gave for a chunk past the end (2026-11-25).
    return httpx.Response(
        400,
        json={
            "error": {
                "message": "Unable to process input: the file or media data is empty, "
                "corrupted, or malformed.",
                "code": "invalid_request",
            }
        },
    )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        with self.lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.sleeps.append(seconds)
            self.now += seconds


def test_saved_replies_follow_the_captured_interactions_shape(settings: Settings) -> None:
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert request.method == "POST"
        assert str(request.url) == "https://reader.test/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == KEY
        assert KEY not in request.content.decode()
        window = body["input"][1]["processing"]
        start, end = window["start_offset"][:-1], window["end_offset"][:-1]
        data = json.loads((FIXTURES / f"CbpsDMwFG2g_{start}-{end}.json").read_text())
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, settings, http)
    captured = json.loads((FIXTURES / "CbpsDMwFG2g_0-300.request.json").read_text())
    captured["model"] = settings.video_reader_model
    captured["input"][0]["text"] = PROMPT
    assert calls[0] == captured
    assert len(calls) == 3
    assert result.source_kind == SourceKind.URL
    assert result.source_format == SourceFormat.VIDEO
    assert result.source == URL
    assert result.title is None
    assert result.published is None
    assert result.packages == ["ipykernel", "openai", "pandas", "python-dotenv"]
    assert all(item.low_confidence for item in result.blocks)
    assert [item.idx for item in result.blocks] == list(range(len(result.blocks)))
    timestamps = [item.video_timestamp for item in result.blocks]
    assert timestamps == sorted(timestamps)
    assert next(item.video_timestamp for item in result.blocks if item.code == "1+1") == 750
    code = [item.code for item in result.blocks]
    assert code.count("pip install -r requirements.txt") == 1
    python = [item for item in result.blocks if item.kind == StepKind.PYTHON]
    assert len(python) == 3
    assert all(item.runnable_code == item.code for item in python)
    outputs = [item for item in result.blocks if item.kind == StepKind.OUTPUT]
    assert len(outputs) == 3
    assert all(item.runnable_code is None and item.context for item in outputs)
    assert 'OPENAI_API_KEY="<redacted>"' in code


@pytest.mark.parametrize("filename", ["0-300", "300-600", "600-900"])
def test_each_saved_reply_parses_as_typed_blocks(filename: str) -> None:
    result = parse_reply(json.loads((FIXTURES / f"CbpsDMwFG2g_{filename}.json").read_text()))
    assert result.blocks
    assert all(isinstance(item, VideoBlock) for item in result.blocks)


@pytest.mark.parametrize(
    "items,expected",
    [
        (
            [block("x = 1  \n\n\nprint(x)"), block("x = 1\n\nprint(x)", at=3)],
            [(0, "x = 1\n\nprint(x)")],
        ),
        ([block("x = 1"), block("x = 1\nprint(x)", at=5)], [(0, "x = 1\nprint(x)")]),
        (
            [block("a", at=280), block("a\nb", at=300), block("a\nb\nc", at=320)],
            [(280, "a\nb\nc")],
        ),
        (
            [block("a", at=10), block("a\nb", at=40), block("a\nb\nc", at=70)],
            [(10, "a\nb\nc")],
        ),
        (
            [block("a", at=10), block("other", at=20), block("a\nb", at=40)],
            [(10, "a\nb"), (20, "other")],
        ),
        ([block("b = 2", at=8), block("a = 1", at=2)], [(2, "a = 1"), (8, "b = 2")]),
        ([block("x = 1\nprint(x)"), block("x = 1", at=5)], [(0, "x = 1\nprint(x)"), (5, "x = 1")]),
    ],
)
def test_deduplication_normalizes_compares_whole_lines_and_orders(
    items: list[dict[str, Any]], expected: list[tuple[int, str]]
) -> None:
    result = deduplicate([VideoBlock.model_validate(item) for item in items])
    assert [(item.start_seconds, item.code) for item in result] == expected


def test_dedup_does_not_compare_different_block_kinds() -> None:
    result = deduplicate(
        [
            VideoBlock.model_validate(block("42")),
            VideoBlock.model_validate(block("42", kind="output")),
        ]
    )
    assert len(result) == 2


def test_merge_reads_groups_blocks_by_first_sighting() -> None:
    reads = [
        deduplicate(
            [
                VideoBlock.model_validate(block("a", at=10)),
                VideoBlock.model_validate(block("a\nb", at=last)),
            ]
        )
        for last in (30, 60)
    ]
    assert [(item.start_seconds, count) for item, count in merge_reads(reads)] == [(10, 2)]


@pytest.mark.parametrize("mode", ["enabled", "unwritable", "disabled"])
def test_video_debug_reply(
    settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: str
) -> None:
    settings.video_max_minutes = 5
    destination = tmp_path / "replies"
    if mode == "unwritable":
        destination.write_text("Existing regular file", encoding="utf-8")
    if mode != "disabled":
        settings.video_debug_dir = str(destination)
    payload = reply([block(f'token = "{KEY}"')])
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http:
        assert VideoReader(settings, http).extract(URL).blocks

    if mode == "enabled":
        (path,) = destination.iterdir()
        assert path.name == "CbpsDMwFG2g-0-300-read1-reader-test-model.json"
        saved = path.read_text(encoding="utf-8")
        assert KEY not in saved
        assert "<redacted>" in saved
        assert parse_reply(json.loads(saved)).blocks
    elif mode == "unwritable":
        assert any("Could not save video reply" in record.message for record in caplog.records)
    else:
        assert not list(tmp_path.iterdir())


def test_two_chunks_then_end_marker_and_cross_boundary_growth(settings: Settings) -> None:
    calls: list[dict[str, Any]] = []
    replies = iter(
        [
            httpx.Response(200, json=reply([block("x = 1", at=290)])),
            httpx.Response(200, json=reply([block("x = 1\nprint(x)", at=2)])),
            end_marker(),
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return next(replies)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, settings, http)
    assert len(calls) == 3
    assert [item["input"][1]["processing"]["start_offset"] for item in calls] == [
        "0s",
        "300s",
        "600s",
    ]
    assert len(result.blocks) == 1
    assert result.blocks[0].code == "x = 1\nprint(x)"
    assert result.blocks[0].video_timestamp == 290


def test_interactions_past_end_reply_ends_the_video(fallback_settings: Settings) -> None:
    replies = iter(
        [
            httpx.Response(200, json=reply([block("x = 1", at=10)])),
            end_marker(),
        ]
    )

    models: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return next(replies)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, fallback_settings, http)
    assert [item.code for item in result.blocks] == ["x = 1"]
    assert models == [PRIMARY_MODEL, PRIMARY_MODEL]


def test_busy_model_is_retried_after_short_waits(settings: Settings) -> None:
    replies = iter(
        [
            httpx.Response(503, json={"error": {"code": "unavailable"}}),
            httpx.Response(503, json={"error": {"code": "unavailable"}}),
            httpx.Response(200, json=reply([block("x = 1", at=10)])),
            end_marker(),
        ]
    )
    sleeps: list[float] = []

    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = VideoReader(settings, http, sleep=sleeps.append).extract(URL)
    assert [item.code for item in result.blocks] == ["x = 1"]
    assert 5.0 in sleeps and 15.0 in sleeps


def test_model_busy_after_every_retry_still_fails(settings: Settings) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as http:
        with pytest.raises(ExtractionError) as caught:
            VideoReader(settings, http, sleep=lambda _: None).extract(URL)
    assert str(caught.value) == OVERLOADED.format(models=settings.video_reader_model)


def test_busy_model_switches_after_existing_retries(fallback_settings: Settings) -> None:
    fallback_settings.video_max_minutes = 5
    models: list[str] = []
    sleeps: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        models.append(body["model"])
        assert body["input"][1]["processing"]["start_offset"] == "0s"
        if len(models) <= 3:
            return httpx.Response(503)
        return httpx.Response(200, json=reply([block()]))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = VideoReader(fallback_settings, http, sleep=sleeps.append).extract(URL)
    assert result.blocks
    assert sleeps == [5, 15]
    assert models == [PRIMARY_MODEL] * 3 + [FALLBACK_MODEL]


def test_repeated_code_across_boundary_is_kept_once(settings: Settings) -> None:
    replies = iter(
        [
            httpx.Response(200, json=reply([block("x = 1  ", at=299)])),
            httpx.Response(200, json=reply([block("x = 1", at=0)])),
            end_marker(),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = extract_video(URL, settings, http)
    assert [(item.video_timestamp, item.code) for item in result.blocks] == [(299, "x = 1")]


def test_absolute_and_clip_relative_timestamps_are_ordered_across_chunks(
    settings: Settings,
) -> None:
    replies = iter(
        [
            httpx.Response(200, json=reply([block("first = 1", at=290)])),
            httpx.Response(
                200,
                json=reply([block("last = 3", at=367), block("relative = 2", at=5)]),
            ),
            end_marker(),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = extract_video(URL, settings, http)
    assert [(item.video_timestamp, item.code) for item in result.blocks] == [
        (290, "first = 1"),
        (305, "relative = 2"),
        (367, "last = 3"),
    ]


def test_minutes_cap_stops_without_an_extra_request(settings: Settings) -> None:
    settings.video_chunk_seconds = 40
    settings.video_max_minutes = 1
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["input"][1]["processing"])
        return httpx.Response(200, json=reply([block(f"x = {len(calls)}")]))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, settings, http)
    assert [item["start_offset"] for item in calls] == ["0s", "40s"]
    assert result.blocks[-1].video_timestamp == 40


@pytest.mark.parametrize(
    "missing", ["video_reader_base_url", "video_reader_api_key", "video_reader_model"]
)
def test_missing_configuration_message(settings: Settings, missing: str) -> None:
    setattr(settings, missing, SecretStr("") if missing.endswith("key") else "")

    def no_request(request: httpx.Request) -> httpx.Response:
        pytest.fail("Missing configuration must be rejected before HTTP")

    with httpx.Client(transport=httpx.MockTransport(no_request)) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    assert str(caught.value) == MISSING


@pytest.mark.parametrize("status", [400, 403])
def test_first_chunk_private_video_does_not_switch(
    fallback_settings: Settings, status: int
) -> None:
    models: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return httpx.Response(status, json={"error": {"status": "INVALID_ARGUMENT"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, fallback_settings, http)
    assert str(caught.value) == PRIVATE
    assert models == [PRIMARY_MODEL]


@pytest.mark.parametrize(
    "error",
    [
        {"status": "FAILED_PRECONDITION"},
        {"status": "INVALID_ARGUMENT", "message": "The processing window is invalid."},
        {"code": "invalid_request", "message": "The processing window is invalid."},
        {"status": "INVALID_ARGUMENT"},
    ],
)
def test_other_400_after_a_chunk_is_not_an_end_marker(
    settings: Settings, error: dict[str, str]
) -> None:
    replies = iter(
        [
            httpx.Response(200, json=reply([block()])),
            httpx.Response(400, json={"error": error}),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        with pytest.raises(
            ExtractionError, match=r"The video reader request failed \(HTTP 400\)\."
        ):
            extract_video(URL, settings, http)


@pytest.mark.parametrize("items", [[], [block("", kind="python")], [block("2", kind="output")]])
def test_no_code_message(settings: Settings, items: list[dict[str, Any]]) -> None:
    settings.video_max_minutes = 5
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply(items)))
    ) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    assert str(caught.value) == NO_CODE


@pytest.mark.parametrize("bad", ["bad reply", '{"blocks": [{"kind": "python"}]}'])
def test_parse_error_gets_one_prompt_repair(settings: Settings, bad: str) -> None:
    settings.video_max_minutes = 5
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=reply([block()], text=bad if len(calls) == 1 else None))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, settings, http)
    assert result.blocks[0].code == "x = 1"
    assert len(calls) == 2
    assert "error" in calls[1]["input"][0]["text"].lower()
    assert calls[1]["input"][1] == calls[0]["input"][1]


def test_second_parse_error_is_clear_and_redacts_key(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=reply(text=json.dumps({"blocks": KEY})))

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    assert len(calls) == 2
    assert "parse" in str(caught.value).lower() or "json" in str(caught.value).lower()
    assert KEY not in str(caught.value) + caplog.text + json.dumps(calls)


@pytest.mark.parametrize("header,seconds", [("3", 3), (None, 5), ("bad", 5), ("1", 1)])
def test_429_retries_once_and_notifies_waits_over_one_second(
    settings: Settings, header: str | None, seconds: int
) -> None:
    settings.video_max_minutes = 5
    clock = Clock()
    notices: list[tuple[str, float]] = []
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": header} if header is not None else {}),
            httpx.Response(200, json=reply([block()])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = VideoReader(settings, http, sleep=clock.sleep, monotonic=clock.monotonic).extract(
            URL, on_wait=lambda *args: notices.append(args)
        )
    assert result.blocks
    assert clock.sleeps == [seconds]
    assert notices == ([("video", seconds)] if seconds > 1 else [])


def test_429_http_date_uses_shared_parser(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.video_max_minutes = 5
    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("time.time", lambda: now.timestamp())
    clock = Clock()
    replies = iter(
        [
            httpx.Response(
                429, headers={"Retry-After": format_datetime(now + timedelta(seconds=4))}
            ),
            httpx.Response(200, json=reply([block()])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        VideoReader(settings, http, sleep=clock.sleep, monotonic=clock.monotonic).extract(URL)
    assert clock.sleeps == [4]


@pytest.mark.parametrize("header,calls_expected", [("21", 1), ("2", 2)])
def test_long_or_repeated_429_uses_quota_message(
    settings: Settings, header: str, calls_expected: int
) -> None:
    calls = 0
    clock = Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": header}, json={"error": KEY})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ExtractionError) as caught:
            VideoReader(settings, http, sleep=clock.sleep, monotonic=clock.monotonic).extract(URL)
    assert str(caught.value) == QUOTA.format(models=settings.video_reader_model)
    assert calls == calls_expected


def test_quota_fallback_sticks_for_the_current_check_only(
    fallback_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    fallback_settings.video_max_minutes = 10
    calls: list[tuple[str, str]] = []
    sleeps: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((body["model"], body["input"][1]["processing"]["start_offset"]))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=reply([block(f"x = {len(calls)}")]))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        reader = VideoReader(fallback_settings, http, sleep=sleeps.append)
        with caplog.at_level(logging.INFO, logger="app.extract.video"):
            assert reader.extract(URL).blocks
            assert reader.extract(URL).blocks
    assert calls == [
        (PRIMARY_MODEL, "0s"),
        (FALLBACK_MODEL, "0s"),
        (FALLBACK_MODEL, "300s"),
        (PRIMARY_MODEL, "0s"),
        (PRIMARY_MODEL, "300s"),
    ]
    assert sleeps == []
    switches = [message for message in caplog.messages if "switching to" in message]
    assert switches == [f"video reader: {PRIMARY_MODEL} quota used, switching to {FALLBACK_MODEL}"]
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    "status,requests_per_model,message",
    [(429, 1, QUOTA), (503, 3, OVERLOADED)],
    ids=["quota", "overloaded"],
)
def test_exhausted_models_are_named_in_failure_message(
    fallback_settings: Settings, status: int, requests_per_model: int, message: str
) -> None:
    models: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return httpx.Response(status, headers={"Retry-After": "21"}, json={"error": KEY})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ExtractionError) as caught:
            VideoReader(fallback_settings, http, sleep=lambda _: None).extract(URL)
    assert models == [PRIMARY_MODEL] * requests_per_model + [FALLBACK_MODEL] * requests_per_model
    assert str(caught.value) == message.format(models=f"{PRIMARY_MODEL}, {FALLBACK_MODEL}")


def test_minimum_interval_paces_chunks_and_notifies(settings: Settings) -> None:
    settings.video_max_minutes = 10
    settings.video_reader_min_interval_seconds = 3
    clock = Clock()
    sent: list[float] = []
    notices: list[tuple[str, float]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(clock.now)
        return httpx.Response(200, json=reply([block(f"x = {len(sent)}")]))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        VideoReader(settings, http, sleep=clock.sleep, monotonic=clock.monotonic).extract(
            URL, on_wait=lambda *args: notices.append(args)
        )
    assert sent == [0, 3]
    assert clock.sleeps == [3]
    assert notices == [("video", 3)]


def test_rate_limit_retry_does_not_consume_parse_retry(settings: Settings) -> None:
    settings.video_max_minutes = 5
    clock = Clock()
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json=reply(text="bad")),
            httpx.Response(200, json=reply([block()])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = VideoReader(settings, http, sleep=clock.sleep, monotonic=clock.monotonic).extract(
            URL
        )
    assert result.blocks
    assert clock.sleeps == [2]


def test_two_checks_share_reader_pacing_without_locking_http(settings: Settings) -> None:
    settings.video_max_minutes = 5
    settings.video_reader_min_interval_seconds = 3
    clock = Clock()
    sent: list[float] = []
    first_entered = threading.Event()
    second_sent = threading.Event()

    def respond(request: httpx.Request) -> httpx.Response:
        with clock.lock:
            sent.append(clock.now)
            index = len(sent)
        if index == 1:
            first_entered.set()
            assert second_sent.wait(timeout=5), "HTTP must run outside the pacing lock"
        else:
            second_sent.set()
        return httpx.Response(200, json=reply([block()]))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        reader = VideoReader(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(reader.extract, URL)
            assert first_entered.wait(timeout=5)
            second = pool.submit(reader.extract, URL)
            assert first.result(timeout=5).blocks
            assert second.result(timeout=5).blocks
    assert sent == [0, 3]
    assert clock.sleeps == [3]


def test_failed_interaction_is_a_failed_chunk(settings: Settings) -> None:
    data = {**reply([block()]), "status": "failed"}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as http:
        with pytest.raises(ExtractionError):
            extract_video(URL, settings, http)


@pytest.mark.parametrize("kind", ["java", "unknown"])
def test_parser_rejects_unknown_kinds(kind: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        parse_reply(reply([block(kind=kind)]))


def test_shell_prompts_files_outputs_and_partial_blocks_are_safe(settings: Settings) -> None:
    settings.video_max_minutes = 5
    items = [
        block("(venv) C:\\path>pip install pandas==1.3\n$ pip install numpy", kind="shell"),
        block("openai\npandas", at=2, kind="file"),
        block("x = ", at=3, partial=True),
        block("visible output", at=4, kind="output"),
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply(items)))
    ) as http:
        result = extract_video(URL, settings, http)
    shell, file, python, output = result.blocks
    assert shell.code == "pip install pandas==1.3\npip install numpy"
    assert shell.runnable_code is None
    assert file.kind == StepKind.OTHER
    assert file.runnable_code is None
    assert python.code == "x ="
    assert python.runnable_code == "x ="
    assert output.kind == StepKind.OUTPUT and output.runnable_code is None
    assert output.context
    assert result.packages == ["numpy", "pandas"]


@pytest.mark.parametrize(
    "secret,code",
    [
        ("sk-" + "a" * 20, 'client("{secret}")'),
        ("AIza" + "b" * 30, 'client("{secret}")'),
        ("gsk_" + "c" * 20, 'client("{secret}")'),
        ("nvapi-" + "d" * 20, 'client("{secret}")'),
        ("hf_" + "e" * 20, 'client("{secret}")'),
        ("custom-key", 'openai.api_key = "{secret}"'),
        ("custom-token", "ACCESS_TOKEN='{secret}'"),
        ("custom-secret", 'client_secret = "{secret}"'),
        ("short", 'OPENAI_API_KEY="{secret}"'),
        ("annotated-value", 'API_KEY: str = "{secret}"'),
        ("environment-value", 'os.environ["API_KEY"] = "{secret}"'),
        ("multiline-value", 'API_KEY = """{secret}"""'),
        ("first-line\nsecond-line", 'MY_SECRET = """{secret}"""'),
        (KEY, 'client("{secret}")'),
    ],
)
def test_secrets_are_masked_before_becoming_blocks(
    settings: Settings, secret: str, code: str
) -> None:
    settings.video_max_minutes = 5
    items = [block(code.format(secret=secret))]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply(items)))
    ) as http:
        result = extract_video(URL, settings, http)
    assert secret not in result.blocks[0].code
    assert json.dumps(secret)[1:-1] not in result.model_dump_json()
    assert "<redacted>" in result.blocks[0].code
    assert result.blocks[0].runnable_code == result.blocks[0].code
    if code.startswith("API_KEY: str"):
        assert result.blocks[0].code.startswith("API_KEY: str =")


@pytest.mark.parametrize("kind", ["file", "shell"])
@pytest.mark.parametrize(
    "code,expected",
    [
        ("OPENAI_API_KEY=abcd1234efgh5678ijkl9012", "OPENAI_API_KEY=<redacted>"),
        ("export TAVILY_API_KEY=tvly-abcdefghijklmnop", "export TAVILY_API_KEY=<redacted>"),
        ("ACCESS_TOKEN: yaml-token", "ACCESS_TOKEN: <redacted>"),
        ("set client_secret=windows-secret", "set client_secret=<redacted>"),
        ("  my_kEy = short-value # comment", "  my_kEy = <redacted> # comment"),
        ("Export access_token=one-value other=value", "Export access_token=<redacted> other=value"),
    ],
)
def test_unquoted_secrets_are_masked_in_file_and_shell_blocks(
    settings: Settings, kind: str, code: str, expected: str
) -> None:
    settings.video_max_minutes = 5
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=reply([block(code, kind=kind)]))
        )
    ) as http:
        result = extract_video(URL, settings, http)
    assert result.blocks[0].code == expected
    assert result.blocks[0].runnable_code is None


@pytest.mark.parametrize(
    "code",
    [
        'os.getenv("OPENAI_API_KEY")',
        'openai.api_key=os.getenv("OPENAI_API_KEY")',
        'OPENAI_API_KEY=os.getenv("OPENAI_API_KEY")',
        "ordinary_name=value",
    ],
)
def test_secret_lookups_and_ordinary_assignments_are_preserved(
    settings: Settings, code: str
) -> None:
    settings.video_max_minutes = 5
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply([block(code)])))
    ) as http:
        result = extract_video(URL, settings, http)
    assert result.blocks[0].code == code


@pytest.mark.parametrize(
    "code",
    [
        "max_tokens = 100",
        "TOKEN_LIMIT=4096",
        "key = df.columns[0]",
        "API_KEY = config.key",
        "MONKEY_COUNT = 3",
    ],
)
def test_python_expressions_with_secret_like_names_are_preserved(
    settings: Settings, code: str
) -> None:
    settings.video_max_minutes = 5
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply([block(code)])))
    ) as http:
        result = extract_video(URL, settings, http)
    assert result.blocks[0].code == code
    assert result.blocks[0].runnable_code == code
    assert redact_code(code) == code


def test_unquoted_secrets_in_output_remain_masked(settings: Settings) -> None:
    settings.video_max_minutes = 5
    items = [block("x = 1"), block("OPENAI_API_KEY=bare-secret", at=1, kind="output")]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply(items)))
    ) as http:
        result = extract_video(URL, settings, http)
    assert result.blocks[1].code == "OPENAI_API_KEY=<redacted>"
    assert result.blocks[1].runnable_code is None


@pytest.mark.parametrize(
    "code,expected",
    [
        ("(venv) C:\\path>set API_KEY=abcd1234", "set API_KEY=<redacted>"),
        ("$ export TOKEN=barevalue", "export TOKEN=<redacted>"),
    ],
)
def test_unquoted_secrets_are_masked_after_shell_prompt_removal(
    settings: Settings, code: str, expected: str
) -> None:
    settings.video_max_minutes = 5
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=reply([block(code, kind="shell")]))
        )
    ) as http:
        result = extract_video(URL, settings, http)
    assert result.blocks[0].code == expected


def test_escaped_configured_key_in_validation_error_is_not_repeated(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    secret = 'reader-"-\\-secret'
    settings.video_reader_api_key = SecretStr(secret)
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=reply(text=json.dumps({"blocks": secret})))

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    error_text = str(caught.value) + caplog.text
    assert secret not in error_text
    assert json.dumps(secret)[1:-1] not in error_text
    assert len(calls) == 2
    repair_prompt = calls[1]["input"][0]["text"]
    assert secret not in repair_prompt
    assert json.dumps(secret)[1:-1] not in repair_prompt


@pytest.mark.parametrize("status", [401, 403, 500, 302])
def test_http_errors_never_expose_configured_key(
    settings: Settings, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    caplog.set_level(logging.DEBUG)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, headers={"Location": "https://login.test/"}, text=KEY)
        ),
        follow_redirects=True,
    ) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    assert KEY not in str(caught.value) + caplog.text


def test_transport_errors_never_expose_configured_key(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(KEY, request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    assert KEY not in str(caught.value) + caplog.text


def test_video_reads_setting_defaults_to_two_and_is_bounded() -> None:
    assert Settings(_env_file=None, providers=[]).video_reads == 2
    for bad in (0, 4):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, providers=[], video_reads=bad)


def test_one_read_leaves_agreement_unset(settings: Settings) -> None:
    settings.video_max_minutes = 5
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply([block()])))
    ) as http:
        result = extract_video(URL, settings, http)
    assert [item.read_agreement for item in result.blocks] == [None]


def test_two_agreeing_reads_confirm_the_block(settings: Settings) -> None:
    settings.video_reads = 2
    settings.video_max_minutes = 5
    calls: list[dict[str, Any]] = []
    replies = iter(
        [
            httpx.Response(200, json=reply([block("x = 1", at=10)])),
            httpx.Response(200, json=reply([block("x = 1  ", at=18)])),
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return next(replies)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, settings, http)
    assert len(calls) == 2
    assert [(item.code, item.video_timestamp, item.read_agreement) for item in result.blocks] == [
        ("x = 1", 10, 2)
    ]
    assert all(item.low_confidence for item in result.blocks)


def test_reads_too_far_apart_in_time_do_not_match(settings: Settings) -> None:
    settings.video_reads = 2
    settings.video_max_minutes = 5
    replies = iter(
        [
            httpx.Response(200, json=reply([block("x = 1", at=10)])),
            httpx.Response(200, json=reply([block("x = 1", at=40)])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = extract_video(URL, settings, http)
    assert [(item.video_timestamp, item.read_agreement) for item in result.blocks] == [
        (10, 1),
        (40, 1),
    ]


def test_a_misread_line_keeps_one_block_with_the_first_read(settings: Settings) -> None:
    settings.video_reads = 2
    settings.video_max_minutes = 5
    replies = iter(
        [
            httpx.Response(200, json=reply([block("a = 1", at=1), block("y = 2\nz = 3", at=5)])),
            httpx.Response(200, json=reply([block("a = 1", at=1), block("y = 2\nz = 8", at=6)])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = extract_video(URL, settings, http)
    assert [(item.code, item.read_agreement) for item in result.blocks] == [
        ("a = 1", 2),
        ("y = 2\nz = 3", 1),
    ]


def test_majority_version_wins_with_three_reads(settings: Settings) -> None:
    settings.video_reads = 3
    settings.video_max_minutes = 5
    replies = iter(
        [
            httpx.Response(200, json=reply([block("z = 8", at=4)])),
            httpx.Response(200, json=reply([block("z = 3", at=5)])),
            httpx.Response(200, json=reply([block("z = 3", at=6)])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = extract_video(URL, settings, http)
    assert [(item.code, item.video_timestamp, item.read_agreement) for item in result.blocks] == [
        ("z = 3", 5, 2)
    ]


def test_block_seen_by_one_read_only_is_kept(settings: Settings) -> None:
    settings.video_reads = 2
    settings.video_max_minutes = 5
    replies = iter(
        [
            httpx.Response(200, json=reply([block("a = 1", at=1)])),
            httpx.Response(200, json=reply([block("a = 1", at=1), block("b = 2", at=100)])),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        result = extract_video(URL, settings, http)
    assert [(item.code, item.read_agreement) for item in result.blocks] == [
        ("a = 1", 2),
        ("b = 2", 1),
    ]


def test_request_count_is_chunks_times_reads(settings: Settings) -> None:
    settings.video_reads = 3
    settings.video_chunk_seconds = 40
    settings.video_max_minutes = 1
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["input"][1]["processing"]["start_offset"])
        return httpx.Response(200, json=reply([block("x = 1")]))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = extract_video(URL, settings, http)
    assert calls == ["0s", "0s", "0s", "40s", "40s", "40s"]
    assert [item.read_agreement for item in result.blocks] == [3]


def test_end_marker_on_the_first_read_stops_without_more_reads(settings: Settings) -> None:
    settings.video_reads = 2
    replies = iter(
        [
            httpx.Response(200, json=reply([block()])),
            httpx.Response(200, json=reply([block()])),
            end_marker(),
        ]
    )
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return next(replies)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        extract_video(URL, settings, http)
    assert calls == 3


def test_quota_message_applies_to_every_read(settings: Settings) -> None:
    settings.video_reads = 2
    replies = iter(
        [
            httpx.Response(200, json=reply([block()])),
            httpx.Response(429, headers={"Retry-After": "21"}),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        with pytest.raises(ExtractionError) as caught:
            extract_video(URL, settings, http)
    assert str(caught.value) == QUOTA.format(models=settings.video_reader_model)
