"""Compare tutorial output with a sandbox reply without changing the run's label."""

import re
from decimal import Decimal, DecimalException, localcontext
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models import ExecResult

_RANDOMNESS = re.compile(
    r"\brandom\s*\.|\btorch\.rand|\brandint\b|\bshuffle\s*\(|\.sample\s*\(|\buuid\w*\b"
)
_SEED = re.compile(
    r"\b(?:manual_seed|seed)\s*\(|\brandom_state\s*=|\bdefault_rng\s*\(\s*[+-]?\d+\s*\)"
)
_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{4,}\b")
_TIME = r"\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?"
_DATE_TIME = re.compile(
    rf"(?<!\w)(?:\d{{4}}-\d{{2}}-\d{{2}}(?:[T ]{_TIME}(?:Z|[+-]\d{{2}}:?\d{{2}})?)?"
    rf"|{_TIME})(?!\w)"
)
_DURATION = re.compile(
    r"(?<![\w.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)[ \t]*(?:s/it|it/s|ms|us|µs|μs|ns|min|s)\b"
)
_PROGRESS = re.compile(r"\d+%\||\u2501{2,}|={2,}>")
_SPACING = re.compile(r"(?<=\S)[ \t]+(?=\S)")
_NUMBER = re.compile(r"(?<![\w.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?![\w.])")


class OutputMatch(StrEnum):
    MATCHES = "matches"
    MATCHES_IGNORING = "matches_ignoring"
    DIFFERS = "differs"
    NOT_COMPARED = "not_compared"


class OutputCheck(BaseModel):
    status: OutputMatch
    ignored: list[str] = Field(default_factory=list)
    reason: str = ""
    shown: str
    actual: str


def _not_compared_reason(result: ExecResult | None, code: str) -> str:
    if result is None:
        return "There was no run to compare."
    if result.status == "error":
        return "The step failed."
    if result.stdout.endswith("[truncated]"):
        return "The sandbox output was truncated."
    if _RANDOMNESS.search(code) and not _SEED.search(code):
        return "The code uses randomness without a seed."
    return ""


def _normalize(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in lines).strip("\n"))


def _round_float_tokens(shown: str, actual: str) -> str:
    expected = _NUMBER.split(shown)
    observed = _NUMBER.split(actual)
    if expected[::2] != observed[::2]:
        return actual
    for index in range(1, len(expected), 2):
        sample, value = expected[index], observed[index]
        if sample == value or not all(re.search(r"[.eE]", token) for token in (sample, value)):
            continue
        try:
            target = Decimal(sample)
            number = Decimal(value)
            if number.as_tuple().exponent >= target.as_tuple().exponent:
                continue
            with localcontext() as context:
                context.prec = max(len(sample), len(value), context.prec)
                if number.quantize(target) == target:
                    observed[index] = sample
        except DecimalException:
            continue
    return "".join(observed)


def _without_progress(text: str) -> str:
    return _normalize("\n".join(line for line in text.split("\n") if not _PROGRESS.search(line)))


def _loose_compare(shown: str, actual: str) -> tuple[bool, list[str]]:
    ignored: list[str] = []
    for label, pattern, replacement in (
        ("memory addresses", _ADDRESS, "<address>"),
        ("dates and times", _DATE_TIME, "<date or time>"),
        ("durations", _DURATION, "<duration>"),
        ("progress bars", None, ""),
        ("spacing", _SPACING, " "),
    ):
        if pattern is None:
            pair = _without_progress(shown), _without_progress(actual)
        else:
            pair = pattern.sub(replacement, shown), pattern.sub(replacement, actual)
        if pair != (shown, actual):
            ignored.append(label)
        shown, actual = pair
    rounded = _round_float_tokens(shown, actual)
    if rounded != actual:
        ignored.append("float digits")
    return shown == rounded, ignored


def check_output(
    shown: str | None, result: ExecResult | None, code: str, earlier_code: str = ""
) -> OutputCheck | None:
    if shown is None:
        return None
    reason = _not_compared_reason(result, earlier_code + "\n" + code)
    if reason:
        return OutputCheck(
            status=OutputMatch.NOT_COMPARED,
            reason=reason,
            shown=shown,
            actual=result.stdout if result else "",
        )
    assert result is not None
    stdout = result.stdout
    variants = [stdout]
    if result.result is not None:
        separator = "\n" if stdout and not stdout.endswith(("\r", "\n")) else ""
        variants.append(stdout + separator + result.result)

    normalized_shown = _normalize(shown)
    normalized_variants = [(actual, _normalize(actual)) for actual in variants]
    for actual, normalized in normalized_variants:
        if normalized_shown == normalized:
            return OutputCheck(status=OutputMatch.MATCHES, shown=shown, actual=actual)

    for actual, normalized in normalized_variants:
        same, ignored = _loose_compare(normalized_shown, normalized)
        if same:
            return OutputCheck(
                status=OutputMatch.MATCHES_IGNORING, shown=shown, actual=actual, ignored=ignored
            )
    return OutputCheck(
        status=OutputMatch.DIFFERS, shown=shown, actual=variants[-1], ignored=ignored
    )
