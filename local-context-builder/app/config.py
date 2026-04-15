"""Application settings loaded from environment variables.

All secrets (Kakao API key, DB URLs, admin key) MUST come from the
environment. Never hardcode them. ``.env.example`` documents the
expected keys 1:1 with the fields in :class:`Settings`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for local-context-builder."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Batch service DB (read/write, owned by this service) ---
    database_url: str

    # --- Real-service DB (read-only, optional until wired up) ---
    realservice_database_url: Optional[str] = None
    realservice_statement_timeout_ms: int = 30000

    # --- Celery broker / result backend ---
    redis_url: str

    # --- External APIs ---
    kakao_rest_api_key: str

    # --- Admin API ---
    admin_api_key: str

    # --- Target region for batch run ---
    target_city: str = "suwon"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Using a cached accessor avoids re-parsing ``.env`` on every call and
    lets tests override settings by clearing the cache.
    """

    return Settings()  # type: ignore[call-arg]
