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
    min_interval_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


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
    llm_max_wait_seconds: float = Field(default=20, ge=0, allow_inf_nan=False)
    tavily_api_key: SecretStr = SecretStr("")
    youtube_api_key: SecretStr = SecretStr("")

    reader_providers: Annotated[list[str], NoDecode] = ["firecrawl", "jina"]
    firecrawl_api_key: SecretStr = SecretStr("")
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    jina_api_key: SecretStr = SecretStr("")
    jina_reader_base_url: str = "https://r.jina.ai/"

    video_reader_base_url: str = ""
    video_reader_api_key: SecretStr = SecretStr("")
    video_reader_model: str = ""
    video_chunk_seconds: int = Field(default=300, ge=1)
    video_max_minutes: int = Field(default=60, ge=1)
    video_reads: int = Field(default=2, ge=1, le=3)
    video_reader_min_interval_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)

    database_path: str = "worksfr.db"
    sandbox_image: str = "worksfr-sandbox"
    step_timeout_seconds: int = 60
    check_timeout_seconds: int = 900
    sandbox_memory: str = "2g"
    sandbox_cpus: float = 2.0
    sandbox_pids_limit: int = 256
    max_concurrent_checks: int = Field(default=1, ge=1)
    max_upload_mb: int = Field(default=10, ge=1)
    max_steps: int = Field(default=150, ge=1)

    worksfr_api_url: str = "http://127.0.0.1:8000"

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
                    providers.append(
                        ProviderConfig(
                            name=name,
                            **values,
                            min_interval_seconds=variables.get(prefix + "min_interval_seconds")
                            or 0,
                        )
                    )
            return {"providers": providers}

        return init_settings, env_settings, dotenv_settings, provider_settings, file_secret_settings

    @field_validator("llm_providers", "reader_providers", "cors_origins", mode="before")
    @classmethod
    def _parse_csv(cls, value: Any) -> Any:
        return _split_csv(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
