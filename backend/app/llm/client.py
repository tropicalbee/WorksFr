"""OpenAI-compatible providers with ordered fallback and validated JSON replies."""

import logging
from typing import TypeVar

import httpx
from openai import APIError, OpenAI
from pydantic import BaseModel, ValidationError

from app.config import Settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMResult[T: BaseModel](BaseModel):
    value: T
    provider: str


class LLMUnavailableError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("No LLM provider available: " + "; ".join(reasons))


class LLMClient:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self._providers: list[tuple[str, str, OpenAI]] = []
        self._keys = [provider.api_key.get_secret_value() for provider in settings.providers]
        self._owns_http = http_client is None
        for provider in settings.providers:
            self._providers.append(
                (
                    provider.name,
                    provider.model,
                    OpenAI(
                        api_key=provider.api_key.get_secret_value(),
                        base_url=provider.base_url,
                        http_client=http_client,
                        max_retries=0,
                        timeout=60.0,
                    ),
                )
            )

    def _redact(self, text: str) -> str:
        for key in sorted((key for key in self._keys if key), key=len, reverse=True):
            text = text.replace(key, "[redacted]")
        return text

    def complete_json(self, messages: list[dict[str, str]], schema: type[T]) -> LLMResult[T]:
        reasons = []
        if not self._providers:
            raise LLMUnavailableError(["No supported provider has both a key and model configured"])
        for provider, model, client in self._providers:
            conversation = [
                {
                    "role": "system",
                    "content": "Return only a JSON object matching this schema: "
                    + self._redact(str(schema.model_json_schema())),
                },
                *(dict(message) for message in messages),
            ]
            for attempt in range(2):
                try:
                    response = client.chat.completions.create(model=model, messages=conversation)
                except APIError as exc:
                    reason = f"{provider}: {type(exc).__name__}"
                    status = getattr(exc, "status_code", None)
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
            for _, _, client in self._providers:
                client.close()
