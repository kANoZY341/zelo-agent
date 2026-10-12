"""Append-only operator decision for one reviewed Skill publication candidate.

This records approval to CONSIDER a future separately qualified merge. It
never enables the protected generic merger, GitHub writes or Skill activation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_skill_publication_operator_decisions"
down_revision = "0043_skill_publication_claims"
branch_labels = None
depends_on = None

TABLE = "skill_publication_operator_decisions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_claims.id"),
            nullable=False,
        ),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("claim_fingerprint", sa.String(64), nullable=False),
        sa.Column("repository", sa.String(256), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_head_sha", sa.String(40), nullable=False),
        sa.Column("pr_base_sha", sa.String(40), nullable=False),
        sa.Column("merge_request_sha256", sa.String(64), nullable=False),
        sa.Column("provider_verified_ci_sha256", sa.String(64), nullable=False),
        sa.Column("decision_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("remote_checked_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("publication_authority", sa.Boolean(), nullable=False),
        sa.Column("external_merge_authority", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("claim_id", name="uq_skill_pub_decision_claim"),
        sa.UniqueConstraint("repository", "pr_number", name="uq_skill_pub_decision_pr"),
        sa.UniqueConstraint("decision_fingerprint", name="uq_skill_pub_decision_digest"),
        sa.CheckConstraint("pr_number >= 1", name="ck_skill_pub_decision_pr_positive"),
        sa.CheckConstraint("decision='approved_for_review'", name="ck_skill_pub_decision_choice"),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_decision_owner"),
        sa.CheckConstraint("publication_authority=0", name="ck_skill_pub_decision_no_publication"),
        sa.CheckConstraint("external_merge_authority=0", name="ck_skill_pub_decision_no_merge"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_decision_no_runtime"),
        sa.CheckConstraint(
            "length(claim_fingerprint)=64 AND length(merge_request_sha256)=64 "
            "AND length(provider_verified_ci_sha256)=64 "
            "AND length(decision_fingerprint)=64 AND length(pr_head_sha)=40 "
            "AND length(pr_base_sha)=40",
            name="ck_skill_pub_decision_lengths",
        ),
    )
    op.create_index("ix_skill_pub_decisions_proposal_id", TABLE, ["proposal_id"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_duplicate_insert BEFORE INSERT ON {TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE claim_id=NEW.claim_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE repository=NEW.repository "
        "AND pr_number=NEW.pr_number) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE decision_fingerprint=NEW.decision_fingerprint) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication operator decisions are append-only'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'Skill publication operator decisions are immutable'); END"
        )


def downgrade() -> None:
    for suffix in ("no_duplicate_insert", "no_update", "no_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
    op.drop_index("ix_skill_pub_decisions_proposal_id", table_name=TABLE)
    op.drop_table(TABLE)
