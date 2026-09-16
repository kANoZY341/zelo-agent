"""Add model selection snapshot fields."""

import sqlalchemy as sa
from alembic import op

revision = "0002_model_gateway"
down_revision = "0001_task_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("selected_alias", sa.String(64)))
    op.add_column("tasks", sa.Column("selected_model", sa.String(128)))
    op.add_column("tasks", sa.Column("config_version", sa.String(64)))
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind", "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt')"
        )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
