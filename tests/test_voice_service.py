"""Voice adapter contract tests without loading local speech models."""

from __future__ import annotations

from fastapi.testclient import TestClient

import voice_service.app as voice_app
import voice_service.providers as providers
from voice_service.providers import audio_suffix, detect_tts_language, pcm16_wav

TOKEN = "test-only-voice-token-0123456789abcdef"


class FakeTranscriber:
    def transcribe(self, audio: bytes, content_type: str) -> dict[str, object]:
        assert audio == b"fake-audio"
        assert content_type == "audio/webm"
        return {
            "text": "مرحبا Zelo",
            "language": "ar",
            "language_probability": 0.99,
            "model": "fake",
        }


class FakeSynthesizer:
    def synthesize(self, text: str, language: str) -> bytes:
        assert text == "Hello from Zelo"
        assert language == "en"
        return b"RIFF-fake-wave"


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_voice_language_detection_and_audio_suffixes():
    assert detect_tts_language("Hello Zelo") == "en"
    assert detect_tts_language("مرحبا زيلو") == "ar"
    assert detect_tts_language("Hello مرحبا") == "en"
    assert audio_suffix("audio/webm; codecs=opus") == ".webm"
    assert audio_suffix("audio/mp4") == ".m4a"
    assert audio_suffix("application/octet-stream") == ".audio"


def test_voice_adapter_requires_separate_token(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", TOKEN)
    with TestClient(voice_app.app) as client:
        response = client.get("/health")
        assert response.status_code == 401
        assert response.json() == {"detail": "voice_unauthorized"}

        response = client.get("/health", headers=auth())
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


def test_transcribe_is_bounded_and_uses_provider(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", TOKEN)
    monkeypatch.setattr(voice_app, "get_transcriber", lambda: FakeTranscriber())

    with TestClient(voice_app.app) as client:
        empty = client.post(
            "/transcribe",
            headers={**auth(), "X-Audio-Type": "audio/webm"},
            content=b"",
        )
        assert empty.status_code == 400
        assert empty.json() == {"detail": "voice_audio_empty"}

        response = client.post(
            "/transcribe",
            headers={**auth(), "X-Audio-Type": "audio/webm"},
            content=b"fake-audio",
        )
        assert response.status_code == 200
        assert response.json()["text"] == "مرحبا Zelo"
        assert response.json()["language"] == "ar"


def test_synthesize_returns_wav_and_language_header(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", TOKEN)
    monkeypatch.setattr(voice_app, "get_synthesizer", lambda: FakeSynthesizer())

    with TestClient(voice_app.app) as client:
        response = client.post(
            "/synthesize",
            headers=auth(),
            json={"text": "Hello from Zelo", "language": "en"},
        )
        assert response.status_code == 200
        assert response.content == b"RIFF-fake-wave"
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["x-zelo-voice-language"] == "en"


def test_synthesize_rejects_unsupported_language_before_provider(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", TOKEN)
    called = False

    class RejectingSynthesizer:
        def synthesize(self, text: str, language: str) -> bytes:
            nonlocal called
            called = True
            raise AssertionError("provider should not be reached")

    monkeypatch.setattr(voice_app, "get_synthesizer", lambda: RejectingSynthesizer())

    with TestClient(voice_app.app) as client:
        response = client.post(
            "/synthesize",
            headers=auth(),
            json={"text": "Bonjour", "language": "fr"},
        )
        assert response.status_code == 422
        assert called is False


def test_voice_adapter_rejects_weak_token(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", "too-short")
    with TestClient(voice_app.app) as client:
        try:
            response = client.get(
                "/health",
                headers={"Authorization": "Bearer too-short"},
            )
        except RuntimeError as exc:
            assert "32-512" in str(exc)
        else:
            assert response.status_code >= 500


def test_pcm16_wav_encoder_returns_valid_wave():
    import wave
    from io import BytesIO

    encoded = pcm16_wav([-1.0, 0.0, 1.0], 16000)
    with wave.open(BytesIO(encoded), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() == 3


def test_tts_provider_is_replaceable_by_configuration(monkeypatch):
    original = providers._synthesizer
    try:
        chatterbox = object()
        monkeypatch.setenv("ZELO_TTS_PROVIDER", "chatterbox")
        monkeypatch.setattr(providers, "ChatterboxSynthesizer", lambda: chatterbox)
        providers._synthesizer = None
        assert providers.get_synthesizer() is chatterbox

        sherpa = object()
        monkeypatch.setenv("ZELO_TTS_PROVIDER", "sherpa_vits")
        monkeypatch.setattr(providers, "SherpaOnnxVitsSynthesizer", lambda: sherpa)
        providers._synthesizer = None
        assert providers.get_synthesizer() is sherpa

        monkeypatch.setenv("ZELO_TTS_PROVIDER", "not-real")
        providers._synthesizer = None
        try:
            providers.get_synthesizer()
        except RuntimeError as exc:
            assert str(exc) == "unsupported_tts_provider"
        else:
            raise AssertionError("unsupported provider should fail closed")
    finally:
        providers._synthesizer = original
