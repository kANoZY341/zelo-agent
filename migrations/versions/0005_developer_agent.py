"""Allow approved developer worker tasks and bind their immutable source snapshot."""

import sqlalchemy as sa
from alembic import op

revision = "0005_developer_agent"
down_revision = "0004_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind",
            "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt', "
            "'tool_call', 'agent_run', 'developer_run')",
        )
        batch.add_column(sa.Column("developer_spec", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
