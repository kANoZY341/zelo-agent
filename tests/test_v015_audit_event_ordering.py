"""Regression: task audit order must not depend on wall-clock or random UUIDs."""

from datetime import timedelta

import pytest

from app.models import AuditEvent, utcnow
from app.schemas import EchoInput
from app.tasks import TaskService


@pytest.mark.parametrize("second_offset", (timedelta(0), -timedelta(seconds=2)))
def test_task_audit_event_order_survives_wall_clock_ties_and_regressions(
    database, settings, second_offset
):
    service = TaskService(database, settings)
    task, created = service.submit(EchoInput(kind="echo", payload={"text": "audit"}), None)
    assert created
    same_instant = utcnow() + timedelta(days=1)

    # Explicit IDs force the later insert to sort earlier by UUID. SQLAlchemy
    # flushes after each addition so the SQLite insertion order is grounded.
    with database.transaction() as session:
        session.add(
            AuditEvent(
                id="ffffffff-ffff-4fff-8fff-ffffffffffff",
                task_id=task.id,
                event="first_after_submission",
                created_at=same_instant,
                details={},
            )
        )
        session.flush()
        session.add(
            AuditEvent(
                id="00000000-0000-4000-8000-000000000001",
                task_id=task.id,
                event="second_after_submission",
                created_at=same_instant + second_offset,
                details={},
            )
        )

    names = [record.event for record in service.events(task.id, 50, 0)]
    assert names[-2:] == ["first_after_submission", "second_after_submission"]
