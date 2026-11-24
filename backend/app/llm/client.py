"""OpenAI-compatible providers with ordered fallback and validated JSON replies."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

import httpx
from openai import APIError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.llm.pacing import WaitCallback, pace, retry_after, wait

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass
class _Provider:
    name: str
    model: str
    client: OpenAI
    interval: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_request: float | None = None


class LLMResult[T: BaseModel](BaseModel):
    value: T
    provider: str


class LLMUnavailableError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("No LLM provider available: " + "; ".join(reasons))


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers: list[_Provider] = []
        self._sleep = sleep
        self._monotonic = monotonic
        self._max_wait = settings.llm_max_wait_seconds
        self._keys = [provider.api_key.get_secret_value() for provider in settings.providers]
        self._owns_http = http_client is None
        for provider in settings.providers:
            self._providers.append(
                _Provider(
                    provider.name,
                    provider.model,
                    OpenAI(
                        api_key=provider.api_key.get_secret_value(),
                        base_url=provider.base_url,
                        http_client=http_client,
                        max_retries=0,
                        timeout=60.0,
                    ),
                    provider.min_interval_seconds,
                )
            )

    def _redact(self, text: str) -> str:
        for key in sorted((key for key in self._keys if key), key=len, reverse=True):
            text = text.replace(key, "[redacted]")
        return text

    def _wait(self, provider: str, seconds: float, on_wait: WaitCallback | None) -> None:
        wait(provider, seconds, on_wait, self._sleep)

    def _pace(self, provider: _Provider, on_wait: WaitCallback | None) -> None:
        pace(provider, on_wait, self._sleep, self._monotonic)

    @staticmethod
    def _retry_after(exc: RateLimitError) -> float:
        return retry_after(exc.response.headers.get("Retry-After", ""))

    def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        on_wait: WaitCallback | None = None,
    ) -> LLMResult[T]:
        reasons = []
        if not self._providers:
            raise LLMUnavailableError(["No supported provider has both a key and model configured"])
        for entry in self._providers:
            provider, model, client = entry.name, entry.model, entry.client
            conversation = [
                {
                    "role": "system",
                    "content": "Return only a JSON object matching this schema: "
                    + self._redact(str(schema.model_json_schema())),
                },
                *(dict(message) for message in messages),
            ]
            retried_rate_limit = False
            for attempt in range(2):
                error = None
                while True:
                    try:
                        self._pace(entry, on_wait)
                        response = client.chat.completions.create(
                            model=model, messages=conversation
                        )
                        break
                    except RateLimitError as exc:
                        seconds = self._retry_after(exc)
                        if retried_rate_limit or seconds > self._max_wait:
                            error = exc
                            break
                        retried_rate_limit = True
                        self._wait(provider, seconds, on_wait)
                    except APIError as exc:
                        error = exc
                        break
                if error is not None:
                    reason = f"{provider}: {type(error).__name__}"
                    status = getattr(error, "status_code", None)
                    if status is not None:
                        reason += f" ({status})"
                    reasons.append(reason)
                    logger.warning("LLM provider unavailable: %s", reason)
                    break
                try:
                    content = response.choices[0].message.content if response.choices else None
                    if not content:
                        raise ValueError("Empty completion")
                    start, end = content.find("{"), content.rfind("}")
                    if start < 0 or end < start:
                        raise ValueError("Reply contains no JSON object")
                    result = schema.model_validate_json(content[start : end + 1])
                except (ValueError, ValidationError) as exc:
                    if attempt == 0:
                        conversation.append({"role": "assistant", "content": content or ""})
                        conversation.append(
                            {
                                "role": "user",
                                "content": "JSON validation error: "
                                + self._redact(str(exc))
                                + ". Return a corrected JSON object only.",
                            }
                        )
                    else:
                        reason = f"{provider}: invalid JSON after two attempts"
                        reasons.append(reason)
                        logger.warning("LLM provider unavailable: %s", reason)
                else:
                    return LLMResult(value=result, provider=provider)
        raise LLMUnavailableError(reasons)

    def close(self) -> None:
        """Close internally created HTTP clients; injected clients belong to the caller."""
        if self._owns_http:
            for provider in self._providers:
                provider.client.close()
