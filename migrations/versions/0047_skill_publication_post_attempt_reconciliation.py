"""Append-only GET evidence following one possible external merge send."""

import sqlalchemy as sa
from alembic import op

revision = "0047_skill_publication_post_attempt_reconciliation"
down_revision = "0046_skill_publication_one_attempt"
branch_labels = None
depends_on = None

TABLE = "skill_publication_post_attempt_reconciliations"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_merge_attempts.id"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_claims.id"),
            nullable=False,
        ),
        sa.Column("attempt_fingerprint", sa.String(64), nullable=False),
        sa.Column("fence_fingerprint", sa.String(64), nullable=False),
        sa.Column("merge_request_sha256", sa.String(64), nullable=False),
        sa.Column("repository", sa.String(256), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_head_sha", sa.String(40), nullable=False),
        sa.Column("pr_base_sha", sa.String(40), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("merge_commit_sha", sa.String(40), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("observation_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("send_may_have_occurred", sa.Boolean(), nullable=False),
        sa.Column("automatic_retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("remote_actor_identity_unproven", sa.Boolean(), nullable=False),
        sa.Column("remote_merge_method_unproven", sa.Boolean(), nullable=False),
        sa.Column("no_skill_promotion_or_runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "observation_fingerprint",
            name="uq_skill_pub_post_attempt_reconcile_digest",
        ),
        sa.CheckConstraint(
            "status IN ('open_unmerged','closed_unmerged','merged_single_parent_content_observed')",
            name="ck_skill_pub_post_attempt_reconcile_status",
        ),
        sa.CheckConstraint(
            "(status='merged_single_parent_content_observed' AND merge_commit_sha IS NOT NULL) "
            "OR (status!='merged_single_parent_content_observed' AND merge_commit_sha IS NULL)",
            name="ck_skill_pub_post_attempt_reconcile_merge_sha",
        ),
        sa.CheckConstraint(
            "send_may_have_occurred=1", name="ck_skill_pub_post_attempt_may_have_sent"
        ),
        sa.CheckConstraint("automatic_retry_allowed=0", name="ck_skill_pub_post_attempt_no_retry"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_post_attempt_no_runtime"),
        sa.CheckConstraint(
            "remote_actor_identity_unproven=1",
            name="ck_skill_pub_post_attempt_actor_unproven",
        ),
        sa.CheckConstraint(
            "remote_merge_method_unproven=1",
            name="ck_skill_pub_post_attempt_method_unproven",
        ),
        sa.CheckConstraint(
            "no_skill_promotion_or_runtime_authority=1",
            name="ck_skill_pub_post_attempt_no_activation",
        ),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_post_attempt_owner"),
        sa.CheckConstraint(
            "pr_number >= 1 AND length(pr_head_sha)=40 AND length(pr_base_sha)=40 "
            "AND length(attempt_fingerprint)=64 AND length(fence_fingerprint)=64 "
            "AND length(merge_request_sha256)=64 AND length(observation_fingerprint)=64 "
            "AND (merge_commit_sha IS NULL OR length(merge_commit_sha)=40)",
            name="ck_skill_pub_post_attempt_digest_lengths",
        ),
    )
    op.create_index(
        "ix_skill_publication_post_attempt_reconciliations_attempt_id",
        TABLE,
        ["attempt_id"],
    )
    op.create_index(
        "ix_skill_publication_post_attempt_reconciliations_claim_id",
        TABLE,
        ["claim_id"],
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_duplicate_insert BEFORE INSERT ON {TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} "
        "WHERE observation_fingerprint=NEW.observation_fingerprint) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication reconciliation history is append-only'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'Skill publication reconciliation "
            "history is immutable'); END"
        )


def downgrade() -> None:
    for suffix in ("no_duplicate_insert", "no_update", "no_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
    op.drop_index("ix_skill_publication_post_attempt_reconciliations_claim_id", table_name=TABLE)
    op.drop_index("ix_skill_publication_post_attempt_reconciliations_attempt_id", table_name=TABLE)
    op.drop_table(TABLE)
