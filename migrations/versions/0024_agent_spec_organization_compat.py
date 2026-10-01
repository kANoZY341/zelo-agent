"""Preserve AgentSpec execution identity across organizational-level schema extension."""

from alembic import op

revision = "0024_agent_spec_organization_compat"
down_revision = "0023_organizational_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tasks_agent_execution_spec_identity_guard")
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
              AND json_remove(
                    json(instance.spec),
                    '$.organizational_level'
                  ) = json_remove(
                    json(json_extract(NEW.agent_execution_spec, '$.spec')),
                    '$.organizational_level'
                  )
              AND COALESCE(
                    json_extract(instance.spec, '$.organizational_level'),
                    'specialist'
                  ) = COALESCE(
                    json_extract(
                        NEW.agent_execution_spec,
                        '$.spec.organizational_level'
                    ),
                    'specialist'
                  )
         )
        BEGIN
            SELECT RAISE(ABORT, 'Agent execution identity is invalid');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
