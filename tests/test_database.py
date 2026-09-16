"""Migration, transaction, audit, and database configuration guarantees."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.database import Database
from app.main import create_app
from app.models import AuditEvent, Task
from app.schemas import EchoInput
from app.tasks import TaskService
from tests.test_api import submit, wait_for


def test_migration_from_empty_database(settings) -> None:
    database = Database(settings.database_path)
    try:
        assert inspect(database.engine).get_table_names() == []
        database.migrate()
        database.check_schema()
        assert set(inspect(database.engine).get_table_names()) == {
            "alembic_version",
            "tasks",
            "task_attempts",
            "approvals",
            "audit_events",
        }
        database.migrate()  # Re-running upgrade head is non-destructive.
        with database.read() as session:
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            assert session.scalar(text("PRAGMA journal_mode")) == "wal"
            assert session.scalar(text("PRAGMA busy_timeout")) == 5000
            assert session.scalar(text("PRAGMA integrity_check")) == "ok"
    finally:
        database.close()


def test_unmigrated_application_refuses_start(settings) -> None:
    with pytest.raises(RuntimeError, match="migration required"):
        with TestClient(create_app(settings)):
            pass


def test_audit_consistency_and_append_only(client, database) -> None:
    task = wait_for(client, submit(client)["id"], "succeeded")
    events = client.get(f"/tasks/{task['id']}/events").json()
    names = [event["event"] for event in events]
    assert names == [
        "task_submitted",
        "attempt_created",
        "state_transition",
        "execution_started",
        "state_transition",
        "execution_completed",
    ]
    transitions = [event["details"] for event in events if event["event"] == "state_transition"]
    assert transitions == [
        {"previous": "pending", "target": "running"},
        {"previous": "running", "target": "succeeded"},
    ]
    assert "hello world" not in str(events)
    for statement in ("UPDATE audit_events SET event='altered'", "DELETE FROM audit_events"):
        with pytest.raises(IntegrityError, match="append-only"):
            with database.transaction() as session:
                session.execute(text(statement))
    assert client.get(f"/tasks/{task['id']}/events").json() == events


def test_state_and_audit_rollback_together(settings, database, monkeypatch) -> None:
    service = TaskService(database, settings)
    task, _ = service.submit(EchoInput(kind="echo", payload={"text": "x"}), None)

    def broken_audit(*args, **kwargs):
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr("app.tasks.audit", broken_audit)
    with pytest.raises(RuntimeError, match="injected"):
        service.cancel(task.id)
    with database.read() as session:
        stored = session.get(Task, task.id)
        assert stored.status == "pending" and stored.cancel_requested is False
        assert list(session.scalars(select(AuditEvent.event))) == ["task_submitted"]


def test_sqlite_backup_is_consistent(settings, database, client) -> None:
    task = wait_for(client, submit(client)["id"], "succeeded")
    backup = settings.runtime_dir / "backup.sqlite3"
    source = sqlite3.connect(settings.database_path)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        assert destination.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert destination.execute(
            "SELECT status FROM tasks WHERE id=?", (task["id"],)
        ).fetchone() == ("succeeded",)
        assert destination.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 6
    finally:
        destination.close()
        source.close()
