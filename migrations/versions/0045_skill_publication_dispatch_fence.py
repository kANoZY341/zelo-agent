"""Append-only, non-dispatching fence for one reviewed learned Skill merge.

This reserves one exact operator-selected external merge action after current
provider byte/CI checks. It NEVER sends a GitHub request or authorizes it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_skill_publication_dispatch_fence"
down_revision = "0044_skill_publication_operator_decisions"
branch_labels = None
depends_on = None

TABLE = "skill_publication_dispatch_fences"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_operator_decisions.id"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("skill_publication_claims.id"),
            nullable=False,
        ),
        sa.Column("decision_fingerprint", sa.String(64), nullable=False),
        sa.Column("repository", sa.String(256), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_head_sha", sa.String(40), nullable=False),
        sa.Column("pr_base_sha", sa.String(40), nullable=False),
        sa.Column("merge_request_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("git_blob_oid", sa.String(40), nullable=False),
        sa.Column("fence_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("remote_verified_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("network_dispatch_authorized", sa.Boolean(), nullable=False),
        sa.Column("network_dispatch_started", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("decision_id", name="uq_skill_pub_dispatch_decision"),
        sa.UniqueConstraint("claim_id", name="uq_skill_pub_dispatch_claim"),
        sa.UniqueConstraint("repository", "pr_number", name="uq_skill_pub_dispatch_pr"),
        sa.UniqueConstraint("fence_fingerprint", name="uq_skill_pub_dispatch_digest"),
        sa.CheckConstraint("pr_number >= 1", name="ck_skill_pub_dispatch_pr_positive"),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_dispatch_owner"),
        sa.CheckConstraint("phase='reserved_no_send'", name="ck_skill_pub_dispatch_inert"),
        sa.CheckConstraint(
            "network_dispatch_authorized=0", name="ck_skill_pub_dispatch_no_authority"
        ),
        sa.CheckConstraint("network_dispatch_started=0", name="ck_skill_pub_dispatch_not_started"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_dispatch_no_runtime"),
        sa.CheckConstraint(
            "length(decision_fingerprint)=64 AND length(merge_request_sha256)=64 "
            "AND length(artifact_sha256)=64 AND length(fence_fingerprint)=64 "
            "AND length(pr_head_sha)=40 AND length(pr_base_sha)=40 "
            "AND length(git_blob_oid)=40",
            name="ck_skill_pub_dispatch_lengths",
        ),
    )
    op.create_index("ix_skill_pub_dispatch_claim", TABLE, ["claim_id"])
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_duplicate_insert BEFORE INSERT ON {TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE decision_id=NEW.decision_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE claim_id=NEW.claim_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE repository=NEW.repository "
        "AND pr_number=NEW.pr_number) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE fence_fingerprint=NEW.fence_fingerprint) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication dispatch fences are append-only'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'Skill publication dispatch fences are immutable'); END"
        )


def downgrade() -> None:
    for suffix in ("no_duplicate_insert", "no_update", "no_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
    op.drop_index("ix_skill_pub_dispatch_claim", table_name=TABLE)
    op.drop_table(TABLE)
