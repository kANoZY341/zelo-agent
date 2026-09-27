from fastapi.testclient import TestClient

from voice_service.app import create_voice_app
from voice_service.config import VoiceSettings
from voice_service.providers import Transcription


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


def settings(tmp_path):
    return VoiceSettings(
        _env_file=None,
        tts_model_dir=tmp_path / "models" / "supertonic",
        max_audio_bytes=1024,
    )


def test_voice_service_health_and_provider_identity(tmp_path):
    client = TestClient(create_voice_app(settings(tmp_path), stt=FakeSTT(), tts=FakeTTS()))
    assert client.get("/health").json() == {
        "status": "healthy",
        "stt": "faster-whisper",
        "tts": "sherpa-supertonic",
    }


def test_transcribe_is_bounded_and_returns_language(tmp_path):
    client = TestClient(create_voice_app(settings(tmp_path), stt=FakeSTT(), tts=FakeTTS()))
    response = client.post(
        "/transcribe",
        content=b"audio",
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "hello zelo", "language": "en"}
    assert client.post("/transcribe", content=b"").status_code == 400
    assert client.post("/transcribe", content=b"x" * 1025).status_code == 413


def test_synthesize_returns_wav_bytes(tmp_path):
    client = TestClient(create_voice_app(settings(tmp_path), stt=FakeSTT(), tts=FakeTTS()))
    response = client.post("/synthesize", json={"text": "hello back", "language": "en"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == b"RIFFfake"


def test_voice_service_rejects_non_loopback_binding(tmp_path):
    try:
        VoiceSettings(_env_file=None, host="0.0.0.0", tts_model_dir=tmp_path)
    except ValueError as error:
        assert "loopback" in str(error).lower()
    else:
        raise AssertionError("non-loopback voice bind must fail")
