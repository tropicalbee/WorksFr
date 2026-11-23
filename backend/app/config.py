import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
logger = logging.getLogger(__name__)


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key: SecretStr
    model: str


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    llm_providers: Annotated[list[str], NoDecode] = []
    providers: list[ProviderConfig] = Field(default_factory=list)
    tavily_api_key: SecretStr = SecretStr("")

    database_path: str = "worksfr.db"
    sandbox_image: str = "worksfr-sandbox"
    step_timeout_seconds: int = 60
    check_timeout_seconds: int = 900
    sandbox_memory: str = "2g"
    sandbox_cpus: float = 2.0
    sandbox_pids_limit: int = 256

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        def provider_settings() -> dict[str, Any]:
            initial = init_settings()
            if "providers" in initial:
                return {}
            normal = {**dotenv_settings(), **env_settings(), **initial}
            names = _split_csv(normal.get("llm_providers", []))
            variables = {**dotenv_settings.env_vars, **env_settings.env_vars}
            providers = []
            for name in dict.fromkeys(names):
                prefix = "llm_" + name.replace("-", "_").lower() + "_"
                values = {
                    field: variables.get(prefix + field)
                    for field in ("base_url", "api_key", "model")
                }
                missing = [
                    field for field, value in values.items() if not value or not value.strip()
                ]
                if missing:
                    logger.warning("Skipping LLM provider %s: missing %s", name, ", ".join(missing))
                else:
                    providers.append(ProviderConfig(name=name, **values))
            return {"providers": providers}

        return init_settings, env_settings, dotenv_settings, provider_settings, file_secret_settings

    @field_validator("llm_providers", "cors_origins", mode="before")
    @classmethod
    def _parse_csv(cls, value: Any) -> Any:
        return _split_csv(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
