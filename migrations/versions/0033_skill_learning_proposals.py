"""Add inert, immutable skill-learning proposals and append-only status history."""

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
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("proposal_id", "sequence", name="uq_skill_learning_event_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_skill_learning_event_sequence"),
        sa.CheckConstraint("event = 'proposed'", name="ck_skill_learning_event_name"),
    )
    op.create_index(
        "ix_skill_learning_events_proposal_id", "skill_learning_events", ["proposal_id"]
    )
    op.execute(
        """CREATE TRIGGER skill_learning_events_first_only
        BEFORE INSERT ON skill_learning_events
        WHEN NEW.sequence <> 1 OR EXISTS (
            SELECT 1 FROM skill_learning_events WHERE proposal_id = NEW.proposal_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Skill-learning status sequence invalid');
        END"""
    )
    for table in ("skill_learning_proposals", "skill_learning_events"):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Skill-learning records are append-only'); END"
            )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
