"""Append-only human design review ledger, never a team activation grant."""

import sqlalchemy as sa
from alembic import op

revision = "0042_harness_team_design_review"
down_revision = "0041_space_resource_activation"
branch_labels = None
depends_on = None

TABLE = "harness_team_design_reviews"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("project_status", sa.String(16), nullable=False),
        sa.Column("template", sa.String(32), nullable=False),
        sa.Column("blueprint_sha256", sa.String(64), nullable=False),
        sa.Column("registry_version", sa.String(64), nullable=False),
        sa.Column("review_plan_sha256", sa.String(64), nullable=False),
        sa.Column("planning_reason", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("receipt_sha256", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_harness_team_review_idempotency"),
        sa.CheckConstraint(
            "decision IN ('accepted_for_design_review','rejected_for_design_review')",
            name="ck_harness_team_design_decision",
        ),
        sa.CheckConstraint(
            "length(review_plan_sha256)=64 AND length(blueprint_sha256)=64 "
            "AND length(registry_version)=64 AND length(request_sha256)=64 "
            "AND length(receipt_sha256)=64",
            name="ck_harness_team_review_digests",
        ),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_harness_team_review_operator"),
    )
    op.create_index("ix_harness_team_design_reviews_project_id", TABLE, ["project_id"])

    # Duplicate INSERT OR REPLACE must not delete an earlier review through
    # its unique idempotency key even when recursive SQLite triggers are OFF.
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_duplicate_insert BEFORE INSERT ON {TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE idempotency_key=NEW.idempotency_key) "
        "BEGIN SELECT RAISE(ABORT, 'Team review is append-only'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'Team review is immutable'); END"
        )


def downgrade() -> None:
    for suffix in ("no_duplicate_insert", "no_update", "no_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
    op.drop_index("ix_harness_team_design_reviews_project_id", table_name=TABLE)
    op.drop_table(TABLE)
