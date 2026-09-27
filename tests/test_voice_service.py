"""Voice adapter contract tests without loading local speech models."""

from __future__ import annotations

from fastapi.testclient import TestClient

import voice_service.app as voice_app
from voice_service.providers import audio_suffix, detect_tts_language

TOKEN = "test-only-voice-token"


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
