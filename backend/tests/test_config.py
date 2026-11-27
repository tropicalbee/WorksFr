import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from pydantic import ValidationError

from app.config import ProviderConfig, Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("VIDEO_READER_MODEL", raising=False)


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.llm_providers == []
    assert s.providers == []
    assert s.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert s.database_path == "worksfr.db"
    assert s.sandbox_image == "worksfr-sandbox"
    assert s.step_timeout_seconds == 60
    assert s.check_timeout_seconds == 900
    assert s.sandbox_memory == "2g"
    assert s.sandbox_tmpfs_mb == 512
    assert s.sandbox_file_size_mb == 256
    assert s.sandbox_cpus == 2.0
    assert s.sandbox_pids_limit == 256
    assert s.tavily_api_key.get_secret_value() == ""


def test_sandbox_storage_limits_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TMPFS_MB", "600")
    monkeypatch.setenv("SANDBOX_FILE_SIZE_MB", "300")
    settings = Settings(_env_file=None)
    assert settings.sandbox_tmpfs_mb == 600
    assert settings.sandbox_file_size_mb == 300


def test_comma_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", " first , ,second,")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    s = Settings(_env_file=None)
    assert s.llm_providers == ["first", "second"]
    assert s.cors_origins == ["http://a.test", "http://b.test"]


def test_empty_env_value_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "")
    assert Settings(_env_file=None).llm_providers == []


def test_secrets_hidden_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "local")
    monkeypatch.setenv("LLM_LOCAL_BASE_URL", "https://local.test/v1")
    monkeypatch.setenv("LLM_LOCAL_API_KEY", "secret-local")
    monkeypatch.setenv("LLM_LOCAL_MODEL", "test-model")
    monkeypatch.setenv("TAVILY_API_KEY", "secret-tv")
    s = Settings(_env_file=None)
    assert s.providers[0].api_key.get_secret_value() == "secret-local"
    text = repr(s) + str(s) + repr(s.providers)
    for secret in ("secret-local", "secret-tv"):
        assert secret not in text


def test_env_order_missing_and_hyphenated_provider(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "fourth-one,missing,local,missing")
    for name in ("FOURTH_ONE", "LOCAL"):
        monkeypatch.setenv(f"LLM_{name}_BASE_URL", "https://mock.test/v1")
        monkeypatch.setenv(f"LLM_{name}_API_KEY", "private-key")
        monkeypatch.setenv(f"LLM_{name}_MODEL", "model-value")
    monkeypatch.setenv("LLM_MISSING_BASE_URL", "https://hidden.test")
    settings = Settings(_env_file=None)
    assert [item.name for item in settings.providers] == ["fourth-one", "local"]
    assert caplog.text.count("Skipping LLM provider missing:") == 1
    assert "api_key" in caplog.text and "model" in caplog.text
    for secret in ("private-key", "model-value", "hidden.test"):
        assert secret not in caplog.text


def test_dotenv_and_environment_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "LLM_PROVIDERS=local\nLLM_LOCAL_BASE_URL=https://mock.test/v1\n"
        "LLM_LOCAL_API_KEY=file-key\nLLM_LOCAL_MODEL=file-model\n",
        encoding="utf-8",
    )
    assert Settings(_env_file=dotenv).providers[0].model == "file-model"
    monkeypatch.setenv("LLM_LOCAL_MODEL", "env-model")
    settings = Settings(_env_file=dotenv)
    assert settings.providers[0].model == "env-model"
    assert settings.providers[0].api_key.get_secret_value() == "file-key"


@pytest.mark.parametrize("interval", [None, "2.5"])
def test_optional_provider_interval(monkeypatch: pytest.MonkeyPatch, interval: str | None) -> None:
    monkeypatch.setenv("LLM_PROVIDERS", "local")
    monkeypatch.setenv("LLM_LOCAL_BASE_URL", "https://mock.test/v1")
    monkeypatch.setenv("LLM_LOCAL_API_KEY", "key")
    monkeypatch.setenv("LLM_LOCAL_MODEL", "model")
    monkeypatch.delenv("LLM_LOCAL_MIN_INTERVAL_SECONDS", raising=False)
    if interval is not None:
        monkeypatch.setenv("LLM_LOCAL_MIN_INTERVAL_SECONDS", interval)
    settings = Settings(_env_file=None)
    assert len(settings.providers) == 1
    assert settings.providers[0].min_interval_seconds == (float(interval) if interval else 0)
    assert settings.llm_max_wait_seconds == 20


def test_maximum_wait_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_WAIT_SECONDS", "7.5")
    assert Settings(_env_file=None).llm_max_wait_seconds == 7.5


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_wait_settings_reject_invalid_values(value: float) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_max_wait_seconds=value)
    with pytest.raises(ValueError):
        ProviderConfig(
            name="local",
            base_url="https://mock.test",
            api_key="x",
            model="x",
            min_interval_seconds=value,
        )


def test_video_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.video_reader_base_url == ""
    assert settings.video_reader_api_key.get_secret_value() == ""
    assert settings.video_reader_model == ""
    assert settings.video_reader_models == []
    assert settings.video_chunk_seconds == 300
    assert settings.video_max_minutes == 60
    assert settings.video_reader_min_interval_seconds == 0


