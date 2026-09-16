"""Application configuration loaded from the environment and local .env file."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment variables use the ZELO_ prefix; environment overrides .env."""

    model_config = SettingsConfigDict(
        env_prefix="ZELO_",
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Zelo Agent", min_length=1)
