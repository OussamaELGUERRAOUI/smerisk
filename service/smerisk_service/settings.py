"""Configuration from environment variables (prefix ``SMERISK_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMERISK_")

    api_tokens: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)
    """Accepted bearer tokens, comma-separated (``SMERISK_API_TOKENS``). Each caller has its own token: jobs and
    datasets are only visible to the token that created them."""
    allow_anonymous: bool = False
    """Accept calls without a token (local development only); they all share one anonymous caller."""
    max_body_bytes: int = Field(50 * 1024 * 1024, gt=0)
    """Requests above this size, as sent (compressed or not), are rejected with 413."""
    max_decompressed_bytes: int = Field(200 * 1024 * 1024, gt=0)
    """A gzip body above this size once decompressed is rejected with 413 (protection against compression bombs)."""
    max_dataset_bytes: int = Field(500 * 1024 * 1024, gt=0)
    """Total size of a dataset, all pages together; above, 413."""
    result_ttl_seconds: int = Field(24 * 3600, gt=0)
    """Retention of job results and datasets (§2.3, §12.5)."""
    job_workers: int = Field(2, ge=1)
    """Calculations running at the same time; the others wait in the queue."""
    max_jobs_per_caller: int = Field(10, ge=1)
    """Jobs pending or running per caller; above, 429."""
    max_datasets_per_caller: int = Field(20, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    """One JSON object per line (for a log collector), or readable text (development)."""
    dataset_key: SecretStr | None = None
    """Fernet key that encrypts datasets in memory; generated at start when absent (datasets do not survive a restart
    anyway)."""

    @field_validator("api_tokens", mode="before")
    @classmethod
    def _split(cls, value: Any) -> Any:
        return [t.strip() for t in value.split(",") if t.strip()] if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
