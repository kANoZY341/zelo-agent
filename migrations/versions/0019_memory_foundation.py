"""Add typed durable memory records for v0.13."""

import sqlalchemy as sa
from alembic import op

revision = "0019_memory_foundation"
down_revision = "0018_autonomous_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provenance_kind", sa.String(length=32), nullable=False),
        sa.Column("provenance_ref", sa.String(length=256), nullable=True),
        sa.Column("provenance_note", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "replaces_id",
            sa.String(length=36),
            sa.ForeignKey("memory_records.id"),
            nullable=True,
        ),
        sa.Column("replacement_kind", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("replaces_id", name="uq_memory_record_replaces"),
        sa.CheckConstraint(
            "scope_kind IN ('personal','project','agent')",
            name="ck_memory_record_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'personal' AND scope_id IS NULL) OR "
            "(scope_kind IN ('project','agent') AND scope_id IS NOT NULL)",
            name="ck_memory_record_scope_shape",
        ),
        sa.CheckConstraint(
            "kind IN ('fact','preference','decision','experience')",
            name="ck_memory_record_kind",
        ),
        sa.CheckConstraint(
            "provenance_kind IN ('operator','conversation_turn','task','project','agent','import')",
            name="ck_memory_record_provenance_kind",
        ),
        sa.CheckConstraint(
            "(replaces_id IS NULL AND replacement_kind IS NULL) OR "
            "(replaces_id IS NOT NULL AND replacement_kind IN ('correction','supersession'))",
            name="ck_memory_record_replacement_shape",
        ),
    )
    op.create_index("ix_memory_records_scope_kind", "memory_records", ["scope_kind"])
    op.create_index("ix_memory_records_scope_id", "memory_records", ["scope_id"])
    op.create_index("ix_memory_records_kind", "memory_records", ["kind"])
    op.create_index("ix_memory_records_expires_at", "memory_records", ["expires_at"])

    op.execute(
        """
        CREATE TRIGGER memory_records_replacement_scope_guard
        BEFORE INSERT ON memory_records
        FOR EACH ROW
        WHEN NEW.replaces_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1
            FROM memory_records AS target
            WHERE target.id = NEW.replaces_id
              AND target.scope_kind = NEW.scope_kind
              AND (
                target.scope_id = NEW.scope_id
                OR (target.scope_id IS NULL AND NEW.scope_id IS NULL)
              )
         )
        BEGIN
            SELECT RAISE(ABORT, 'Memory replacement must stay in the same scope');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER memory_records_existing_replacement_guard
        BEFORE INSERT ON memory_records
        FOR EACH ROW
        WHEN NEW.replaces_id IS NOT NULL
         AND EXISTS (
            SELECT 1 FROM memory_records WHERE replaces_id = NEW.replaces_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'Memory has already been replaced');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER memory_records_existing_id_guard
        BEFORE INSERT ON memory_records
        FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM memory_records WHERE id = NEW.id)
        BEGIN
            SELECT RAISE(ABORT, 'Memory record id already exists');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER memory_records_project_scope_guard
        BEFORE INSERT ON memory_records
        FOR EACH ROW
        WHEN NEW.scope_kind = 'project'
         AND NOT EXISTS (SELECT 1 FROM projects WHERE id = NEW.scope_id)
        BEGIN
            SELECT RAISE(ABORT, 'Project-scoped memory requires an existing Project');
        END
        """
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER memory_records_no_{operation.lower()} "
            f"BEFORE {operation} ON memory_records "
            "BEGIN SELECT RAISE(ABORT, 'Memory records are append-only'); END"
        )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
