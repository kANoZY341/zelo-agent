"""Add bounded Campaign schedules and durable Mind wake runs."""

import sqlalchemy as sa
from alembic import op

revision = "0016_scheduler_wakes"
down_revision = "0015_campaign_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_schedules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("prompt", sa.String(length=16384), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("max_runs", sa.Integer(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','completed','cancelled')",
            name="ck_campaign_schedule_status",
        ),
        sa.CheckConstraint(
            "max_runs BETWEEN 1 AND 100",
            name="ck_campaign_schedule_max_runs",
        ),
        sa.CheckConstraint(
            "run_count >= 0 AND run_count <= max_runs",
            name="ck_campaign_schedule_run_count",
        ),
        sa.CheckConstraint(
            "(interval_seconds IS NULL AND max_runs = 1) OR "
            "(interval_seconds BETWEEN 60 AND 86400 AND max_runs > 1)",
            name="ck_campaign_schedule_cadence",
        ),
    )
    op.create_index(
        "ix_campaign_schedules_campaign_id",
        "campaign_schedules",
        ["campaign_id"],
    )
    op.create_index(
        "ix_campaign_schedules_conversation_id",
        "campaign_schedules",
        ["conversation_id"],
    )
    op.create_index(
        "ix_campaign_schedules_status",
        "campaign_schedules",
        ["status"],
    )
    op.create_index(
        "ix_campaign_schedules_next_run_at",
        "campaign_schedules",
        ["next_run_at"],
    )

    op.create_table(
        "campaign_schedule_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.String(length=36),
            sa.ForeignKey("campaign_schedules.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lease_holder_id", sa.String(length=128), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column(
            "budget_reservation_id",
            sa.String(length=36),
            sa.ForeignKey("campaign_budget_reservations.id"),
            nullable=True,
        ),
        sa.Column(
            "conversation_turn_id",
            sa.String(length=36),
            sa.ForeignKey("conversation_turns.id"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(length=256), nullable=True),
        sa.UniqueConstraint(
            "schedule_id",
            "sequence",
            name="uq_campaign_schedule_run_sequence",
        ),
        sa.UniqueConstraint(
            "budget_reservation_id",
            name="uq_campaign_schedule_run_budget_reservation",
        ),
        sa.UniqueConstraint(
            "conversation_turn_id",
            name="uq_campaign_schedule_run_conversation_turn",
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_campaign_schedule_run_sequence",
        ),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','interrupted')",
            name="ck_campaign_schedule_run_status",
        ),
        sa.CheckConstraint(
            "lease_generation >= 1",
            name="ck_campaign_schedule_run_lease_generation",
        ),
    )
    op.create_index(
        "ix_campaign_schedule_runs_schedule_id",
        "campaign_schedule_runs",
        ["schedule_id"],
    )
    op.create_index(
        "ix_campaign_schedule_runs_status",
        "campaign_schedule_runs",
        ["status"],
    )

    op.execute(
        """
        CREATE TRIGGER campaign_schedules_identity_guard
        BEFORE UPDATE ON campaign_schedules
        FOR EACH ROW
        WHEN
            NEW.id <> OLD.id
            OR NEW.campaign_id <> OLD.campaign_id
            OR NEW.conversation_id <> OLD.conversation_id
            OR NEW.prompt <> OLD.prompt
            OR COALESCE(NEW.interval_seconds, -1) <> COALESCE(OLD.interval_seconds, -1)
            OR NEW.max_runs <> OLD.max_runs
            OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'Campaign schedule identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_schedules_progress_guard
        BEFORE UPDATE ON campaign_schedules
        FOR EACH ROW
        WHEN
            NEW.run_count < OLD.run_count
            OR NEW.run_count > OLD.run_count + 1
            OR (
                OLD.status IN ('completed','cancelled')
                AND NEW.status <> OLD.status
            )
            OR (
                OLD.status = 'active'
                AND NEW.status NOT IN ('active','completed','cancelled')
            )
        BEGIN
            SELECT RAISE(ABORT, 'Invalid Campaign schedule transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_schedules_no_delete
        BEFORE DELETE ON campaign_schedules
        BEGIN
            SELECT RAISE(ABORT, 'Campaign schedules are durable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER campaign_schedule_runs_update_guard
        BEFORE UPDATE ON campaign_schedule_runs
        FOR EACH ROW
        WHEN
            NEW.id <> OLD.id
            OR NEW.schedule_id <> OLD.schedule_id
            OR NEW.sequence <> OLD.sequence
            OR (
                OLD.status IN ('succeeded','failed')
                AND (
                    NEW.status <> OLD.status
                    OR COALESCE(NEW.budget_reservation_id, '') <>
                       COALESCE(OLD.budget_reservation_id, '')
                    OR COALESCE(NEW.conversation_turn_id, '') <>
                       COALESCE(OLD.conversation_turn_id, '')
                    OR NEW.lease_holder_id <> OLD.lease_holder_id
                    OR NEW.lease_token <> OLD.lease_token
                    OR NEW.lease_generation <> OLD.lease_generation
                    OR NEW.started_at <> OLD.started_at
                    OR COALESCE(NEW.completed_at, '') <> COALESCE(OLD.completed_at, '')
                    OR COALESCE(NEW.error, '') <> COALESCE(OLD.error, '')
                )
            )
            OR (
                OLD.status = 'running'
                AND NEW.status NOT IN ('running','succeeded','failed','interrupted')
            )
            OR (
                OLD.status = 'interrupted'
                AND NEW.status NOT IN ('interrupted','running')
            )
        BEGIN
            SELECT RAISE(ABORT, 'Invalid Campaign schedule run transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_schedule_runs_no_delete
        BEFORE DELETE ON campaign_schedule_runs
        BEGIN
            SELECT RAISE(ABORT, 'Campaign schedule runs are durable');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
