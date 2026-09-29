"""Add persisted project-bound agent instances for v0.14."""

import sqlalchemy as sa
from alembic import op

revision = "0020_agent_instances"
down_revision = "0019_memory_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_instances",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.String(length=64), nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','paused','retired')",
            name="ck_agent_instance_status",
        ),
        sa.CheckConstraint(
            "agent_version >= 1",
            name="ck_agent_instance_version",
        ),
        sa.CheckConstraint(
            "length(registry_version) = 64",
            name="ck_agent_instance_registry_version",
        ),
    )
    op.create_index("ix_agent_instances_agent_id", "agent_instances", ["agent_id"])
    op.create_index("ix_agent_instances_project_id", "agent_instances", ["project_id"])
    op.create_index("ix_agent_instances_status", "agent_instances", ["status"])

    op.create_table(
        "agent_instance_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "agent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "agent_instance_id",
            "sequence",
            name="uq_agent_instance_event_sequence",
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_agent_instance_event_sequence",
        ),
        sa.CheckConstraint(
            "event IN ('created','paused','resumed','retired')",
            name="ck_agent_instance_event_name",
        ),
    )
    op.create_index(
        "ix_agent_instance_events_agent_instance_id",
        "agent_instance_events",
        ["agent_instance_id"],
    )

    op.execute(
        """
        CREATE TRIGGER agent_instances_identity_immutable
        BEFORE UPDATE ON agent_instances
        FOR EACH ROW
        WHEN NEW.agent_id <> OLD.agent_id
          OR NEW.agent_version <> OLD.agent_version
          OR NEW.registry_version <> OLD.registry_version
          OR NEW.project_id <> OLD.project_id
          OR NEW.spec <> OLD.spec
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'Agent instance identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_instances_status_transition_guard
        BEFORE UPDATE OF status ON agent_instances
        FOR EACH ROW
        WHEN NOT (
            NEW.status = OLD.status
            OR (OLD.status = 'active' AND NEW.status IN ('paused','retired'))
            OR (OLD.status = 'paused' AND NEW.status IN ('active','retired'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'Invalid agent instance lifecycle transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_instances_no_delete
        BEFORE DELETE ON agent_instances
        BEGIN
            SELECT RAISE(ABORT, 'Agent instances cannot be deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_instance_events_sequence_guard
        BEFORE INSERT ON agent_instance_events
        FOR EACH ROW
        WHEN NEW.sequence <> COALESCE(
            (
                SELECT MAX(sequence) + 1
                FROM agent_instance_events
                WHERE agent_instance_id = NEW.agent_instance_id
            ),
            1
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent instance event sequence must be contiguous');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_instance_events_created_guard
        BEFORE INSERT ON agent_instance_events
        FOR EACH ROW
        WHEN (NEW.sequence = 1 AND NEW.event <> 'created')
          OR (NEW.sequence > 1 AND NEW.event = 'created')
        BEGIN
            SELECT RAISE(ABORT, 'Invalid agent instance creation event');
        END
        """
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER agent_instance_events_no_{operation.lower()} "
            f"BEFORE {operation} ON agent_instance_events "
            "BEGIN SELECT RAISE(ABORT, 'Agent instance events are append-only'); END"
        )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
