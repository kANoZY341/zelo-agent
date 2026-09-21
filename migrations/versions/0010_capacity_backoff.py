"""Persist developer capacity backoff eligibility."""

import sqlalchemy as sa
from alembic import op

revision = "0010_capacity_backoff"
down_revision = "0009_worker_concurrency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("capacity_backoff_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("tasks", sa.Column("capacity_backoff_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
