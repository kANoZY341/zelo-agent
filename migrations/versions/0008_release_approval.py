"""Persist separately approved exact-main-SHA release actions."""

import sqlalchemy as sa
from alembic import op

revision = "0008_release_approval"
down_revision = "0007_merge_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind",
            "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt', "
            "'tool_call', 'agent_run', 'developer_run', 'promotion_run', "
            "'merge_run', 'release_run')",
        )
        batch.add_column(sa.Column("release_spec", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
