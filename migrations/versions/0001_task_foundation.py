"""Persistent tasks, attempts, approvals, and append-only audit history."""

import sqlalchemy as sa
from alembic import op

revision = "0001_task_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("action_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(256)),
        sa.CheckConstraint("kind IN ('echo', 'text_stats', 'approval_demo')", name="ck_task_kind"),
        sa.CheckConstraint(
            "status IN ('pending','awaiting_approval','running','succeeded',"
            "'failed','cancelled','interrupted')",
            name="ck_task_status",
        ),
        sa.CheckConstraint("action_version >= 1", name="ck_task_version"),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(256)),
        sa.Column("cancellation_requested_at", sa.DateTime()),
        sa.UniqueConstraint("task_id", "number", name="uq_attempt_number"),
        sa.CheckConstraint("number >= 1", name="ck_attempt_number"),
        sa.CheckConstraint(
            "outcome IN ('running','succeeded','failed','cancelled','interrupted')",
            name="ck_attempt_outcome",
        ),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_single_running_attempt ON task_attempts ((1)) "
        "WHERE outcome = 'running'"
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("action_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime()),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired','invalidated','cancelled')",
            name="ck_approval_status",
        ),
    )
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("task_attempts.id")),
        sa.Column("approval_id", sa.String(36), sa.ForeignKey("approvals.id")),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_events_task_id", "audit_events", ["task_id"])
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER audit_no_{operation.lower()} BEFORE {operation} ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END"
        )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
