"""Opt-in provider checks. Ordinary pytest runs never send these requests."""

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import LLMClient, LLMUnavailableError

settings = Settings()


class Ping(BaseModel):
    ok: bool
    word: str


@pytest.mark.llm
@pytest.mark.parametrize("name", settings.llm_providers, ids=settings.llm_providers)
def test_provider_returns_valid_json(name: str) -> None:
    provider = next((item for item in settings.providers if item.name == name), None)
    if provider is None:
        pytest.skip(f"{name}: base URL, key or model missing in backend/.env")
    client = None
    try:
        client = LLMClient(settings.model_copy(update={"providers": [provider]}))
        result = client.complete_json(
            [{"role": "user", "content": "Reply with ok true and the word pong"}], Ping
        )
        valid = result.value.ok is True and result.provider == provider.name
    except LLMUnavailableError as exc:
        pytest.fail("; ".join(exc.reasons), pytrace=False)
    except Exception as exc:
        # Never let SDK exceptions or chained tracebacks expose provider credentials.
        pytest.fail(f"{name}: {type(exc).__name__}", pytrace=False)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                pytest.fail(f"{name}: {type(exc).__name__}", pytrace=False)
    if not valid:
        pytest.fail(name, pytrace=False)
