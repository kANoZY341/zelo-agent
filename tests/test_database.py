"""Migration, transaction, audit, and database configuration guarantees."""

import sqlite3

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.database import Database, migration_config
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
            "conversations",
            "conversation_turns",
            "projects",
            "goals",
            "campaigns",
            "campaign_task_links",
            "task_dependencies",
            "campaign_events",
            "campaign_checkpoints",
            "campaign_leases",
            "campaign_budget_limits",
            "campaign_budget_reservations",
            "campaign_schedules",
            "campaign_schedule_continuations",
            "campaign_schedule_runs",
            "memory_records",
            "agent_instances",
            "agent_instance_events",
            "agent_delegations",
            "organization_units",
            "organization_assignments",
            "agent_child_creations",
        }
        assert not any(
            table.startswith("_alembic_tmp_")
            for table in inspect(database.engine).get_table_names()
        )
        database.migrate()  # Re-running upgrade head is non-destructive.
        with database.read() as session:
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            assert session.scalar(text("PRAGMA journal_mode")) == "wal"
            assert session.scalar(text("PRAGMA busy_timeout")) == 5000
            assert session.scalar(text("PRAGMA integrity_check")) == "ok"
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        database.close()


@pytest.mark.parametrize(
    "starting_revision",
    [
        "0001_task_foundation",
        "0003_tool_call",
        "0004_agent_runtime",
        "0005_developer_agent",
        "0006_promotion_review",
        "0007_merge_approval",
        "0008_release_approval",
        "0016_scheduler_wakes",
        "0019_memory_foundation",
    ],
)
def test_migration_preserves_foreign_key_dependents(settings, starting_revision) -> None:
    database = Database(settings.database_path)
    try:
        config = migration_config()
        with database.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, starting_revision)

        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id, kind, payload, timeout_seconds, status, action_version,
                        action_hash, request_hash, cancel_requested, created_at, updated_at
                    ) VALUES (
                        :task_id, 'echo', '{}', 30, 'succeeded', 1,
                        :action_hash, :request_hash, 0, '2026-01-01 00:00:00',
                        '2026-01-01 00:00:00'
                    )
                    """
                ),
                {
                    "task_id": "task-before-model-gateway",
                    "action_hash": "a" * 64,
                    "request_hash": "b" * 64,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO task_attempts (
                        id, task_id, number, started_at, outcome
                    ) VALUES (
                        'attempt-before-model-gateway', :task_id, 1,
                        '2026-01-01 00:00:00', 'succeeded'
                    )
                    """
                ),
                {"task_id": "task-before-model-gateway"},
            )
            session.execute(
                text(
                    """
                    INSERT INTO approvals (
                        id, task_id, action_version, action_hash, status,
                        created_at, expires_at
                    ) VALUES (
                        'approval-before-model-gateway', :task_id, 1, :action_hash,
                        'approved', '2026-01-01 00:00:00', '2026-01-02 00:00:00'
                    )
                    """
                ),
                {"task_id": "task-before-model-gateway", "action_hash": "c" * 64},
            )
            session.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        id, task_id, attempt_id, approval_id, event,
                        created_at, details
                    ) VALUES (
                        'audit-before-model-gateway', :task_id, :attempt_id,
                        :approval_id, 'task_submitted', '2026-01-01 00:00:00', '{}'
                    )
                    """
                ),
                {
                    "task_id": "task-before-model-gateway",
                    "attempt_id": "attempt-before-model-gateway",
                    "approval_id": "approval-before-model-gateway",
                },
            )

        with database.read() as session:
            assert session.scalar(text("PRAGMA foreign_keys")) == 1

        database.migrate()

        with database.read() as session:
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            assert session.execute(
                text("SELECT kind, selected_alias FROM tasks WHERE id = :task_id"),
                {"task_id": "task-before-model-gateway"},
            ).one() == ("echo", None)
            assert (
                session.execute(
                    text("SELECT task_id FROM task_attempts WHERE id = :attempt_id"),
                    {"attempt_id": "attempt-before-model-gateway"},
                ).scalar_one()
                == "task-before-model-gateway"
            )
            assert (
                session.execute(
                    text("SELECT task_id FROM approvals WHERE id = :approval_id"),
                    {"approval_id": "approval-before-model-gateway"},
                ).scalar_one()
                == "task-before-model-gateway"
            )
            assert session.execute(
                text(
                    "SELECT task_id, attempt_id, approval_id FROM audit_events WHERE id = :event_id"
                ),
                {"event_id": "audit-before-model-gateway"},
            ).one() == (
                "task-before-model-gateway",
                "attempt-before-model-gateway",
                "approval-before-model-gateway",
            )
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == "0026_research_worker_selection"
            )
            assert "tool_call" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "agent_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "developer_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "promotion_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "merge_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "release_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "self_improvement_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            assert "research_run" in next(
                constraint["sqltext"]
                for constraint in inspect(database.engine).get_check_constraints("tasks")
                if constraint["name"] == "ck_task_kind"
            )
            columns = {column["name"] for column in inspect(database.engine).get_columns("tasks")}
            assert "developer_spec" in columns
            assert "research_spec" in columns
            assert "agent_execution_spec" in columns
            assert "promotion_spec" in columns
            assert "merge_spec" in columns
            assert "release_spec" in columns
            assert "capacity_backoff_attempts" in columns
            assert "capacity_backoff_until" in columns
            trigger_names = {
                row[0]
                for row in session.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND name LIKE 'running_attempt_%_exclusive'"
                    )
                )
            }
            assert trigger_names == {
                "running_attempt_insert_exclusive",
                "running_attempt_update_exclusive",
            }
            assert not any(
                table.startswith("_alembic_tmp_")
                for table in inspect(database.engine).get_table_names()
            )
    finally:
        database.close()


@pytest.mark.parametrize(
    "starting_revision,target_revision,failure_statement",
    [
        ("0001_task_foundation", "0002_model_gateway", "ADD COLUMN selected_alias"),
        ("0003_tool_call", "0004_agent_runtime", "DROP TABLE tasks"),
        ("0004_agent_runtime", "0005_developer_agent", "DROP TABLE tasks"),
        ("0005_developer_agent", "0006_promotion_review", "DROP TABLE tasks"),
        ("0006_promotion_review", "0007_merge_approval", "DROP TABLE tasks"),
        ("0007_merge_approval", "0008_release_approval", "DROP TABLE tasks"),
        (
            "0008_release_approval",
            "0009_worker_concurrency",
            "CREATE UNIQUE INDEX uq_running_attempt_per_task",
        ),
        (
            "0009_worker_concurrency",
            "0010_capacity_backoff",
            "ADD COLUMN capacity_backoff_attempts",
        ),
        (
            "0010_capacity_backoff",
            "0011_self_improvement",
            "DROP TABLE tasks",
        ),
        (
            "0011_self_improvement",
            "0014_campaign_state",
            "CREATE TABLE conversations",
        ),
    ],
)
def test_failed_migration_rolls_back_and_can_retry(
    settings, starting_revision, target_revision, failure_statement
) -> None:
    database = Database(settings.database_path)
    try:
        config = migration_config()
        with database.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, starting_revision)

        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id, kind, payload, timeout_seconds, status, action_version,
                        action_hash, request_hash, cancel_requested, created_at, updated_at
                    ) VALUES (
                        'task-rollback', 'echo', '{}', 30, 'succeeded', 1,
                        :action_hash, :request_hash, 0, '2026-01-01 00:00:00',
                        '2026-01-01 00:00:00'
                    )
                    """
                ),
                {"action_hash": "a" * 64, "request_hash": "b" * 64},
            )
            session.execute(
                text(
                    """
                    INSERT INTO task_attempts (
                        id, task_id, number, started_at, outcome
                    ) VALUES (
                        'attempt-rollback', 'task-rollback', 1,
                        '2026-01-01 00:00:00', 'succeeded'
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO approvals (
                        id, task_id, action_version, action_hash, status,
                        created_at, expires_at
                    ) VALUES (
                        'approval-rollback', 'task-rollback', 1, :action_hash,
                        'approved', '2026-01-01 00:00:00', '2026-01-02 00:00:00'
                    )
                    """
                ),
                {"action_hash": "c" * 64},
            )
            session.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        id, task_id, attempt_id, approval_id, event,
                        created_at, details
                    ) VALUES (
                        'audit-rollback', 'task-rollback', 'attempt-rollback',
                        'approval-rollback', 'task_submitted',
                        '2026-01-01 00:00:00', '{}'
                    )
                    """
                )
            )

        before_columns = {
            column["name"] for column in inspect(database.engine).get_columns("tasks")
        }
        before_counts = {}
        with database.read() as session:
            for table in ("tasks", "task_attempts", "approvals", "audit_events"):
                before_counts[table] = session.scalar(text(f"SELECT COUNT(*) FROM {table}"))

        def fail_after_selected_alias(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ) -> None:
            if failure_statement in statement:
                raise RuntimeError("injected migration failure")

        with database.engine.connect() as connection:
            event.listen(connection, "after_cursor_execute", fail_after_selected_alias)
            config = migration_config()
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="injected migration failure"):
                command.upgrade(config, target_revision)
            event.remove(connection, "after_cursor_execute", fail_after_selected_alias)

        assert {
            column["name"] for column in inspect(database.engine).get_columns("tasks")
        } == before_columns
        assert set(inspect(database.engine).get_table_names()) == {
            "alembic_version",
            "approvals",
            "audit_events",
            "task_attempts",
            "tasks",
        }
        with database.read() as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version")) == starting_revision
            )
            for table, count in before_counts.items():
                assert session.scalar(text(f"SELECT COUNT(*) FROM {table}")) == count
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []

        database.migrate()

        with database.read() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0026_research_worker_selection"
            )
            assert session.scalar(text("SELECT COUNT(*) FROM tasks")) == before_counts["tasks"]
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert not any(
            table.startswith("_alembic_tmp_")
            for table in inspect(database.engine).get_table_names()
        )
    finally:
        database.close()


def test_research_task_kind_is_database_allowlisted_only(settings) -> None:
    database = Database(settings.database_path)
    try:
        database.migrate()
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id, kind, payload, timeout_seconds, status, action_version,
                        action_hash, request_hash, cancel_requested, created_at, updated_at
                    ) VALUES (
                        'research-db-kind', 'research_run', '{"query":"proof"}', 120,
                        'pending', 1, :action_hash, :request_hash, 0,
                        '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                    )
                    """
                ),
                {"action_hash": "a" * 64, "request_hash": "b" * 64},
            )
        with pytest.raises(IntegrityError, match="ck_task_kind"):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO tasks (
                            id, kind, payload, timeout_seconds, status, action_version,
                            action_hash, request_hash, cancel_requested, created_at, updated_at
                        ) VALUES (
                            'unknown-db-kind', 'research_write', '{}', 120,
                            'pending', 1, :action_hash, :request_hash, 0,
                            '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                        )
                        """
                    ),
                    {"action_hash": "c" * 64, "request_hash": "d" * 64},
                )
    finally:
        database.close()


def test_unmigrated_application_refuses_start(settings) -> None:
    with pytest.raises(RuntimeError, match="migration required"):
        with TestClient(create_app(settings)):
            pass


def test_developer_kind_and_specification_persist(database) -> None:
    with database.transaction() as session:
        task = Task(
            kind="developer_run",
            payload={"instruction": "fixture", "validation_profile": "zelo_core"},
            timeout_seconds=30,
            status="awaiting_approval",
            action_version=1,
            action_hash="a" * 64,
            request_hash="b" * 64,
            developer_spec={"base_sha": "c" * 40, "limits": {"overall_seconds": 30}},
        )
        session.add(task)
        session.flush()
        task_id = task.id
    with database.read() as session:
        task = session.get(Task, task_id)
        assert task.kind == "developer_run"
        assert task.developer_spec["limits"]["overall_seconds"] == 30
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_running_attempt_database_invariant_allows_only_developer_concurrency(database) -> None:
    with database.transaction() as session:
        for task_id, kind in (
            ("dev-one", "developer_run"),
            ("dev-two", "developer_run"),
            ("echo-one", "echo"),
        ):
            session.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id, kind, payload, timeout_seconds, status, action_version,
                        action_hash, request_hash, cancel_requested, created_at, updated_at
                    ) VALUES (
                        :task_id, :kind, '{}', 30, 'running', 1,
                        :action_hash, :request_hash, 0,
                        '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                    )
                    """
                ),
                {
                    "task_id": task_id,
                    "kind": kind,
                    "action_hash": ("a" if kind == "developer_run" else "c") * 64,
                    "request_hash": ("b" if kind == "developer_run" else "d") * 64,
                },
            )

        session.execute(
            text(
                """
                INSERT INTO task_attempts (id, task_id, number, started_at, outcome)
                VALUES ('dev-attempt-one', 'dev-one', 1, '2026-01-01 00:00:00', 'running')
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO task_attempts (id, task_id, number, started_at, outcome)
                VALUES ('dev-attempt-two', 'dev-two', 1, '2026-01-01 00:00:00', 'running')
                """
            )
        )
        with pytest.raises(IntegrityError, match="exclusive"):
            with session.begin_nested():
                session.execute(
                    text(
                        """
                        INSERT INTO task_attempts (id, task_id, number, started_at, outcome)
                        VALUES (
                            'echo-attempt-one', 'echo-one', 1,
                            '2026-01-01 00:00:00', 'running'
                        )
                        """
                    )
                )
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.execute(
                    text(
                        """
                        INSERT INTO task_attempts (id, task_id, number, started_at, outcome)
                        VALUES (
                            'dev-attempt-duplicate', 'dev-one', 2,
                            '2026-01-01 00:00:00', 'running'
                        )
                        """
                    )
                )


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
