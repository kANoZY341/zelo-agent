"""Public endpoint contracts and settings integration."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_root() -> None:
    settings = Settings(_env_file=None, app_name="Zelo Agent")
    with TestClient(create_app(settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Zelo Agent",
        "version": "0.1.0",
        "status": "online",
    }


def test_health() -> None:
    settings = Settings(_env_file=None, app_name="Zelo Agent")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_environment_configures_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZELO_APP_NAME", "Zelo Test")
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["name"] == "Zelo Test"
