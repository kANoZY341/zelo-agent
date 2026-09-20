"""Permit concurrent developer attempts while preserving exclusive non-developer work."""

from alembic import op

revision = "0009_worker_concurrency"
down_revision = "0008_release_approval"
branch_labels = None
depends_on = None


_INSERT_GUARD = """
CREATE TRIGGER running_attempt_insert_exclusive
BEFORE INSERT ON task_attempts
WHEN NEW.outcome = 'running'
BEGIN
    SELECT RAISE(ABORT, 'non-developer running attempt must be exclusive')
    WHERE (SELECT kind FROM tasks WHERE id = NEW.task_id) != 'developer_run'
      AND EXISTS (
          SELECT 1 FROM task_attempts
          WHERE outcome = 'running'
      );

    SELECT RAISE(ABORT, 'developer attempt conflicts with exclusive running attempt')
    WHERE (SELECT kind FROM tasks WHERE id = NEW.task_id) = 'developer_run'
      AND EXISTS (
          SELECT 1
          FROM task_attempts AS attempts
          JOIN tasks ON tasks.id = attempts.task_id
          WHERE attempts.outcome = 'running'
            AND tasks.kind != 'developer_run'
      );
END
"""

_UPDATE_GUARD = """
CREATE TRIGGER running_attempt_update_exclusive
BEFORE UPDATE OF outcome, task_id ON task_attempts
WHEN NEW.outcome = 'running'
 AND (OLD.outcome != 'running' OR OLD.task_id != NEW.task_id)
BEGIN
    SELECT RAISE(ABORT, 'non-developer running attempt must be exclusive')
    WHERE (SELECT kind FROM tasks WHERE id = NEW.task_id) != 'developer_run'
      AND EXISTS (
          SELECT 1 FROM task_attempts
          WHERE outcome = 'running'
            AND id != OLD.id
      );

    SELECT RAISE(ABORT, 'developer attempt conflicts with exclusive running attempt')
    WHERE (SELECT kind FROM tasks WHERE id = NEW.task_id) = 'developer_run'
      AND EXISTS (
          SELECT 1
          FROM task_attempts AS attempts
          JOIN tasks ON tasks.id = attempts.task_id
          WHERE attempts.outcome = 'running'
            AND attempts.id != OLD.id
            AND tasks.kind != 'developer_run'
      );
END
"""


def upgrade() -> None:
    op.drop_index("uq_single_running_attempt", table_name="task_attempts")
    op.execute(
        "CREATE UNIQUE INDEX uq_running_attempt_per_task "
        "ON task_attempts (task_id) WHERE outcome = 'running'"
    )
    op.execute(_INSERT_GUARD)
    op.execute(_UPDATE_GUARD)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
