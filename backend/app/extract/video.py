"""Transcribe public video clips by URL; recovered code is never executed here."""

import json
import re
import time
from collections.abc import Callable
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.extract.errors import ExtractionError
from app.extract.libs import library_fields
from app.extract.routing import route_url
from app.llm.pacing import PacingState, WaitCallback, pace, retry_after, wait
from app.models import Block, Extraction, SourceFormat, SourceKind, StepKind, is_runnable
from scripts.capture_video_reply import MISSING_CONFIG, PROMPT

VideoKind = Literal["python", "javascript", "typescript", "c", "cpp", "shell", "output", "file"]
_CODE_KINDS = {"python", "javascript", "typescript", "c", "cpp"}

PRIVATE_VIDEO = (
    "This video isn't public, so it can't be read. Upload the notebook or paste the code instead."
)
NO_CODE = "No code was visible in this video."
QUOTA_EXCEEDED = "The video reader's quota is used up for now. Try again later."

_KEY_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|"
    r"gsk_[A-Za-z0-9_-]+|nvapi-[A-Za-z0-9_-]+|hf_[A-Za-z0-9_-]+)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?P<prefix>\b[A-Za-z_]\w*(?:\.\w+)*"
    r"""(?:\[[ \t]*['"][^'"\r\n]+['"][ \t]*\])?"""
    r"(?:[ \t]*:[^=\r\n]+)?[ \t]*=[ \t]*)"
    r"""(?P<quote>"{3}|'{3}|"|')(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)""",
    re.S,
)
_UNQUOTED_SECRET_ASSIGNMENT = re.compile(
    r"(?P<prefix>^[ \t]*(?:(?:export|set)[ \t]+)?(?P<name>[A-Za-z_]\w*)"
    r"[ \t]*(?P<separator>=|:)[ \t]*)(?P<value>[^\s'\"]+)",
    re.M | re.I,
)
_FUNCTION_CALL = re.compile(r"[A-Za-z_]\w*(?:\.\w+)*\(")
_SHELL_PROMPT = re.compile(r"^(?:\([^\r\n)]*\)[ \t]*)?[A-Za-z]:[^\r\n>]*>[ \t]*")
_ENV_PROMPT = re.compile(r"^(?:\([^\r\n)]*\)[ \t]*)?\$[ \t]+")


class VideoBlock(BaseModel):
    start_seconds: int = Field(ge=0)
    kind: VideoKind
    code: str
    partial: bool


class VideoReply(BaseModel):
    blocks: list[VideoBlock]


class _Content(BaseModel):
    type: str
    text: str | None = None


class _InteractionStep(BaseModel):
    type: str
    content: list[_Content] = Field(default_factory=list)


class _Interaction(BaseModel):
    status: str
    steps: list[_InteractionStep] = Field(default_factory=list)


def parse_reply(reply: object) -> VideoReply:
    """Read text from model_output steps, ignoring opaque thought signatures."""
    interaction = _Interaction.model_validate(reply)
    if interaction.status != "completed":
        raise ExtractionError("The video reader did not complete this clip.")
    text = "\n".join(
        content.text
        for step in interaction.steps
        if step.type == "model_output"
        for content in step.content
        if content.type == "text" and content.text is not None
    )
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Reply contains no JSON object")
    return VideoReply.model_validate_json(text[start : end + 1])


def redact_code(
    text: str,
    key: str = "",
    *,
    kind: VideoKind = "python",
) -> str:
    """Mask literal secrets while preserving unquoted Python expressions."""
    if key:
        text = text.replace(key, "<redacted>")
        text = text.replace(json.dumps(key)[1:-1], "<redacted>")
    text = _KEY_PATTERN.sub("<redacted>", text)

    def mask_assignment(match: re.Match[str]) -> str:
        name = match.group("prefix").split("=", 1)[0].upper()
        if any(marker in name for marker in ("KEY", "TOKEN", "SECRET")):
            quote = match.group("quote")
            return match.group("prefix") + quote + "<redacted>" + quote
        return match.group(0)

    text = _SECRET_ASSIGNMENT.sub(mask_assignment, text)
    if kind in _CODE_KINDS:
        return text

    def mask_unquoted_assignment(match: re.Match[str]) -> str:
        name = match.group("name").upper()
        value = match.group("value")
        # A colon followed by a type and '=' belongs to a quoted Python assignment.
        if match.group("separator") == ":" and (
            re.match(r"[ \t]*=", text[match.end() :]) or value.endswith("=")
        ):
            return match.group(0)
        if any(
            marker in name for marker in ("KEY", "TOKEN", "SECRET")
        ) and not _FUNCTION_CALL.match(value):
            return match.group("prefix") + "<redacted>"
        return match.group(0)

    return _UNQUOTED_SECRET_ASSIGNMENT.sub(mask_unquoted_assignment, text)


