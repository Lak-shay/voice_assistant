"""Application Configuration Module

Loads environment variables dynamically based on APP_ENV (dev vs prod),
with fallback to .env and environment variable overrides via pydantic-settings.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_active_env_file() -> str:
    """Determine the active environment file path based on APP_ENV."""
    app_env = os.getenv("APP_ENV", os.getenv("ENV", "dev")).strip().lower()
    if app_env in ("prod", "production"):
        target_file = ".env.prod"
    else:
        target_file = ".env.dev"

    # Fall back to .env if specific environment file does not exist
    if not Path(target_file).exists() and Path(".env").exists():
        return ".env"
    return target_file


class Settings(BaseSettings):
    """Voice Assistant Configuration Settings."""

    APP_ENV: str = "dev"

    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""

    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = ""

    STARTUP_CHECK_TIMEOUT: float = 8.0

    model_config = SettingsConfigDict(
        env_file=get_active_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
