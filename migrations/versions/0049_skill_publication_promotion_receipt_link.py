"""Append-only local Skill publication link; never authorizes Skill execution."""

import sqlalchemy as sa
from alembic import op

revision = "0049_skill_publication_promotion_receipt_link"
down_revision = "0048_python_agent_execution_kind"
branch_labels = None
depends_on = None

TABLE = "skill_publication_promotion_receipt_links"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "claim_id", sa.String(36), sa.ForeignKey("skill_publication_claims.id"), nullable=False
        ),
        sa.Column(
            "proposal_id",
            sa.String(36),
            sa.ForeignKey("skill_learning_proposals.id"),
            nullable=False,
        ),
        sa.Column(
            "receipt_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_merge_receipts.id"),
            nullable=False,
        ),
        sa.Column(
            "agent_instance_id", sa.String(36), sa.ForeignKey("agent_instances.id"), nullable=False
        ),
        sa.Column("source_skill_id", sa.String(64), nullable=False),
        sa.Column("procedure_id", sa.String(64), nullable=False),
        sa.Column("proposed_version", sa.Integer(), nullable=False),
        sa.Column("intent_fingerprint", sa.String(64), nullable=False),
        sa.Column("receipt_fingerprint", sa.String(64), nullable=False),
        sa.Column("repository", sa.String(256), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_head_sha", sa.String(40), nullable=False),
        sa.Column("merge_commit_sha", sa.String(40), nullable=False),
        sa.Column("revision_sha256", sa.String(64), nullable=False),
        sa.Column("link_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("claim_id", name="uq_skill_pub_link_claim"),
        sa.UniqueConstraint("proposal_id", name="uq_skill_pub_link_proposal"),
        sa.UniqueConstraint("receipt_id", name="uq_skill_pub_link_receipt"),
        sa.UniqueConstraint(
            "agent_instance_id",
            "source_skill_id",
            "procedure_id",
            "proposed_version",
            name="uq_skill_pub_link_scope_version",
        ),
        sa.UniqueConstraint("link_fingerprint", name="uq_skill_pub_link_digest"),
        sa.CheckConstraint(
            "proposed_version BETWEEN 1 AND 1000000", name="ck_skill_pub_link_version"
        ),
        sa.CheckConstraint("pr_number >= 1", name="ck_skill_pub_link_pr"),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_link_owner"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_link_no_runtime"),
        sa.CheckConstraint(
            "length(link_fingerprint)=64 AND length(receipt_fingerprint)=64 "
            "AND length(intent_fingerprint)=64 AND length(pr_head_sha)=40 "
            "AND length(merge_commit_sha)=40 AND length(revision_sha256)=64",
            name="ck_skill_pub_link_digest_lengths",
        ),
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_duplicate_insert BEFORE INSERT ON {TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE claim_id=NEW.claim_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE proposal_id=NEW.proposal_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE receipt_id=NEW.receipt_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE agent_instance_id=NEW.agent_instance_id "
        "AND source_skill_id=NEW.source_skill_id AND procedure_id=NEW.procedure_id "
        "AND proposed_version=NEW.proposed_version) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication link is append-only'); END"
    )
    # Keep direct SQL inserts from manufacturing a positive link from
    # mismatched immutable rows. The service still checks complete fingerprints
    # and live state under BEGIN IMMEDIATE.
    op.execute(
        f"CREATE TRIGGER {TABLE}_source_guard BEFORE INSERT ON {TABLE} "
        "WHEN NOT EXISTS (SELECT 1 FROM skill_publication_claims AS claim "
        "JOIN skill_publication_merge_receipts AS receipt ON receipt.id=NEW.receipt_id "
        "JOIN skill_publication_merge_attempts AS attempt ON attempt.id=receipt.attempt_id "
        "JOIN skill_publication_dispatch_fences AS fence ON fence.id=attempt.fence_id "
        "JOIN skill_publication_operator_decisions AS decision ON decision.id=fence.decision_id "
        "JOIN tasks AS source ON source.id=attempt.promotion_task_id "
        "JOIN skill_learning_proposals AS proposal ON proposal.id=NEW.proposal_id "
        "JOIN skill_learning_reviews AS review ON review.proposal_id=NEW.proposal_id "
        "WHERE claim.id=NEW.claim_id AND claim.proposal_id=NEW.proposal_id "
        "AND claim.agent_instance_id=NEW.agent_instance_id "
        "AND claim.source_skill_id=NEW.source_skill_id "
        "AND claim.procedure_id=NEW.procedure_id "
        "AND claim.proposed_version=NEW.proposed_version "
        "AND claim.intent_fingerprint=NEW.intent_fingerprint "
        "AND proposal.revision_sha256=NEW.revision_sha256 "
        "AND review.decision='accepted' AND review.reviewer_id='ahmad' "
        "AND review.review_fingerprint=claim.review_fingerprint "
        "AND receipt.claim_id=claim.id AND receipt.response_confirmed=1 "
        "AND receipt.receipt_fingerprint=NEW.receipt_fingerprint "
        "AND receipt.merge_commit_sha=NEW.merge_commit_sha "
        "AND receipt.parent_base_sha=fence.pr_base_sha "
        "AND receipt.merged_blob_oid=fence.git_blob_oid "
        "AND attempt.claim_id=claim.id AND fence.claim_id=claim.id "
        "AND attempt.fence_fingerprint=fence.fence_fingerprint "
        "AND attempt.merge_request_sha256=fence.merge_request_sha256 "
        "AND attempt.repository=fence.repository AND attempt.pr_number=fence.pr_number "
        "AND attempt.pr_head_sha=fence.pr_head_sha AND attempt.pr_base_sha=fence.pr_base_sha "
        "AND source.kind='promotion_run' AND source.status='succeeded' "
        "AND source.cancel_requested=0 "
        "AND decision.claim_id=claim.id AND decision.proposal_id=NEW.proposal_id "
        "AND decision.decision='approved_for_review' "
        "AND decision.claim_fingerprint=claim.claim_fingerprint "
        "AND decision.decision_fingerprint=fence.decision_fingerprint "
        "AND decision.merge_request_sha256=fence.merge_request_sha256 "
        "AND decision.repository=fence.repository AND decision.pr_number=fence.pr_number "
        "AND decision.pr_head_sha=fence.pr_head_sha "
        "AND decision.pr_base_sha=fence.pr_base_sha "
        "AND fence.repository=NEW.repository AND fence.pr_number=NEW.pr_number "
        "AND fence.pr_head_sha=NEW.pr_head_sha "
        "AND fence.artifact_sha256=claim.artifact_sha256) "
        "OR EXISTS (SELECT 1 FROM skill_publication_merge_failures "
        "WHERE claim_id=NEW.claim_id) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication receipt lineage invalid'); END"
    )
    # Cross-table serialization with the older generic-link ledger. A
    # positive publication receipt cannot coexist with a generic link for
    # its own or a later version, and no later generic insert may bypass it.
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_generic_conflict BEFORE INSERT ON {TABLE} "
        "WHEN EXISTS (SELECT 1 FROM skill_learning_promotion_links AS old "
        "JOIN skill_learning_proposals AS p ON p.id=old.proposal_id "
        "WHERE p.agent_instance_id=NEW.agent_instance_id "
        "AND p.source_skill_id=NEW.source_skill_id "
        "AND p.procedure_id=NEW.procedure_id "
        "AND json_extract(p.proposal_json, '$.proposed_revision.version') "
        ">= NEW.proposed_version) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication lineage conflict'); END"
    )
    op.execute(
        "CREATE TRIGGER skill_learning_promotion_links_no_publication_conflict "
        "BEFORE INSERT ON skill_learning_promotion_links "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} AS published "
        "JOIN skill_learning_proposals AS proposed ON proposed.id=NEW.proposal_id "
        "WHERE published.agent_instance_id=proposed.agent_instance_id "
        "AND published.source_skill_id=proposed.source_skill_id "
        "AND published.procedure_id=proposed.procedure_id) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication lineage conflict'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'Skill publication link is immutable'); END"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS skill_learning_promotion_links_no_publication_conflict")
    for suffix in (
        "no_duplicate_insert",
        "source_guard",
        "no_generic_conflict",
        "no_update",
        "no_delete",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
    op.drop_table(TABLE)
