"""Durable single-attempt GitHub merge fence, never a retry authorization.

This table is written BEFORE the sole external PUT. A crash after commit
leaves it terminally unresolved. The separate immutable receipt records only
a fully verified response. Both are append-only, even with INSERT OR REPLACE.
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_skill_publication_one_attempt"
down_revision = "0045_skill_publication_dispatch_fence"
branch_labels = None
depends_on = None

ATTEMPTS = "skill_publication_merge_attempts"
RECEIPTS = "skill_publication_merge_receipts"
FAILURES = "skill_publication_merge_failures"


def _append_only(table: str, parts: tuple[str, ...]) -> None:
    conditions = " OR ".join(
        "EXISTS (SELECT 1 FROM " + table + " WHERE " + predicate + ")" for predicate in parts
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_duplicate_insert BEFORE INSERT ON {table} "
        f"WHEN {conditions} "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication merge history is append-only'); END"
    )
    for verb in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'Skill publication merge history is immutable'); END"
        )


def upgrade() -> None:
    op.create_table(
        ATTEMPTS,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "fence_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_dispatch_fences.id"),
            nullable=False,
        ),
        sa.Column(
            "claim_id", sa.String(36), sa.ForeignKey("skill_publication_claims.id"), nullable=False
        ),
        sa.Column("promotion_task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("repository", sa.String(256), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_head_sha", sa.String(40), nullable=False),
        sa.Column("pr_base_sha", sa.String(40), nullable=False),
        sa.Column("fence_fingerprint", sa.String(64), nullable=False),
        sa.Column("merge_request_sha256", sa.String(64), nullable=False),
        sa.Column("attempt_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("send_may_have_occurred", sa.Boolean(), nullable=False),
        sa.Column("automatic_retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fence_id", name="uq_skill_pub_attempt_fence"),
        sa.UniqueConstraint("claim_id", name="uq_skill_pub_attempt_claim"),
        sa.UniqueConstraint("repository", "pr_number", name="uq_skill_pub_attempt_pr"),
        sa.UniqueConstraint("attempt_fingerprint", name="uq_skill_pub_attempt_digest"),
        sa.CheckConstraint("phase='send_outcome_unresolved'", name="ck_skill_pub_attempt_phase"),
        sa.CheckConstraint("send_may_have_occurred=1", name="ck_skill_pub_attempt_may_have_sent"),
        sa.CheckConstraint("automatic_retry_allowed=0", name="ck_skill_pub_attempt_no_retry"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_attempt_no_runtime"),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_attempt_owner"),
        sa.CheckConstraint("pr_number >= 1", name="ck_skill_pub_attempt_pr_positive"),
        sa.CheckConstraint(
            "length(pr_head_sha)=40 AND length(pr_base_sha)=40 "
            "AND length(fence_fingerprint)=64 AND length(merge_request_sha256)=64 "
            "AND length(attempt_fingerprint)=64",
            name="ck_skill_pub_attempt_digest_lengths",
        ),
    )
    op.create_index("ix_skill_pub_attempt_task", ATTEMPTS, ["promotion_task_id"])
    _append_only(
        ATTEMPTS,
        (
            "id=NEW.id",
            "fence_id=NEW.fence_id",
            "claim_id=NEW.claim_id",
            "repository=NEW.repository AND pr_number=NEW.pr_number",
            "attempt_fingerprint=NEW.attempt_fingerprint",
        ),
    )

    op.create_table(
        RECEIPTS,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey(f"{ATTEMPTS}.id"), nullable=False),
        sa.Column(
            "claim_id", sa.String(36), sa.ForeignKey("skill_publication_claims.id"), nullable=False
        ),
        sa.Column("merge_commit_sha", sa.String(40), nullable=False),
        sa.Column("parent_base_sha", sa.String(40), nullable=False),
        sa.Column("merged_blob_oid", sa.String(40), nullable=False),
        sa.Column("receipt_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("response_confirmed", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("attempt_id", name="uq_skill_pub_merge_receipt_attempt"),
        sa.UniqueConstraint("claim_id", name="uq_skill_pub_merge_receipt_claim"),
        sa.UniqueConstraint("receipt_fingerprint", name="uq_skill_pub_merge_receipt_digest"),
        sa.CheckConstraint("response_confirmed=1", name="ck_skill_pub_receipt_response"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_receipt_no_runtime"),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_receipt_owner"),
        sa.CheckConstraint(
            "length(merge_commit_sha)=40 AND length(parent_base_sha)=40 "
            "AND length(merged_blob_oid)=40 AND length(receipt_fingerprint)=64",
            name="ck_skill_pub_receipt_digest_lengths",
        ),
    )
    _append_only(
        RECEIPTS,
        (
            "id=NEW.id",
            "attempt_id=NEW.attempt_id",
            "claim_id=NEW.claim_id",
            "receipt_fingerprint=NEW.receipt_fingerprint",
        ),
    )

    op.create_table(
        FAILURES,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey(f"{ATTEMPTS}.id"), nullable=False),
        sa.Column(
            "claim_id", sa.String(36), sa.ForeignKey("skill_publication_claims.id"), nullable=False
        ),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("failure_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("automatic_retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("attempt_id", name="uq_skill_pub_merge_failure_attempt"),
        sa.UniqueConstraint("claim_id", name="uq_skill_pub_merge_failure_claim"),
        sa.UniqueConstraint("failure_fingerprint", name="uq_skill_pub_merge_failure_digest"),
        sa.CheckConstraint(
            "phase IN ('put_request','put_response','post_pr','post_commit',"
            "'post_tree','receipt_persistence')",
            name="ck_skill_pub_failure_phase",
        ),
        sa.CheckConstraint(
            "reason IN ('provider_exception','response_invalid','pr_mismatch',"
            "'parent_mismatch','blob_mismatch','storage_failure','unclassified')",
            name="ck_skill_pub_failure_reason",
        ),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_failure_owner"),
        sa.CheckConstraint("automatic_retry_allowed=0", name="ck_skill_pub_failure_no_retry"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_failure_no_runtime"),
        sa.CheckConstraint(
            "length(failure_fingerprint)=64", name="ck_skill_pub_failure_digest_length"
        ),
    )
    _append_only(
        FAILURES,
        (
            "id=NEW.id",
            "attempt_id=NEW.attempt_id",
            "claim_id=NEW.claim_id",
            "failure_fingerprint=NEW.failure_fingerprint",
        ),
    )


def downgrade() -> None:
    for table in (FAILURES, RECEIPTS, ATTEMPTS):
        for suffix in ("no_duplicate_insert", "no_update", "no_delete"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_{suffix}")
    op.drop_table(FAILURES)
    op.drop_table(RECEIPTS)
    op.drop_index("ix_skill_pub_attempt_task", table_name=ATTEMPTS)
    op.drop_table(ATTEMPTS)
