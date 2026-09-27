"""Voice service configuration. All model/runtime paths stay local."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZELO_VOICE_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    stt_provider: str = "faster-whisper"
    stt_model: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_download_root: Path = (
        ROOT / "runtime" / "voice" / "models" / "faster-whisper"
    )
    tts_provider: str = "sherpa-supertonic"
    tts_model_dir: Path = (
        ROOT
        / "runtime"
        / "voice"
        / "models"
        / "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
    )
    tts_speaker_id: int = Field(default=0, ge=0, le=255)
    tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    max_audio_bytes: int = Field(default=20_000_000, ge=1024, le=50_000_000)
    max_text_chars: int = Field(default=8000, ge=1, le=32000)

    @field_validator("stt_download_root", "tts_model_dir")
    @classmethod
    def local_model_path(cls, value: Path) -> Path:
        resolved = (ROOT / value).resolve() if not value.is_absolute() else value.resolve()
        voice_root = (ROOT / "runtime" / "voice").resolve()
        if not resolved.is_relative_to(voice_root):
            raise ValueError("Voice model paths must stay inside runtime/voice")
        return resolved

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost"}:
            raise ValueError("Voice service must bind to loopback only")
        return value

    @field_validator("stt_provider")
    @classmethod
    def validate_stt_provider(cls, value: str) -> str:
        if value not in {"faster-whisper"}:
            raise ValueError("Unsupported STT provider")
        return value

    @field_validator("tts_provider")
    @classmethod
    def validate_tts_provider(cls, value: str) -> str:
        if value not in {"sherpa-supertonic"}:
            raise ValueError("Unsupported TTS provider")
        return value
