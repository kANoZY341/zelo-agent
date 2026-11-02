"""Core and Voice reject unauthorized or oversized JSON before parsing it."""

from fastapi.testclient import TestClient

import voice_service.app as voice_app

VOICE_TOKEN = "test-only-voice-token-0123456789abcdef"


def test_core_rejects_malformed_json_before_authentication(client):
    response = client.post(
        "/tasks",
        content=b"{",
        headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Valid operator bearer token required"}


def test_core_rejects_oversized_json_before_parsing(client):
    response = client.post(
        "/tasks",
        content=b"{" + b" " * (16 * 1024 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "request_body_too_large"}


def test_voice_rejects_malformed_synthesis_json_before_authentication(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", VOICE_TOKEN)
    with TestClient(voice_app.app) as client:
        response = client.post(
            "/synthesize",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "voice_unauthorized"}


def test_voice_rejects_streamed_oversized_synthesis_json(monkeypatch):
    monkeypatch.setenv("ZELO_VOICE_TOKEN", VOICE_TOKEN)
    with TestClient(voice_app.app) as client:
        response = client.post(
            "/synthesize",
            content=iter((b'{"text":"', b"x" * (64 * 1024), b'"}')),
            headers={"Authorization": f"Bearer {VOICE_TOKEN}", "Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "voice_synthesis_body_too_large"}