def test_video_settings_environment_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for field, value in {
        "VIDEO_READER_BASE_URL": "https://video.test/v1beta",
        "VIDEO_READER_API_KEY": "video-secret-key",
        "VIDEO_READER_MODEL": "video-model",
        "VIDEO_CHUNK_SECONDS": "120",
        "VIDEO_MAX_MINUTES": "45",
        "VIDEO_READER_MIN_INTERVAL_SECONDS": "2.5",
    }.items():
        monkeypatch.setenv(field, value)
    settings = Settings(_env_file=None)
    assert settings.video_reader_base_url == "https://video.test/v1beta"
    assert settings.video_reader_api_key.get_secret_value() == "video-secret-key"
    assert settings.video_reader_model == "video-model"
    assert settings.video_reader_models == ["video-model"]
    assert settings.video_chunk_seconds == 120
    assert settings.video_max_minutes == 45
    assert settings.video_reader_min_interval_seconds == 2.5
    assert "video-secret-key" not in repr(settings) + str(settings)


def test_video_models_parse_in_fallback_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_READER_MODEL", " primary , , fallback, primary ,")
    settings = Settings(_env_file=None)
    assert settings.video_reader_models == ["primary", "fallback"]
    assert settings.video_reader_model == "primary"


def test_video_model_setting_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("VIDEO_READER_MODEL=file-first,file-second\n", encoding="utf-8")
    assert Settings(_env_file=dotenv).video_reader_models == ["file-first", "file-second"]

    monkeypatch.setenv("VIDEO_READER_MODEL", "env-model")
    assert Settings(_env_file=dotenv).video_reader_models == ["env-model"]
    assert Settings(_env_file=dotenv, video_reader_model="legacy-init").video_reader_models == [
        "legacy-init"
    ]
    assert Settings(_env_file=dotenv, video_reader_models=["init-model"]).video_reader_models == [
        "init-model"
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("video_chunk_seconds", 0),
        ("video_max_minutes", -1),
        ("video_reader_min_interval_seconds", -1),
        ("video_reader_min_interval_seconds", float("inf")),
        ("video_reader_min_interval_seconds", float("nan")),
    ],
)
def test_video_settings_reject_invalid_limits(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


@pytest.fixture
def capture_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    from scripts import capture_video_reply

    monkeypatch.setattr(capture_video_reply, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        capture_video_reply,
        "Settings",
        lambda: Settings(
            _env_file=None,
            video_reader_base_url="https://video.test/v1beta/",
            video_reader_api_key="video-secret-key",
            video_reader_model="video-model",
        ),
    )
    return capture_video_reply


def mock_video_http(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    responder: Callable[[httpx.Request], httpx.Response],
) -> None:
    client_type = httpx.Client
    transport = httpx.MockTransport(responder)

    def client(**kwargs: object) -> httpx.Client:
        return client_type(transport=transport, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", client)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
    ],
)
def test_capture_video_saves_reply_and_safe_request(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    url: str,
) -> None:
    reply = b'{ "id": "capture", "outputs": [] }\n'
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://video.test/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == "video-secret-key"
        return httpx.Response(200, content=reply)

    mock_video_http(monkeypatch, capture_module, respond)
    assert capture_module.main([url, "--start", "10", "--end", "20"]) == 0
    assert len(requests) == 1
    response_path = tmp_path / "dQw4w9WgXcQ_10-20.json"
    request_path = tmp_path / "dQw4w9WgXcQ_10-20.request.json"
    assert response_path.read_bytes() == reply
    body = json.loads(request_path.read_text(encoding="utf-8"))
    assert body == json.loads(requests[0].content)
    assert body["model"] == "video-model"
    assert body["input"][0]["type"] == "text"
    assert "partial" in body["input"][0]["text"]
    assert body["input"][1] == {
        "type": "video",
        "uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "processing": {"type": "static", "start_offset": "10s", "end_offset": "20s"},
    }
    output = capsys.readouterr().out
    assert "200" in output
    assert str(response_path) in output
    assert "video-secret-key" not in output
    assert all(b"video-secret-key" not in path.read_bytes() for path in tmp_path.iterdir())


def test_capture_video_non_200_writes_nothing(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, text='{"error": {"status": "INVALID_ARGUMENT"}}')

    mock_video_http(monkeypatch, capture_module, respond)
    assert capture_module.main(["https://youtu.be/dQw4w9WgXcQ"]) == 1
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []
    output = capsys.readouterr().out
    assert "400" in output and "INVALID_ARGUMENT" in output


@pytest.mark.parametrize(
    "url",
    [
        "dQw4w9WgXcQ",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        "https://youtu.be/dQw4w9WgXcQ/extra",
        "ftp://youtu.be/dQw4w9WgXcQ",
    ],
)
def test_capture_video_rejects_bad_url(capture_module: ModuleType, url: str) -> None:
    assert capture_module.main([url]) == 2


@pytest.mark.parametrize("missing", ["base_url", "api_key", "model"])
def test_capture_video_missing_config_exits_two(
    capture_module: ModuleType, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values = {
        "video_reader_base_url": "https://video.test/v1beta",
        "video_reader_api_key": "video-secret-key",
        "video_reader_model": "video-model",
    }
    values[f"video_reader_{missing}"] = ""
    monkeypatch.setattr(capture_module, "Settings", lambda: Settings(_env_file=None, **values))
    assert capture_module.main(["https://youtu.be/dQw4w9WgXcQ"]) == 2


def test_capture_video_transport_failure_hides_secret(
    capture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed with video-secret-key", request=request)

    mock_video_http(monkeypatch, capture_module, respond)
    assert capture_module.main(["https://youtu.be/dQw4w9WgXcQ"]) == 1
    assert list(tmp_path.iterdir()) == []
    output = capsys.readouterr()
    assert "video-secret-key" not in output.out + output.err


@pytest.mark.parametrize("gpus", ["all; rm", "-1", "--privileged", "0", "device=0,"])
def test_invalid_sandbox_gpus(gpus: str) -> None:
    with pytest.raises(ValidationError, match="sandbox_gpus"):
        Settings(_env_file=None, sandbox_gpus=gpus)
