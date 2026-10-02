"""Add bounded browser task kind and persist its exact execution specification."""

import sqlalchemy as sa
from alembic import op

revision = "0029_browser_worker_selection"
down_revision = "0028_api_worker_selection"
branch_labels = None
depends_on = None

_INSERT_GUARD = """
CREATE TRIGGER running_attempt_insert_exclusive
BEFORE INSERT ON task_attempts
WHEN NEW.outcome = 'running'
BEGIN
    SELECT RAISE(ABORT, 'non-developer running attempt must be exclusive')
    WHERE (SELECT kind FROM tasks WHERE id = NEW.task_id) != 'developer_run'
      AND EXISTS (SELECT 1 FROM task_attempts WHERE outcome = 'running');

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
          WHERE outcome = 'running' AND id != OLD.id
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
    connection = op.get_bind()
    task_triggers = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND tbl_name = 'tasks' AND sql IS NOT NULL "
            "ORDER BY name"
        )
    ).all()
    op.execute("DROP TRIGGER IF EXISTS running_attempt_insert_exclusive")
    op.execute("DROP TRIGGER IF EXISTS running_attempt_update_exclusive")

    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind",
            "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt', "
            "'tool_call', 'agent_run', 'developer_run', 'promotion_run', "
            "'merge_run', 'release_run', 'self_improvement_run', 'research_run', "
            "'python_run', 'api_run', 'browser_run')",
        )
        batch.add_column(sa.Column("browser_spec", sa.JSON(), nullable=True))

    op.execute(_INSERT_GUARD)
    op.execute(_UPDATE_GUARD)
    for name, sql in task_triggers:
        if name == "tasks_agent_execution_spec_kind_guard":
            # Extend only this lane; retain every existing identity/scope guard.
            old = "NEW.kind NOT IN ('developer_run','research_run')"
            if old not in sql:
                raise RuntimeError("Unexpected Agent task kind guard")
            sql = sql.replace(old, "NEW.kind NOT IN ('developer_run','research_run','browser_run')")
        op.execute(sql)

    op.execute("""
        CREATE TRIGGER tasks_browser_spec_immutable
        BEFORE UPDATE OF browser_spec ON tasks
        WHEN NEW.browser_spec IS NOT OLD.browser_spec
          AND NOT (OLD.status IN ('failed', 'cancelled', 'interrupted')
                   AND NEW.status = 'pending'
                   AND NEW.action_version = OLD.action_version + 1)
        BEGIN
            SELECT RAISE(ABORT, 'Browser execution identity is immutable within an action');
        END
    """)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
