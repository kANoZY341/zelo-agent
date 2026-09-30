"""Bind persisted tasks to exact Agent Factory execution identity."""

import sqlalchemy as sa
from alembic import op

revision = "0022_agent_execution_identity"
down_revision = "0021_agent_delegations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("agent_execution_spec", sa.JSON(), nullable=True),
    )

    op.execute(
        """
        CREATE TRIGGER tasks_agent_execution_spec_kind_guard
        BEFORE INSERT ON tasks
        FOR EACH ROW
        WHEN json_type(NEW.agent_execution_spec) NOT IN ('null','object')
          OR (
            json_type(NEW.agent_execution_spec) = 'object'
            AND (
                NEW.kind NOT IN ('developer_run','research_run')
                OR json_extract(NEW.agent_execution_spec, '$.task_kind') <> NEW.kind
            )
         )
        BEGIN
            SELECT RAISE(ABORT, 'Agent execution task kind is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tasks_agent_execution_spec_identity_guard
        BEFORE INSERT ON tasks
        FOR EACH ROW
        WHEN json_type(NEW.agent_execution_spec) = 'object'
         AND NOT EXISTS (
            SELECT 1
            FROM agent_instances AS instance
            WHERE instance.id = json_extract(
                NEW.agent_execution_spec,
                '$.agent_instance_id'
            )
              AND instance.project_id = json_extract(
                NEW.agent_execution_spec,
                '$.project_id'
            )
              AND instance.agent_id = json_extract(
                NEW.agent_execution_spec,
                '$.spec.agent_id'
            )
              AND instance.agent_version = CAST(
                json_extract(NEW.agent_execution_spec, '$.spec.version') AS INTEGER
            )
              AND instance.registry_version = json_extract(
                NEW.agent_execution_spec,
                '$.registry_version'
            )
         )
        BEGIN
            SELECT RAISE(ABORT, 'Agent execution identity is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tasks_agent_execution_spec_delegation_guard
        BEFORE INSERT ON tasks
        FOR EACH ROW
        WHEN json_type(NEW.agent_execution_spec, '$.delegation') = 'object'
         AND NOT EXISTS (
            SELECT 1
            FROM agent_delegations AS delegation
            WHERE delegation.id = json_extract(
                NEW.agent_execution_spec,
                '$.delegation.delegation_id'
            )
              AND delegation.child_instance_id = json_extract(
                NEW.agent_execution_spec,
                '$.agent_instance_id'
            )
              AND delegation.parent_instance_id = json_extract(
                NEW.agent_execution_spec,
                '$.delegation.parent_instance_id'
            )
              AND delegation.campaign_id = json_extract(
                NEW.agent_execution_spec,
                '$.delegation.campaign_id'
            )
              AND delegation.request_hash = json_extract(
                NEW.agent_execution_spec,
                '$.delegation.request_hash'
            )
         )
        BEGIN
            SELECT RAISE(ABORT, 'Agent execution delegation identity is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tasks_agent_execution_spec_immutable
        BEFORE UPDATE OF agent_execution_spec ON tasks
        FOR EACH ROW
        WHEN NEW.agent_execution_spec IS NOT OLD.agent_execution_spec
        BEGIN
            SELECT RAISE(ABORT, 'Agent execution identity is immutable');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
