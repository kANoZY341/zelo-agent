"""Operator-asserted inert REA target selections with final revocation.

No Core Worker, Task, approval or analyzer execution permissions are created.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_rea_target_operator_selection"
down_revision = "0037_channel_operator_account_selection"
branch_labels = None
depends_on = None


def _snapshot_id_constraint(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "length(snapshot_id)=39 AND substr(snapshot_id,1,7)='target-' "
        "AND substr(snapshot_id,8) NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def _hash_constraint(field: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({field})=64 AND {field} NOT GLOB '*[^0-9a-f]*'",
        name=f"ck_rea_target_selection_{field}",
    )


def upgrade() -> None:
    op.create_table(
        "rea_operator_target_selections",
        sa.Column("snapshot_id", sa.String(39), primary_key=True),
        sa.Column("receipt_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("review_fingerprint", sa.String(64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("selected_at", sa.DateTime(), nullable=False),
        sa.Column("execution_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("worker_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        _snapshot_id_constraint("ck_rea_target_selection_snapshot_id"),
        *(
            _hash_constraint(field)
            for field in ("receipt_sha256", "content_sha256", "review_fingerprint")
        ),
        sa.CheckConstraint(
            "file_count BETWEEN 1 AND 128", name="ck_rea_target_selection_file_count"
        ),
        sa.CheckConstraint(
            "total_bytes BETWEEN 0 AND 4194304", name="ck_rea_target_selection_total_bytes"
        ),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_rea_target_selection_operator"),
        sa.CheckConstraint("execution_authority=0", name="ck_rea_target_selection_no_execution"),
        sa.CheckConstraint("worker_authority=0", name="ck_rea_target_selection_no_worker"),
        sa.CheckConstraint("approval_authority=0", name="ck_rea_target_selection_no_approval"),
        sqlite_with_rowid=False,
    )
    op.create_table(
        "rea_operator_target_revocations",
        sa.Column(
            "snapshot_id",
            sa.String(39),
            sa.ForeignKey("rea_operator_target_selections.snapshot_id"),
            primary_key=True,
        ),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=False),
        _snapshot_id_constraint("ck_rea_target_revocation_snapshot_id"),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_rea_target_revocation_operator"),
        sqlite_with_rowid=False,
    )
    for table in ("rea_operator_target_selections", "rea_operator_target_revocations"):
        op.execute(
            f"CREATE TRIGGER {table}_no_duplicate_insert "
            f"BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE snapshot_id = NEW.snapshot_id) "
            "BEGIN SELECT RAISE(ABORT, 'REA target selection is append-only'); END"
        )
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'REA target selection is append-only'); END"
            )


def downgrade() -> None:
    for table in ("rea_operator_target_revocations", "rea_operator_target_selections"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_duplicate_insert")
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation.lower()}")
    op.drop_table("rea_operator_target_revocations")
    op.drop_table("rea_operator_target_selections")
