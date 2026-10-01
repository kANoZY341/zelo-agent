"""Record atomic hierarchy-aware Agent Factory child creation."""

import sqlalchemy as sa
from alembic import op

revision = "0025_hierarchy_child_creation"
down_revision = "0024_agent_spec_organization_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_child_creations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "parent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_assignment_id",
            sa.String(length=36),
            sa.ForeignKey("organization_assignments.id"),
            nullable=False,
        ),
        sa.Column(
            "child_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column(
            "child_assignment_id",
            sa.String(length=36),
            sa.ForeignKey("organization_assignments.id"),
            nullable=False,
        ),
        sa.Column(
            "delegation_id",
            sa.String(length=36),
            sa.ForeignKey("agent_delegations.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "parent_instance_id",
            "idempotency_key",
            name="uq_agent_child_creation_idempotency",
        ),
        sa.UniqueConstraint(
            "child_instance_id",
            name="uq_agent_child_creation_child",
        ),
        sa.UniqueConstraint(
            "child_assignment_id",
            name="uq_agent_child_creation_assignment",
        ),
        sa.UniqueConstraint(
            "delegation_id",
            name="uq_agent_child_creation_delegation",
        ),
    )
    op.create_index(
        "ix_agent_child_creations_parent_instance_id",
        "agent_child_creations",
        ["parent_instance_id"],
    )
    op.create_index(
        "ix_agent_child_creations_parent_assignment_id",
        "agent_child_creations",
        ["parent_assignment_id"],
    )

    op.execute(
        """
        CREATE TRIGGER agent_child_creations_relation_guard
        BEFORE INSERT ON agent_child_creations
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM organization_assignments AS parent_assignment
            JOIN organization_assignments AS child_assignment
              ON child_assignment.id = NEW.child_assignment_id
            JOIN agent_instances AS parent
              ON parent.id = NEW.parent_instance_id
            JOIN agent_instances AS child
              ON child.id = NEW.child_instance_id
            JOIN agent_delegations AS delegation
              ON delegation.id = NEW.delegation_id
            WHERE parent_assignment.id = NEW.parent_assignment_id
              AND parent_assignment.agent_instance_id = parent.id
              AND child_assignment.agent_instance_id = child.id
              AND child_assignment.reports_to_assignment_id = parent_assignment.id
              AND delegation.parent_instance_id = parent.id
              AND delegation.child_instance_id = child.id
              AND parent.project_id = child.project_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent child creation relation is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_child_creations_immutable
        BEFORE UPDATE ON agent_child_creations
        BEGIN
            SELECT RAISE(ABORT, 'Agent child creation receipt is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_child_creations_no_delete
        BEFORE DELETE ON agent_child_creations
        BEGIN
            SELECT RAISE(ABORT, 'Agent child creation receipts cannot be deleted');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