def _normalize(code: str) -> str:
    lines: list[str] = []
    for line in code.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip()
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip("\n")


def _strip_shell_prompts(code: str) -> str:
    return "\n".join(_ENV_PROMPT.sub("", _SHELL_PROMPT.sub("", line)) for line in code.splitlines())


def deduplicate(blocks: list[VideoBlock]) -> list[VideoBlock]:
    """Keep code growth and exact repeats consistent across clip boundaries."""
    kept: list[VideoBlock] = []
    for block in sorted(blocks, key=lambda block: block.start_seconds):
        code = _normalize(block.code)
        if not code:
            continue
        block = block.model_copy(update={"code": code})
        if any(old.kind == block.kind and old.code == code for old in kept):
            continue
        lines = code.splitlines()
        kept = [
            old
            for old in kept
            if not (
                old.kind == block.kind
                and len(old.code.splitlines()) < len(lines)
                and old.code.splitlines() == lines[: len(old.code.splitlines())]
            )
        ]
        kept.append(block)
    return kept


READ_WINDOW_SECONDS = 15


def merge_reads(reads: list[list[VideoBlock]]) -> list[tuple[VideoBlock, int]]:
    """Group blocks across reads by kind and time; keep the version most reads gave."""
    groups: list[list[tuple[int, VideoBlock]]] = []
    for number, blocks in enumerate(reads):
        for item in blocks:
            code = _normalize(item.code)
            open_groups = [
                group
                for group in groups
                if group[0][1].kind == item.kind
                and all(seen != number for seen, _ in group)
                and abs(group[0][1].start_seconds - item.start_seconds) <= READ_WINDOW_SECONDS
            ]
            same = [g for g in open_groups if any(_normalize(v.code) == code for _, v in g)]
            chosen = same or open_groups
            if chosen:
                min(chosen, key=lambda g: abs(g[0][1].start_seconds - item.start_seconds)).append(
                    (number, item)
                )
            else:
                groups.append([(number, item)])
    merged: list[tuple[VideoBlock, int]] = []
    for group in groups:
        counts: dict[str, int] = {}
        for _, member in group:
            key = _normalize(member.code)
            counts[key] = counts.get(key, 0) + 1
        best = max(counts.values())
        winner = next(member for _, member in group if counts[_normalize(member.code)] == best)
        merged.append((winner, best))
    merged.sort(key=lambda entry: entry[0].start_seconds)
    return merged


def _request_body(url: str, model: str, start: int, end: int, prompt: str) -> dict:
    # Keep the capture script's Interactions request shape, including static processing.
    return {
        "model": model,
        "input": [
            {"type": "text", "text": prompt},
            {
                "type": "video",
                "uri": url,
                "processing": {
                    "type": "static",
                    "start_offset": f"{start}s",
                    "end_offset": f"{end}s",
                },
            },
        ],
    }


