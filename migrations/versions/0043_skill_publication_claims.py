"""Durable non-authoritative exclusive learned-skill publication claim.

Reserves a single exact procedure/version slot under Core's serialized SQLite
writer. Historical claims never become a GitHub merge authorization.
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_skill_publication_claims"
down_revision = "0042_harness_team_design_review"
branch_labels = None
depends_on = None

TABLE = "skill_publication_claims"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.String(36),
            sa.ForeignKey("skill_learning_proposals.id"),
            nullable=False,
        ),
        sa.Column(
            "agent_instance_id",
            sa.String(36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column("source_skill_id", sa.String(64), nullable=False),
        sa.Column("procedure_id", sa.String(64), nullable=False),
        sa.Column("proposed_version", sa.Integer(), nullable=False),
        sa.Column("repository", sa.String(256), nullable=False),
        sa.Column("review_fingerprint", sa.String(64), nullable=False),
        sa.Column("intent_fingerprint", sa.String(64), nullable=False),
        sa.Column("claim_fingerprint", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("prior_revision_sha256", sa.String(64), nullable=True),
        sa.Column("operator_subject", sa.String(16), nullable=False),
        sa.Column("publication_authority", sa.Boolean(), nullable=False),
        sa.Column("runtime_authority", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("proposal_id", name="uq_skill_pub_claim_proposal"),
        sa.UniqueConstraint(
            "agent_instance_id",
            "source_skill_id",
            "procedure_id",
            "proposed_version",
            name="uq_skill_pub_claim_scope_version",
        ),
        sa.UniqueConstraint("claim_fingerprint", name="uq_skill_pub_claim_digest"),
        sa.CheckConstraint(
            "proposed_version BETWEEN 1 AND 1000000", name="ck_skill_pub_claim_version"
        ),
        sa.CheckConstraint("operator_subject='ahmad'", name="ck_skill_pub_claim_owner"),
        sa.CheckConstraint("publication_authority=0", name="ck_skill_pub_claim_no_publication"),
        sa.CheckConstraint("runtime_authority=0", name="ck_skill_pub_claim_no_runtime"),
        sa.CheckConstraint(
            "length(review_fingerprint)=64 AND length(intent_fingerprint)=64 "
            "AND length(claim_fingerprint)=64 AND length(artifact_sha256)=64 "
            "AND (prior_revision_sha256 IS NULL OR length(prior_revision_sha256)=64)",
            name="ck_skill_pub_claim_digests",
        ),
    )
    op.create_index("ix_skill_pub_claims_agent_instance", TABLE, ["agent_instance_id"])

    # Prevent INSERT OR REPLACE from deleting/replacing a claimed slot even
    # under SQLite's default recursive_triggers=OFF behavior.
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_duplicate_insert BEFORE INSERT ON {TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE proposal_id=NEW.proposal_id) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE claim_fingerprint=NEW.claim_fingerprint) OR "
        f"EXISTS (SELECT 1 FROM {TABLE} WHERE "
        "agent_instance_id=NEW.agent_instance_id "
        "AND source_skill_id=NEW.source_skill_id "
        "AND procedure_id=NEW.procedure_id "
        "AND proposed_version=NEW.proposed_version) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication claims are append-only'); END"
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'Skill publication claims are immutable'); END"
        )


def downgrade() -> None:
    for suffix in ("no_duplicate_insert", "no_update", "no_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
    op.drop_index("ix_skill_pub_claims_agent_instance", table_name=TABLE)
    op.drop_table(TABLE)
