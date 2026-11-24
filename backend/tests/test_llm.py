import json
import logging
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

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


@pytest.mark.parametrize("status", [500, 401, 404, 400, 403, 422])
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


def test_provider_defined_only_in_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "local")
    monkeypatch.setenv("LLM_LOCAL_BASE_URL", "https://local.test/v1")
    monkeypatch.setenv("LLM_LOCAL_API_KEY", "local-secret")
    monkeypatch.setenv("LLM_LOCAL_MODEL", "local-model")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "local.test"
        assert json.loads(request.content)["model"] == "local-model"
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = LLMClient(Settings(_env_file=None), http).complete_json(MESSAGES, RepairProposal)
        assert result.provider == "local"


def test_returned_provider_is_not_mutable_client_state(settings: Settings) -> None:
    responses: Iterator[httpx.Response] = iter(
        [completion(VALID), *(httpx.Response(500, json={"error": "unavailable"}) for _ in range(3))]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as http:
        client = LLMClient(settings, http)
        client.complete_json(MESSAGES, RepairProposal)
        assert not hasattr(client, "last_provider")
        with pytest.raises(LLMUnavailableError):
            client.complete_json(MESSAGES, RepairProposal)


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


def test_minimum_provider_interval_and_per_call_notice(settings: Settings) -> None:
    settings.providers[0].min_interval_seconds = 3
    clock = Clock()
    notices = []
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(clock.monotonic())
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        client.complete_json(MESSAGES, RepairProposal)
        client.complete_json(MESSAGES, RepairProposal, on_wait=lambda *args: notices.append(args))
    assert sent == [0, 3]
    assert clock.sleeps == [3]
    assert notices == [("first", 3)]


@pytest.mark.parametrize("header,wait", [("3", 3), (None, 5), ("bad", 5)])
def test_rate_limit_retries_same_provider_once(
    settings: Settings, header: str | None, wait: float
) -> None:
    calls = []
    clock = Clock()
    notices = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"Retry-After": header} if header else {}, json={"error": "limited"}
            )
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        result = client.complete_json(
            MESSAGES, RepairProposal, on_wait=lambda *args: notices.append(args)
        )
    assert result.provider == "first"
    assert calls == ["first.test", "first.test"]
    assert clock.sleeps == [wait]
    assert notices == [("first", wait)]


@pytest.mark.parametrize("header,expected_sleeps", [("60", []), ("3", [3])])
def test_excessive_or_repeated_rate_limit_falls_back(
    settings: Settings, header: str, expected_sleeps: list[float]
) -> None:
    clock = Clock()
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "first.test":
            return httpx.Response(429, headers={"Retry-After": header}, json={"error": "limited"})
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        assert client.complete_json(MESSAGES, RepairProposal).provider == "second"
    assert calls == ["first.test"] * (2 if expected_sleeps else 1) + ["second.test"]
    assert clock.sleeps == expected_sleeps


def test_rate_limit_budget_is_separate_from_json_retry(settings: Settings) -> None:
    clock = Clock()
    calls = []
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": "3"}, json={"error": "limited"}),
            completion("bad"),
            httpx.Response(429, headers={"Retry-After": "3"}, json={"error": "limited"}),
            completion(VALID),
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return next(replies)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        assert client.complete_json(MESSAGES, RepairProposal).provider == "second"
    assert calls == ["first.test"] * 3 + ["second.test"]
    assert clock.sleeps == [3]


def test_rate_limit_does_not_consume_json_validation_attempt(settings: Settings) -> None:
    clock = Clock()
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "limited"}),
            completion("bad"),
            completion(VALID),
        ]
    )
    notices = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        result = client.complete_json(
            MESSAGES, RepairProposal, on_wait=lambda *args: notices.append(args)
        )
    assert result.provider == "first"
    assert clock.sleeps == [1]
    assert notices == []


def test_http_date_retry_after(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("app.llm.client.time.time", lambda: now.timestamp())
    clock = Clock()
    replies = iter(
        [
            httpx.Response(
                429,
                headers={"Retry-After": format_datetime(now + timedelta(seconds=4), usegmt=True)},
                json={"error": "limited"},
            ),
            completion(VALID),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        assert client.complete_json(MESSAGES, RepairProposal).provider == "first"
    assert clock.sleeps == [4]


def test_two_threads_share_provider_pacing(settings: Settings) -> None:
    settings.providers[0].min_interval_seconds = 3
    clock = Clock()
    sent = []
    first_entered = threading.Event()
    second_sent = threading.Event()

    def respond(request: httpx.Request) -> httpx.Response:
        with clock.lock:
            sent.append(clock.now)
            index = len(sent)
        if index == 1:
            first_entered.set()
            assert second_sent.wait(timeout=5), "pacing lock must not cover the HTTP request"
        else:
            second_sent.set()
        return completion(VALID)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = LLMClient(settings, http, sleep=clock.sleep, monotonic=clock.monotonic)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.complete_json, MESSAGES, RepairProposal)
            assert first_entered.wait(timeout=5)
            second = pool.submit(client.complete_json, MESSAGES, RepairProposal)
            assert first.result(timeout=5).provider == "first"
            assert second.result(timeout=5).provider == "first"
    assert sent == [0, 3]