class VideoReader:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client,
        *,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._sleep = time.sleep if sleep is None else sleep
        self._monotonic = time.monotonic if monotonic is None else monotonic
        self._pacing = PacingState("video", settings.video_reader_min_interval_seconds)

    def _chunk(
        self, url: str, start: int, end: int, on_wait: WaitCallback | None
    ) -> VideoReply | None:
        settings = self._settings
        key = settings.video_reader_api_key.get_secret_value()
        prompt = PROMPT
        retried_rate_limit = False
        for attempt in range(2):
            while True:
                pace(self._pacing, on_wait, self._sleep, self._monotonic)
                try:
                    response = self._http.post(
                        settings.video_reader_base_url.rstrip("/") + "/interactions",
                        headers={"x-goog-api-key": key},
                        json=_request_body(url, settings.video_reader_model, start, end, prompt),
                        timeout=300,
                        follow_redirects=False,
                    )
                except httpx.HTTPError as exc:
                    raise ExtractionError(
                        f"The video reader request failed ({type(exc).__name__})."
                    ) from None
                if response.status_code != 429:
                    break
                seconds = retry_after(response.headers.get("Retry-After", ""))
                if retried_rate_limit or seconds > settings.llm_max_wait_seconds:
                    raise ExtractionError(QUOTA_EXCEEDED)
                retried_rate_limit = True
                wait("video", seconds, on_wait, self._sleep)
            if response.status_code in (400, 403) and start == 0:
                raise ExtractionError(PRIVATE_VIDEO)
            if response.status_code == 400:
                try:
                    error = response.json().get("error", {})
                    is_end = isinstance(error, dict) and error.get("status") == "INVALID_ARGUMENT"
                except (ValueError, AttributeError):
                    is_end = False
                if is_end:
                    return None
            if response.status_code != 200:
                raise ExtractionError(
                    f"The video reader request failed (HTTP {response.status_code})."
                )
            try:
                return parse_reply(response.json())
            except ValueError as exc:
                if attempt:
                    raise ExtractionError(
                        "The video reader returned invalid JSON after two attempts."
                    ) from None
                prompt = (
                    PROMPT
                    + " JSON validation error: "
                    + redact_code(str(exc), key)
                    + ". Return a corrected JSON object only."
                )
        raise AssertionError("Unreachable video parsing state")

    def extract(self, url: str, *, on_wait: WaitCallback | None = None) -> Extraction:
        settings = self._settings
        key = settings.video_reader_api_key.get_secret_value()
        if not all(
            value.strip()
            for value in (settings.video_reader_base_url, key, settings.video_reader_model)
        ):
            raise ExtractionError(MISSING_CONFIG)
        route = route_url(url)
        if route.kind != "video":
            raise ExtractionError("Provide a public YouTube video URL with a valid video ID.")
        url = route.fetch_urls[0]
        reads = settings.video_reads
        recovered: list[list[VideoBlock]] = [[] for _ in range(reads)]
        cap = settings.video_max_minutes * 60
        for start in range(0, cap, settings.video_chunk_seconds):
            end = min(start + settings.video_chunk_seconds, cap)
            finished = False
            for number in range(reads):
                reply = self._chunk(url, start, end, on_wait)
                if reply is None:
                    finished = True
                    break
                for block in reply.blocks:
                    code = _strip_shell_prompts(block.code) if block.kind == "shell" else block.code
                    code = redact_code(code, key, kind=block.kind)
                    timestamp = block.start_seconds
                    if not start <= timestamp <= end:
                        timestamp += start
                    recovered[number].append(
                        block.model_copy(update={"start_seconds": timestamp, "code": code})
                    )
            if finished:
                break
        merged = merge_reads([deduplicate(blocks) for blocks in recovered])
        if not merged or all(block.kind == "output" for block, _ in merged):
            raise ExtractionError(NO_CODE)
        blocks: list[Block] = []
        previous: int | None = None
        for recovered_block, agreement in merged:
            kind = (
                StepKind.OTHER if recovered_block.kind == "file" else StepKind(recovered_block.kind)
            )
            context = "This code was cut off in the video." if recovered_block.partial else ""
            if kind == StepKind.OUTPUT and previous is not None:
                context = f"Expected output for step {previous + 1}."
            blocks.append(
                Block(
                    idx=len(blocks),
                    kind=kind,
                    code=recovered_block.code,
                    runnable_code=recovered_block.code if is_runnable(kind) else None,
                    context=context,
                    video_timestamp=recovered_block.start_seconds,
                    low_confidence=True,
                    read_agreement=agreement if reads > 1 else None,
                )
            )
            if kind != StepKind.OUTPUT:
                previous = blocks[-1].idx
        return Extraction(
            source_kind=SourceKind.URL,
            source_format=SourceFormat.VIDEO,
            source=url,
            blocks=blocks,
            **library_fields(blocks),
        )


def extract_video(
    url: str,
    settings: Settings,
    client: httpx.Client,
    *,
    on_wait: WaitCallback | None = None,
) -> Extraction:
    return VideoReader(settings, client).extract(url, on_wait=on_wait)
