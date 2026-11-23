import json
import logging
from collections.abc import Iterator

import httpx
import pytest

from app.config import ProviderConfig, Settings
from app.llm import LLMClient, LLMUnavailableError
from app.models import RepairProposal


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(
        _env_file=None,
        providers=[
            ProviderConfig(
                name=name,
                base_url=f"https://{name}.test/v1",
                api_key=f"secret-{name}",
                model=f"{name}-model",
            )
            for name in ("first", "second", "third")
        ],
    )


def completion(content: str | None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


VALID = '{"patched_code":"x = 1","explanation":"fixed","apis_changed":[]}'
MESSAGES = [{"role": "user", "content": "Repair this step"}]


@pytest.mark.parametrize("status", [429, 500, 401, 404, 400, 403, 422])
def test_fallback_on_api_errors(settings: Settings, status: int) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        body = json.loads(request.content)
        assert "response_format" not in body
        assert body["messages"][0]["role"] == "system"
        if len(calls) == 1:
            return httpx.Response(status, json={"error": {"message": "unavailable"}})
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http)
        result = client.complete_json(MESSAGES, RepairProposal)
        assert result.value.patched_code == "x = 1"
        assert result.provider == "second"
        assert calls == ["first.test", "second.test"]


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_connection_fallback(settings: Settings, failure: type[httpx.RequestError]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "first.test":
            raise failure("offline", request=request)
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http)
        result = client.complete_json(MESSAGES, RepairProposal)
        assert result.provider == "second"


@pytest.mark.parametrize(
    "content", [VALID, f"```json\n{VALID}\n```", f"Here it is:\n{VALID}\nDone."]
)
def test_json_extraction(settings: Settings, content: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: completion(content))) as http:
        client = LLMClient(settings, http)
        assert client.complete_json(MESSAGES, RepairProposal).value.explanation == "fixed"
        assert not hasattr(client, "last_provider")


@pytest.mark.parametrize("bad", ["not JSON", '{"patched_code":7}', None])
def test_parse_retry_succeeds(settings: Settings, bad: str | None) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return completion(bad if len(requests) == 1 else VALID)

    original = [dict(message) for message in MESSAGES]
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http)
        client.complete_json(original, RepairProposal)
    assert len(requests) == 2
    assert requests[1]["messages"][-2] == {"role": "assistant", "content": bad or ""}
    assert requests[1]["messages"][-1]["role"] == "user"
    assert "error" in requests[1]["messages"][-1]["content"].lower()
    assert original == MESSAGES


def test_parse_failures_fall_through(settings: Settings) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return completion("bad" if len(calls) < 3 else VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http)
        result = client.complete_json(MESSAGES, RepairProposal)
        assert result.provider == "second"
    assert calls == ["first.test"] * 2 + ["second.test"]


def test_skips_unconfigured_and_preserves_order(settings: Settings) -> None:
    settings.providers = [settings.providers[2]]
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        assert json.loads(request.content)["model"] == settings.providers[0].model
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http)
        result = client.complete_json(MESSAGES, RepairProposal)
        assert result.provider == "third"
    assert calls == ["third.test"]


def test_all_fail_and_secrets_are_not_logged(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "secret-first secret-second secret-third"}}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http)
        with pytest.raises(LLMUnavailableError) as caught:
            client.complete_json(MESSAGES, RepairProposal)
        assert len(caught.value.reasons) == 3
        assert "first: AuthenticationError (401)" in caught.value.reasons
        for secret in ("secret-first", "secret-second", "secret-third"):
            assert secret not in caplog.text + str(caught.value)


def test_no_configured_provider_fails_at_call(settings: Settings) -> None:
    settings.providers = []
    client = LLMClient(settings)
    with pytest.raises(LLMUnavailableError, match="configured"):
        client.complete_json(MESSAGES, RepairProposal)


def test_returned_provider_is_not_mutable_client_state(settings: Settings) -> None:
    responses: Iterator[httpx.Response] = iter(
        [completion(VALID), *(httpx.Response(429, json={"error": "limited"}) for _ in range(3))]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as http:
        client = LLMClient(settings, http)
        client.complete_json(MESSAGES, RepairProposal)
        assert not hasattr(client, "last_provider")
        with pytest.raises(LLMUnavailableError):
            client.complete_json(MESSAGES, RepairProposal)
