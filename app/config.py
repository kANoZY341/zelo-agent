"""Application configuration loaded from the environment and local .env file."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Environment variables use the ZELO_ prefix; environment overrides .env."""

    model_config = SettingsConfigDict(
        env_prefix="ZELO_",
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="Zelo Agent", min_length=1)
    operator_token: SecretStr | None = None
    runtime_dir: Path = ROOT / "runtime"
    max_waiting_tasks: int = Field(default=100, ge=1, le=1000)
    approval_ttl_seconds: float = Field(default=3600, ge=0.05, le=86400)
    runner_poll_seconds: float = Field(default=0.05, ge=0.01, le=1)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_general_model: str = "qwen3:4b-instruct"
    ollama_reasoning_model: str = "deepseek-r1:7b"
    model_config_version: str = "1"
    model_timeout_seconds: float = Field(default=60.0, ge=0.01, le=600)

    @field_validator("ollama_url")
    @classmethod
    def validate_ollama_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Ollama URL must be localhost HTTP")
        return value.rstrip("/")

    @field_validator("operator_token")
    @classmethod
    def validate_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if (
                not 32 <= len(token) <= 512
                or not token.isascii()
                or any(c.isspace() for c in token)
            ):
                raise ValueError(
                    "Operator token must contain 32–512 non-whitespace ASCII characters"
                )
            if token == "REPLACE_WITH_A_RANDOM_LOCAL_OPERATOR_TOKEN":
                raise ValueError("Replace the example operator token before starting Zelo")
        return value

    @field_validator("runtime_dir")
    @classmethod
    def validate_runtime_dir(cls, value: Path) -> Path:
        resolved = (ROOT / value).resolve() if not value.is_absolute() else value.resolve()
        if not resolved.is_relative_to(ROOT / "runtime"):
            raise ValueError("Runtime directory must be inside the repository's runtime/ directory")
        return resolved

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "zelo.sqlite3"
