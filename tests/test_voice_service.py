from fastapi.testclient import TestClient

from voice_service.app import create_voice_app
from voice_service.config import VoiceSettings
from voice_service.providers import Transcription

VOICE_TOKEN = "voice-test-token-" + ("x" * 32)
AUTH = {"Authorization": f"Bearer {VOICE_TOKEN}"}


class FakeSTT:
    def transcribe(self, audio: bytes, media_type: str | None) -> Transcription:
        assert audio == b"audio"
        assert media_type == "audio/webm"
        return Transcription(text="hello zelo", language="en")


class FakeTTS:
    def synthesize(self, text: str, language: str) -> bytes:
        assert text == "hello back"
        assert language == "en"
        return b"RIFFfake"


def settings():
    return VoiceSettings(
        _env_file=None,
        service_token=VOICE_TOKEN,
        max_audio_bytes=1024,
    )


def test_voice_service_requires_proxy_token_before_work(runtime_path):
    client = TestClient(
        create_voice_app(settings(), stt=FakeSTT(), tts=FakeTTS())
    )
    assert client.get("/health").status_code == 401
    response = client.post(
        "/transcribe",
        content=b"audio",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 401


def test_voice_service_health_and_provider_identity(runtime_path):
    client = TestClient(
        create_voice_app(settings(), stt=FakeSTT(), tts=FakeTTS())
    )
    assert client.get("/health", headers=AUTH).json() == {
        "status": "healthy",
        "stt": "faster-whisper",
        "tts": "sherpa-supertonic",
    }


def test_transcribe_is_typed_stream_bounded_and_returns_language(runtime_path):
    client = TestClient(
        create_voice_app(settings(), stt=FakeSTT(), tts=FakeTTS())
    )
    response = client.post(
        "/transcribe",
        content=b"audio",
        headers={**AUTH, "Content-Type": "audio/webm"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "hello zelo", "language": "en"}

    wrong_type = client.post(
        "/transcribe",
        content=b"audio",
        headers={**AUTH, "Content-Type": "text/plain"},
    )
    assert wrong_type.status_code == 415

    empty = client.post(
        "/transcribe",
        content=b"",
        headers={**AUTH, "Content-Type": "audio/webm"},
    )
    assert empty.status_code == 400

    oversized = client.post(
        "/transcribe",
        content=b"x" * 1025,
        headers={**AUTH, "Content-Type": "audio/webm"},
    )
    assert oversized.status_code == 413


def test_synthesize_returns_wav_bytes(runtime_path):
    client = TestClient(
        create_voice_app(settings(), stt=FakeSTT(), tts=FakeTTS())
    )
    response = client.post(
        "/synthesize",
        json={"text": "hello back", "language": "en"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == b"RIFFfake"


def test_voice_service_rejects_non_loopback_binding():
    try:
        VoiceSettings(
            _env_file=None,
            service_token=VOICE_TOKEN,
            host="0.0.0.0",
        )
    except ValueError as error:
        assert "loopback" in str(error).lower()
    else:
        raise AssertionError("non-loopback voice bind must fail")


def test_voice_service_rejects_external_model_paths(tmp_path):
    try:
        VoiceSettings(
            _env_file=None,
            service_token=VOICE_TOKEN,
            stt_download_root=tmp_path / "outside",
        )
    except ValueError as error:
        assert "runtime/voice" in str(error)
    else:
        raise AssertionError("voice model path outside runtime/voice must fail")
