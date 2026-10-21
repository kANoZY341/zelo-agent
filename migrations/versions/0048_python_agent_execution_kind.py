"""Admit policy-bound Python Agent tasks to the existing Agent identity guard."""

from alembic import op

revision = "0048_python_agent_execution_kind"
down_revision = "0047_skill_publication_post_attempt_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The 0034 guard omitted a kind supported by AgentSpec, AgentTaskCreate,
    # TaskService, and ck_task_kind. Replace only this guard, keeping every
    # other task trigger and all task rows untouched.
    op.execute("DROP TRIGGER tasks_agent_execution_spec_kind_guard")
    op.execute(
        """
        CREATE TRIGGER tasks_agent_execution_spec_kind_guard
        BEFORE INSERT ON tasks
        FOR EACH ROW
        WHEN json_type(NEW.agent_execution_spec) NOT IN ('null','object')
          OR (
            json_type(NEW.agent_execution_spec) = 'object'
            AND (
              NEW.kind NOT IN (
                'developer_run','research_run','browser_run','shell_run',
                'computer_run','python_run','skill_learning_proposal_run'
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
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
