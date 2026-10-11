from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden with an environment variable."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://datarescue:datarescue@localhost:5432/datarescue"
    redis_url: str = "redis://localhost:6379/0"

    # "rq" enqueues to Redis; "sync" runs tasks inline (tests and the seed script).
    task_mode: Literal["rq", "sync"] = "rq"
    task_max_retries: int = 3

    # HS256 needs >= 32 bytes. The default is for local development only; production
    # startup refuses it (see _require_real_secret).
    jwt_secret: str = Field(default="dev-only-insecure-secret-change-me-0000", min_length=32)
    jwt_ttl_minutes: int = 60
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    data_dir: Path = Path("/data")
    max_upload_bytes: int = 50 * 1024 * 1024
    ingestion_chunk_size: int = 1000
    max_generated_records: int = 200_000

    # Contract thresholds live in quality_rules.configuration and source_systems rows.
    freshness_check_interval_seconds: int = 300
    stale_job_timeout_seconds: int = 300

    recovery_max_targets: int = 10_000
    rate_limit_per_minute: int = 20

    # Optional LLM enrichment; disabled unless both flag and key are set.
    llm_enabled: bool = False
    llm_provider: Literal["anthropic"] = "anthropic"
    llm_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 16000
    llm_max_input_chars: int = 12_000

    # Test-only hook used by the chaos tests to crash a recovery mid-flight.
    chaos_enabled: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _require_real_secret(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret.startswith("dev-only"):
            raise ValueError("JWT_SECRET must be set to a random value in production")
        return self

    @property
    def llm_active(self) -> bool:
        return self.llm_enabled and bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
