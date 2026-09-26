"""Allow the fixed Research specialist task kind without weakening task guards."""

from alembic import op

revision = "0017_research_specialist"
down_revision = "0016_scheduler_wakes"
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
    # Recreating the SQLite tasks table invalidates the v0.9 attempt triggers
    # because they query tasks directly. Remove and restore them byte-for-byte.
    op.execute("DROP TRIGGER IF EXISTS running_attempt_insert_exclusive")
    op.execute("DROP TRIGGER IF EXISTS running_attempt_update_exclusive")

    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind",
            "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt', "
            "'tool_call', 'agent_run', 'developer_run', 'promotion_run', "
            "'merge_run', 'release_run', 'self_improvement_run', 'research_run')",
        )

    op.execute(_INSERT_GUARD)
    op.execute(_UPDATE_GUARD)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
