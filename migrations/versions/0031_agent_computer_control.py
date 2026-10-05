"""Persist per-AgentInstance computer permissions and append-only activity."""

import sqlalchemy as sa
from alembic import op

revision = "0031_agent_computer_control"
down_revision = "0030_shell_worker_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_computer_permissions",
        sa.Column(
            "agent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("browser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("files", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("shell", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "enabled IN (0,1) AND browser IN (0,1) AND files IN (0,1) AND shell IN (0,1)",
            name="ck_agent_computer_permission_booleans",
        ),
    )

    op.create_table(
        "agent_computer_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "agent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "agent_instance_id",
            "sequence",
            name="uq_agent_computer_event_sequence",
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_agent_computer_event_sequence",
        ),
        sa.CheckConstraint(
            "actor IN ('owner','system')",
            name="ck_agent_computer_event_actor",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded','failed')",
            name="ck_agent_computer_event_outcome",
        ),
    )
    op.create_index(
        "ix_agent_computer_events_agent_instance_id",
        "agent_computer_events",
        ["agent_instance_id"],
    )

    op.execute(
        """
        CREATE TRIGGER agent_computer_events_sequence_guard
        BEFORE INSERT ON agent_computer_events
        FOR EACH ROW
        WHEN NEW.sequence <> COALESCE(
            (
                SELECT MAX(sequence) + 1
                FROM agent_computer_events
                WHERE agent_instance_id = NEW.agent_instance_id
            ),
            1
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent computer event sequence must be contiguous');
        END
        """
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER agent_computer_events_no_{operation.lower()} "
            f"BEFORE {operation} ON agent_computer_events "
            "BEGIN SELECT RAISE(ABORT, 'Agent computer events are append-only'); END"
        )

    op.execute(
        """
        CREATE TRIGGER agent_computer_permissions_no_reassign
        BEFORE UPDATE OF agent_instance_id ON agent_computer_permissions
        WHEN NEW.agent_instance_id <> OLD.agent_instance_id
        BEGIN
            SELECT RAISE(ABORT, 'Agent computer ownership is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_computer_permissions_no_delete
        BEFORE DELETE ON agent_computer_permissions
        BEGIN
            SELECT RAISE(ABORT, 'Agent computer permission records cannot be deleted');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
