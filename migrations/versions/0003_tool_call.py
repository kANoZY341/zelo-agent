"""Allow the native tool-call task kind."""

from alembic import op

revision = "0003_tool_call"
down_revision = "0002_model_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_task_kind", type_="check")
        batch.create_check_constraint(
            "ck_task_kind",
            "kind IN ('echo', 'text_stats', 'approval_demo', 'ai_prompt', 'tool_call')",
        )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
