"""Request pacing and Retry-After handling shared by model clients."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Protocol

WaitCallback = Callable[[str, float], None]


class PacedProvider(Protocol):
    name: str
    interval: float
    lock: threading.Lock
    last_request: float | None


@dataclass
class PacingState:
    name: str
    interval: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_request: float | None = None


def wait(
    provider: str,
    seconds: float,
    on_wait: WaitCallback | None,
    sleep: Callable[[float], None],
) -> None:
    if seconds > 1 and on_wait is not None:
        on_wait(provider, seconds)
    if seconds > 0:
        sleep(seconds)


def pace(
    provider: PacedProvider,
    on_wait: WaitCallback | None,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> None:
    while True:
        with provider.lock:
            now = monotonic()
            remaining = (
                0
                if provider.last_request is None
                else provider.last_request + provider.interval - now
            )
            if remaining <= 0:
                provider.last_request = now
                return
        # Another thread may send while this one sleeps, so recheck admission.
        wait(provider.name, remaining, on_wait, sleep)


def retry_after(header: str) -> float:
    try:
        seconds = float(header)
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 5
    return max(0, seconds) if math.isfinite(seconds) else 5
