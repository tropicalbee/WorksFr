import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict

from app.packages import normalize

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
    video_reader_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("video_reader_model", "video_reader_models"),
    )
    video_chunk_seconds: int = Field(default=300, ge=1)
    video_max_minutes: int = Field(default=60, ge=1)
    video_reads: int = Field(default=2, ge=1, le=3)
    video_debug_dir: str = ""
    video_download: bool = False
    video_download_max_mb: int = Field(default=1024, ge=1)
    video_download_timeout_seconds: int = Field(default=600, ge=1)
    video_tools_image: str = "worksfr-video-tools"
    video_tools_memory: str = "512m"
    video_tools_cpus: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    video_tools_pids_limit: int = Field(default=128, ge=1)
    video_tools_tmpfs_mb: int = Field(default=64, ge=1)
    video_reader_min_interval_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)

    database_path: str = "worksfr.db"
    sandbox_image: str = "worksfr-sandbox"
    sandbox_gpus: str = ""
    sandbox_gpu_image: str = ""
    step_timeout_seconds: int = 60
    check_timeout_seconds: int = 900
    sandbox_memory: str = "2g"
    sandbox_tmpfs_mb: int = Field(default=512, ge=1)
    sandbox_file_size_mb: int = Field(default=256, ge=1)
    sandbox_cpus: float = 2.0
    sandbox_pids_limit: int = 256
    sandbox_max_package_mb: int = Field(default=400, ge=1)
    sandbox_package_indexes: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict, repr=False
    )
    max_concurrent_checks: int = Field(default=1, ge=1)
    max_upload_mb: int = Field(default=10, ge=1)
    max_steps: int = Field(default=150, ge=1)

    worksfr_api_url: str = "http://127.0.0.1:8000"
    api_token: SecretStr = SecretStr("")

    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

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

    @field_validator(
        "llm_providers", "reader_providers", "cors_origins", "video_reader_models", mode="before"
    )
    @classmethod
    def _parse_csv(cls, value: Any) -> Any:
        return _split_csv(value)

    @field_validator("sandbox_gpus")
    @classmethod
    def _validate_sandbox_gpus(cls, value: str) -> str:
        if value and not re.fullmatch(r"all|[1-9][0-9]*|device=[0-9]+(?:,[0-9]+)*", value):
            raise ValueError("use all, a positive count, or device= followed by numeric IDs")
        return value

    @field_validator("sandbox_package_indexes", mode="before")
    @classmethod
    def _parse_package_indexes(cls, value: Any) -> dict[str, str]:
        entries = (
            value.items()
            if isinstance(value, dict)
            else ((entry.partition("=")[0], entry.partition("=")[2]) for entry in _split_csv(value))
        )
        indexes: dict[str, str] = {}
        for name, url in entries:
            name, url = str(name).strip(), str(url).strip()
            try:
                parsed = urlsplit(url)
                valid = (
                    parsed.scheme in ("http", "https") and parsed.hostname and not parsed.fragment
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if valid and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
                indexes[normalize(name)] = url
            else:
                logger.warning("Ignoring malformed sandbox package index entry")
        return indexes

    @field_validator("video_reader_models")
    @classmethod
    def _unique_video_models(cls, models: list[str]) -> list[str]:
        return list(dict.fromkeys(models))

    @property
    def video_reader_model(self) -> str:
        """Primary model for callers that make a single video reader request."""
        return self.video_reader_models[0] if self.video_reader_models else ""

    @video_reader_model.setter
    def video_reader_model(self, value: str) -> None:
        self.video_reader_models = self._unique_video_models(_split_csv(value))


@lru_cache
def get_settings() -> Settings:
    return Settings()
