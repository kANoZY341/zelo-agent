"""Add explicit bounded continuation policy for Campaign schedules."""

import sqlalchemy as sa
from alembic import op

revision = "0018_autonomous_continuation"
down_revision = "0017_research_specialist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_schedule_continuations",
        sa.Column(
            "schedule_id",
            sa.String(length=36),
            sa.ForeignKey("campaign_schedules.id"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
