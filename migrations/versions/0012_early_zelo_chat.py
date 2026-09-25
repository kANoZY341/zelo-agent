"""Add persistent early Zelo Mind conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0012_early_zelo_chat"
down_revision = "0011_self_improvement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("user_content", sa.Text(), nullable=False),
        sa.Column("assistant_content", sa.Text(), nullable=False),
        sa.Column("mind_action", sa.JSON(), nullable=False),
        sa.Column("selected_alias", sa.String(length=64), nullable=False),
        sa.Column("selected_model", sa.String(length=128), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column(
            "core_task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversation_id", "number", name="uq_conversation_turn_number"),
        sa.UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_conversation_turn_idempotency",
        ),
    )
    op.create_index(
        "ix_conversation_turns_conversation_id",
        "conversation_turns",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_turns_core_task_id",
        "conversation_turns",
        ["core_task_id"],
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
