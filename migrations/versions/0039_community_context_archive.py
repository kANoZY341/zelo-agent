"""Opt-in operator-only untrusted Context Mode concept archive."""

import sqlalchemy as sa
from alembic import op

revision = "0039_community_context_archive"
down_revision = "0038_rea_target_operator_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_context_archives",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=True),
        sa.Column("scope_key", sa.String(48), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_is_partial", sa.Boolean(), nullable=False),
        sa.Column("raw_data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("scope_key", "source_ref", name="uq_context_archive_source"),
        sa.CheckConstraint(
            "scope_kind IN ('operator', 'project')", name="ck_context_archive_scope_kind"
        ),
        sa.CheckConstraint(
            "(scope_kind='operator' AND scope_id IS NULL AND scope_key='operator') OR "
            "(scope_kind='project' AND scope_id IS NOT NULL AND scope_key='project:' || scope_id)",
            name="ck_context_archive_scope",
        ),
        sa.CheckConstraint(
            "length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_context_archive_sha",
        ),
        sa.CheckConstraint("total_bytes BETWEEN 1 AND 1048576", name="ck_context_archive_bytes"),
        sa.CheckConstraint("length(raw_data)=total_bytes", name="ck_context_archive_data_length"),
        sa.CheckConstraint("line_count BETWEEN 1 AND 15000", name="ck_context_archive_lines"),
        sa.CheckConstraint("length(excerpt) <= 2048", name="ck_context_archive_excerpt"),
    )
    op.execute(
        "CREATE TRIGGER context_archive_no_update BEFORE UPDATE ON community_context_archives "
        "BEGIN SELECT RAISE(ABORT, 'context source is immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS context_archive_no_update")
    op.drop_table("community_context_archives")
