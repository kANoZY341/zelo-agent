"""Core-owned Space resource registry and owner-confirmed, still non-executing grants."""

import sqlalchemy as sa
from alembic import op

revision = "0041_space_resource_activation"
down_revision = "0040_agent_space_intents"
branch_labels = None
depends_on = None

_APPEND_ONLY_TABLES = (
    ("agent_space_resources", "id"),
    ("agent_space_resource_retirements", "resource_record_id"),
    ("agent_space_activations", "id"),
    ("agent_space_activation_revocations", "activation_id"),
)


def upgrade() -> None:
    op.create_table(
        "agent_space_resources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("space_id", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("operations_json", sa.Text(), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("root_device", sa.Integer(), nullable=False),
        sa.Column("root_inode", sa.Integer(), nullable=False),
        sa.Column("resource_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "space_id",
            "resource_kind",
            "resource_id",
            name="uq_space_resource_project_identity",
        ),
        sa.CheckConstraint("resource_kind='files'", name="ck_space_resource_kind"),
        sa.CheckConstraint("length(resource_sha256)=64", name="ck_space_resource_digest"),
    )
    op.create_index("ix_agent_space_resources_project_id", "agent_space_resources", ["project_id"])
    op.create_table(
        "agent_space_resource_retirements",
        sa.Column(
            "resource_record_id",
            sa.String(36),
            sa.ForeignKey("agent_space_resources.id"),
            primary_key=True,
        ),
        sa.Column("retired_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "agent_space_activations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(36),
            sa.ForeignKey("agent_space_intents.id"),
            nullable=False,
        ),
        sa.Column(
            "resource_record_id",
            sa.String(36),
            sa.ForeignKey("agent_space_resources.id"),
            nullable=False,
        ),
        sa.Column(
            "agent_instance_id",
            sa.String(36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.String(64), nullable=False),
        sa.Column("spec_sha256", sa.String(64), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("resource_sha256", sa.String(64), nullable=False),
        sa.Column("activation_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("intent_id", name="uq_space_activation_intent"),
        sa.CheckConstraint("length(activation_sha256)=64", name="ck_space_activation_digest"),
    )
    op.create_index(
        "ix_agent_space_activations_intent_id", "agent_space_activations", ["intent_id"]
    )
    op.create_index(
        "ix_agent_space_activations_resource_record_id",
        "agent_space_activations",
        ["resource_record_id"],
    )
    op.create_index(
        "ix_agent_space_activations_agent_instance_id",
        "agent_space_activations",
        ["agent_instance_id"],
    )
    op.create_table(
        "agent_space_activation_revocations",
        sa.Column(
            "activation_id",
            sa.String(36),
            sa.ForeignKey("agent_space_activations.id"),
            primary_key=True,
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=False),
    )

    # SQLite INSERT OR REPLACE bypasses DELETE triggers by default. Deny
    # duplicate replacement before the engine can delete the original row.
    for table, key in _APPEND_ONLY_TABLES:
        # Protect *every* unique key: INSERT OR REPLACE may delete an old row
        # through a secondary unique constraint while assigning a fresh PK.
        # This bypasses DELETE triggers when recursive_triggers is off.
        duplicates = [f"EXISTS (SELECT 1 FROM {table} WHERE {key}=NEW.{key})"]
        if table == "agent_space_resources":
            duplicates.append(
                "EXISTS (SELECT 1 FROM agent_space_resources WHERE "
                "project_id=NEW.project_id AND space_id=NEW.space_id AND "
                "resource_kind=NEW.resource_kind AND resource_id=NEW.resource_id)"
            )
        if table == "agent_space_activations":
            duplicates.append(
                "EXISTS (SELECT 1 FROM agent_space_activations WHERE intent_id=NEW.intent_id)"
            )
        op.execute(
            f"CREATE TRIGGER {table}_no_duplicate_insert BEFORE INSERT ON {table} "
            f"WHEN {' OR '.join(duplicates)} "
            "BEGIN SELECT RAISE(ABORT, 'Space record is append-only'); END"
        )
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Space record is immutable'); END"
            )


def downgrade() -> None:
    for table, _ in reversed(_APPEND_ONLY_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_duplicate_insert")
        for action in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{action.lower()}")
    op.drop_table("agent_space_activation_revocations")
    op.drop_index(
        "ix_agent_space_activations_agent_instance_id", table_name="agent_space_activations"
    )
    op.drop_index(
        "ix_agent_space_activations_resource_record_id", table_name="agent_space_activations"
    )
    op.drop_index("ix_agent_space_activations_intent_id", table_name="agent_space_activations")
    op.drop_table("agent_space_activations")
    op.drop_table("agent_space_resource_retirements")
    op.drop_index("ix_agent_space_resources_project_id", table_name="agent_space_resources")
    op.drop_table("agent_space_resources")
