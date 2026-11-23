from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.llm_providers == []
    assert s.providers == []
    assert s.cors_origins == ["http://localhost:5173"]
    assert s.database_path == "worksfr.db"
    assert s.sandbox_image == "worksfr-sandbox"
    assert s.step_timeout_seconds == 60
    assert s.check_timeout_seconds == 900
    assert s.sandbox_memory == "2g"
    assert s.sandbox_cpus == 2.0
    assert s.sandbox_pids_limit == 256
    assert s.tavily_api_key.get_secret_value() == ""


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
