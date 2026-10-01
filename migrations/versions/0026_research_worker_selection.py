"""Persist exact Research Worker selection on Core tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0026_research_worker_selection"
down_revision = "0025_hierarchy_child_creation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("research_spec", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
