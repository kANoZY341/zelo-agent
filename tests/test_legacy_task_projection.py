"""Historical terminal Task projections remain readable without granting old authority."""

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.models import Task


@pytest.mark.parametrize("status", ["failed", "succeeded"])
def test_terminal_legacy_developer_task_remains_readable(
    client: TestClient, database: Database, status: str
) -> None:
    legacy_spec = {
        "instruction": "Historical task",
        "worker": {"implementation": "codex", "auth_location": "/private/old-auth"},
    }
    with database.transaction() as session:
        task = Task(
            kind="developer_run",
            payload={"instruction": "Historical task", "validation_profile": "zelo_core"},
            timeout_seconds=30,
            status=status,
            action_version=1,
            action_hash="a" * 64,
            request_hash="b" * 64,
            developer_spec=legacy_spec,
        )
        session.add(task)
        session.flush()
        task_id = task.id

    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["developer_spec"] is None
    assert response.json()["legacy_developer_spec_unavailable"] is True
    assert "/private/old-auth" not in response.text
    assert client.get("/tasks").status_code == 200
    with database.read() as session:
        assert session.get(Task, task_id).developer_spec == legacy_spec
