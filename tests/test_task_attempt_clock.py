"""Task attempt timestamps remain ordered across wall-clock corrections."""

from datetime import timedelta

from app.models import TaskAttempt
from app.policy import State
from app.schemas import EchoInput
from app.tasks import TaskService


def test_finish_keeps_attempt_timestamp_order_after_clock_rewind(
    settings, database, monkeypatch
) -> None:
    service = TaskService(database, settings)
    task, _ = service.submit(EchoInput(kind="echo", payload={"text": "clock"}), None)
    execution = service.claim()
    assert execution is not None and execution.task_id == task.id
    with database.read() as session:
        started_at = session.get(TaskAttempt, execution.attempt_id).started_at

    monkeypatch.setattr("app.tasks.utcnow", lambda: started_at - timedelta(seconds=2))
    service.finish(execution, State.SUCCEEDED, {"text": "clock"})

    with database.read() as session:
        attempt = session.get(TaskAttempt, execution.attempt_id)
        assert attempt.outcome == State.SUCCEEDED
        assert attempt.ended_at == started_at
