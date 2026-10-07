"""Add immutable skill-learning proposals and operator review records."""

import sqlalchemy as sa
from alembic import op

revision = "0033_skill_learning_proposals"
down_revision = "0032_computer_worker_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_learning_proposals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("proposal_fingerprint", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "agent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("agent_registry_version", sa.String(length=64), nullable=False),
        sa.Column("skill_registry_version", sa.String(length=64), nullable=False),
        sa.Column("source_skill_id", sa.String(length=64), nullable=False),
        sa.Column("source_skill_version", sa.Integer(), nullable=False),
        sa.Column("procedure_id", sa.String(length=64), nullable=False),
        sa.Column("revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("prior_revision_sha256", sa.String(length=64), nullable=True),
        sa.Column("proposal_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(proposal_json) <= 65536", name="ck_skill_proposal_size"),
        sa.CheckConstraint(
            "length(proposal_fingerprint) = 64", name="ck_skill_proposal_fingerprint"
        ),
        sa.CheckConstraint("length(revision_sha256) = 64", name="ck_skill_proposal_revision"),
        sa.CheckConstraint(
            "length(agent_registry_version) = 64", name="ck_skill_proposal_agent_registry"
        ),
        sa.CheckConstraint(
            "length(skill_registry_version) = 64", name="ck_skill_proposal_skill_registry"
        ),
    )
    op.create_index(
        "ix_skill_learning_proposals_agent_instance_id",
        "skill_learning_proposals",
        ["agent_instance_id"],
    )
    op.create_table(
        "skill_learning_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.String(length=36),
            sa.ForeignKey("skill_learning_proposals.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reviewer_id", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("review_fingerprint", sa.String(length=64), nullable=False, unique=True),
        sa.Column("review_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("reviewer_id = 'ahmad'", name="ck_skill_learning_review_reviewer"),
        sa.CheckConstraint(
            "decision IN ('accepted','rejected')", name="ck_skill_learning_review_decision"
        ),
        sa.CheckConstraint("length(review_json) <= 16384", name="ck_skill_learning_review_size"),
    )
    op.create_index(
        "ix_skill_learning_reviews_proposal_id", "skill_learning_reviews", ["proposal_id"]
    )
    op.create_table(
        "skill_learning_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.String(length=36),
            sa.ForeignKey("skill_learning_proposals.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("proposal_id", "sequence", name="uq_skill_learning_event_sequence"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 3", name="ck_skill_learning_event_sequence"),
        sa.CheckConstraint(
            "event IN ('proposed','reviewed','accepted','rejected')",
            name="ck_skill_learning_event_name",
        ),
        sa.CheckConstraint(
            "(sequence = 1 AND event = 'proposed' AND reviewer_id IS NULL) OR "
            "(sequence IN (2,3) AND reviewer_id = 'ahmad')",
            name="ck_skill_learning_event_reviewer",
        ),
    )
    op.create_index(
        "ix_skill_learning_events_proposal_id", "skill_learning_events", ["proposal_id"]
    )
    op.execute(
        """CREATE TRIGGER skill_learning_review_insert_guard
        BEFORE INSERT ON skill_learning_reviews
        WHEN (SELECT COUNT(*) FROM skill_learning_events
              WHERE proposal_id = NEW.proposal_id) <> 1
          OR (SELECT event FROM skill_learning_events
              WHERE proposal_id = NEW.proposal_id AND sequence = 1) <> 'proposed'
          OR json_extract(NEW.review_json, '$.reviewer_id') <> NEW.reviewer_id
          OR json_extract(NEW.review_json, '$.decision') <> NEW.decision
          OR (NEW.decision = 'accepted' AND (
              COALESCE(
                  json_extract(NEW.review_json, '$.checks.evidence_supports_change'), 0
              ) <> 1 OR
              COALESCE(
                  json_extract(NEW.review_json, '$.checks.no_tool_escalation'), 0
              ) <> 1 OR
              COALESCE(
                  json_extract(NEW.review_json, '$.checks.authority_envelope_respected'), 0
              ) <> 1 OR
              COALESCE(
                  json_extract(
                      NEW.review_json,
                      '$.checks.verification_and_failure_steps_explicit'
                  ),
                  0
              ) <> 1 OR
              COALESCE(
                  json_extract(
                      NEW.review_json,
                      '$.checks.no_secrets_or_unrelated_private_data'
                  ),
                  0
              ) <> 1 OR
              COALESCE(
                  json_extract(
                      NEW.review_json,
                      '$.checks.procedure_bounded_and_deterministic'
                  ),
                  0
              ) <> 1
          ))
        BEGIN
            SELECT RAISE(ABORT, 'Skill-learning review is not authorized or valid');
        END"""
    )
    op.execute(
        """CREATE TRIGGER skill_learning_events_sequence_guard
        BEFORE INSERT ON skill_learning_events
        WHEN NEW.sequence <> COALESCE(
                (SELECT MAX(sequence) + 1 FROM skill_learning_events
                 WHERE proposal_id = NEW.proposal_id), 1
            )
          OR (NEW.sequence = 1 AND NEW.event <> 'proposed')
          OR (NEW.sequence = 2 AND (
                NEW.event <> 'reviewed' OR NEW.reviewer_id <> 'ahmad' OR
                NOT EXISTS (SELECT 1 FROM skill_learning_reviews
                            WHERE proposal_id = NEW.proposal_id)
            ))
          OR (NEW.sequence = 3 AND (
                NEW.event NOT IN ('accepted','rejected') OR NEW.reviewer_id <> 'ahmad' OR
                NOT EXISTS (SELECT 1 FROM skill_learning_events
                            WHERE proposal_id = NEW.proposal_id AND sequence = 2
                              AND event = 'reviewed' AND reviewer_id = 'ahmad') OR
                NEW.event <> (SELECT decision FROM skill_learning_reviews
                              WHERE proposal_id = NEW.proposal_id)
            ))
        BEGIN
            SELECT RAISE(ABORT, 'Skill-learning status sequence invalid');
        END"""
    )
    for table in ("skill_learning_proposals", "skill_learning_reviews", "skill_learning_events"):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Skill-learning records are append-only'); END"
            )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
