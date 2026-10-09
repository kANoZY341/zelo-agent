"""Inert owner-reviewed Space grant intentions: immutable and revocable."""

import sqlalchemy as sa
from alembic import op

revision = "0040_agent_space_intents"
down_revision = "0039_community_context_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_space_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_instance_id", sa.String(36), sa.ForeignKey("agent_instances.id"), nullable=False
        ),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("space_id", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("operations_json", sa.Text(), nullable=False),
        sa.Column("spec_sha256", sa.String(64), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("agent_version >= 1", name="ck_space_intent_agent_version"),
        sa.CheckConstraint("length(registry_version)=64", name="ck_space_intent_registry"),
        sa.CheckConstraint(
            "length(spec_sha256)=64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_space_intent_spec_hash",
        ),
        sa.CheckConstraint(
            "length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_space_intent_hash",
        ),
        sa.CheckConstraint(
            "resource_kind IN ('files','browser','terminal','memory')", name="ck_space_intent_kind"
        ),
        sa.CheckConstraint(
            "length(operations_json) BETWEEN 8 AND 32", name="ck_space_intent_operations_size"
        ),
    )
    op.create_index(
        "ix_agent_space_intents_agent_instance_id", "agent_space_intents", ["agent_instance_id"]
    )
    op.create_index("ix_agent_space_intents_project_id", "agent_space_intents", ["project_id"])
    op.create_table(
        "agent_space_intent_revocations",
        sa.Column(
            "intent_id",
            sa.String(36),
            sa.ForeignKey("agent_space_intents.id"),
            primary_key=True,
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=False),
    )
    for table in ("agent_space_intents", "agent_space_intent_revocations"):
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Space intent is immutable'); END"
            )


def downgrade() -> None:
    for table in ("agent_space_intent_revocations", "agent_space_intents"):
        for action in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{action.lower()}")
    op.drop_table("agent_space_intent_revocations")
    op.drop_index("ix_agent_space_intents_project_id", table_name="agent_space_intents")
    op.drop_index("ix_agent_space_intents_agent_instance_id", table_name="agent_space_intents")
    op.drop_table("agent_space_intents")
