"""Allow only policy-bound, direct skill-learning proposal tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0034_skill_learning_task"
down_revision = "0033_skill_learning_proposals"
branch_labels = None
depends_on = None

_AGENT_KIND_GUARD = """
CREATE TRIGGER tasks_agent_execution_spec_kind_guard
BEFORE INSERT ON tasks
FOR EACH ROW
WHEN json_type(NEW.agent_execution_spec) NOT IN ('null','object')
  OR (
    json_type(NEW.agent_execution_spec) = 'object'
    AND (
      NEW.kind NOT IN (
        'developer_run','research_run','browser_run','shell_run',
        'computer_run','skill_learning_proposal_run'
      )
      OR json_extract(NEW.agent_execution_spec, '$.task_kind') <> NEW.kind
      OR (NEW.kind = 'skill_learning_proposal_run' AND (
        COALESCE(json_extract(NEW.agent_execution_spec, '$.spec.skill_learning'), '')
          <> 'propose'
        OR COALESCE(json_type(NEW.agent_execution_spec, '$.delegation'), 'missing')
          <> 'null'
      ))
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'Agent execution task kind is invalid');
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
    attempt_triggers = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND tbl_name = 'task_attempts' AND sql IS NOT NULL "
            "ORDER BY name"
        )
    ).all()
    for name, _sql in attempt_triggers:
        op.execute(f'DROP TRIGGER "{name}"')

    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind",
            "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt', "
            "'tool_call', 'agent_run', 'developer_run', 'promotion_run', "
            "'merge_run', 'release_run', 'self_improvement_run', 'research_run', "
            "'python_run', 'api_run', 'browser_run', 'shell_run', 'computer_run', "
            "'skill_learning_proposal_run')",
        )

    for name, sql in task_triggers:
        if name != "tasks_agent_execution_spec_kind_guard":
            op.execute(sql)
    op.execute(_AGENT_KIND_GUARD)
    for _name, sql in attempt_triggers:
        op.execute(sql)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
