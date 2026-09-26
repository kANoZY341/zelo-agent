"""Add v0.12 Campaign leases, recovery state, and shared budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0015_campaign_controls"
down_revision = "0014_campaign_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_leases",
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            primary_key=True,
        ),
        sa.Column("scope", sa.String(length=64), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("holder_id", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("acquired_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("lease_token", name="uq_campaign_lease_token"),
        sa.CheckConstraint("generation >= 0", name="ck_campaign_lease_generation"),
        sa.CheckConstraint(
            "length(scope) BETWEEN 1 AND 64",
            name="ck_campaign_lease_scope",
        ),
        sa.CheckConstraint(
            "(holder_id IS NULL AND lease_token IS NULL AND acquired_at IS NULL "
            "AND expires_at IS NULL) OR "
            "(holder_id IS NOT NULL AND lease_token IS NOT NULL AND acquired_at IS NOT NULL "
            "AND expires_at IS NOT NULL)",
            name="ck_campaign_lease_shape",
        ),
    )
    op.create_index("ix_campaign_leases_expires_at", "campaign_leases", ["expires_at"])

    op.create_table(
        "campaign_budget_limits",
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            primary_key=True,
        ),
        sa.Column("resource", sa.String(length=64), primary_key=True),
        sa.Column("limit_units", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "limit_units >= 0",
            name="ck_campaign_budget_limit_nonnegative",
        ),
        sa.CheckConstraint(
            "length(resource) BETWEEN 1 AND 64",
            name="ck_campaign_budget_resource",
        ),
    )

    op.create_table(
        "campaign_budget_reservations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("committed_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id", "resource"],
            ["campaign_budget_limits.campaign_id", "campaign_budget_limits.resource"],
            name="fk_campaign_budget_reservation_limit",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "idempotency_key",
            name="uq_campaign_budget_reservation_idempotency",
        ),
        sa.CheckConstraint("units > 0", name="ck_campaign_budget_reservation_units"),
        sa.CheckConstraint(
            "committed_units >= 0 AND committed_units <= units",
            name="ck_campaign_budget_reservation_committed_units",
        ),
        sa.CheckConstraint(
            "status IN ('reserved','committed','released','expired')",
            name="ck_campaign_budget_reservation_status",
        ),
        sa.CheckConstraint(
            "status = 'committed' OR committed_units = 0",
            name="ck_campaign_budget_reservation_terminal_units",
        ),
    )
    op.create_index(
        "ix_campaign_budget_reservations_campaign_id",
        "campaign_budget_reservations",
        ["campaign_id"],
    )
    op.create_index(
        "ix_campaign_budget_reservations_resource",
        "campaign_budget_reservations",
        ["resource"],
    )
    op.create_index(
        "ix_campaign_budget_reservations_status",
        "campaign_budget_reservations",
        ["status"],
    )
    op.create_index(
        "ix_campaign_budget_reservations_expires_at",
        "campaign_budget_reservations",
        ["expires_at"],
    )

    op.execute(
        """
        CREATE TRIGGER campaign_leases_no_delete
        BEFORE DELETE ON campaign_leases
        BEGIN
            SELECT RAISE(ABORT, 'Campaign lease generations are durable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_leases_generation_guard
        BEFORE UPDATE ON campaign_leases
        FOR EACH ROW
        WHEN NEW.generation < OLD.generation OR NEW.generation > OLD.generation + 1
        BEGIN
            SELECT RAISE(ABORT, 'Invalid campaign lease generation transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_leases_free_acquire_guard
        BEFORE UPDATE ON campaign_leases
        FOR EACH ROW
        WHEN OLD.holder_id IS NULL
             AND NEW.holder_id IS NOT NULL
             AND NEW.generation <> OLD.generation + 1
        BEGIN
            SELECT RAISE(ABORT, 'Lease acquisition must advance the generation');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_leases_holder_guard
        BEFORE UPDATE ON campaign_leases
        FOR EACH ROW
        WHEN OLD.holder_id IS NOT NULL
             AND NEW.holder_id IS NOT NULL
             AND NEW.generation = OLD.generation
             AND (
                 NEW.holder_id <> OLD.holder_id
                 OR NEW.lease_token <> OLD.lease_token
                 OR NEW.acquired_at <> OLD.acquired_at
             )
        BEGIN
            SELECT RAISE(ABORT, 'Lease holder identity is fenced by generation');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_leases_handoff_guard
        BEFORE UPDATE ON campaign_leases
        FOR EACH ROW
        WHEN OLD.holder_id IS NOT NULL
             AND NEW.holder_id IS NOT NULL
             AND NEW.generation = OLD.generation + 1
             AND OLD.expires_at > CURRENT_TIMESTAMP
        BEGIN
            SELECT RAISE(ABORT, 'Active campaign lease cannot be stolen');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER campaign_budget_limits_no_delete
        BEFORE DELETE ON campaign_budget_limits
        BEGIN
            SELECT RAISE(ABORT, 'Campaign budget limits are durable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_budget_limits_identity_guard
        BEFORE UPDATE ON campaign_budget_limits
        FOR EACH ROW
        WHEN NEW.campaign_id <> OLD.campaign_id OR NEW.resource <> OLD.resource
        BEGIN
            SELECT RAISE(ABORT, 'Campaign budget identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_budget_limits_capacity_guard
        BEFORE UPDATE OF limit_units ON campaign_budget_limits
        FOR EACH ROW
        WHEN NEW.limit_units < (
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN status = 'reserved' THEN units
                        WHEN status = 'committed' THEN committed_units
                        ELSE 0
                    END
                ),
                0
            )
            FROM campaign_budget_reservations
            WHERE campaign_id = OLD.campaign_id AND resource = OLD.resource
        )
        BEGIN
            SELECT RAISE(ABORT, 'Budget limit is below allocated units');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER campaign_budget_reservations_insert_guard
        BEFORE INSERT ON campaign_budget_reservations
        FOR EACH ROW
        WHEN NEW.status <> 'reserved' OR NEW.committed_units <> 0
        BEGIN
            SELECT RAISE(ABORT, 'Budget reservations must start reserved');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_budget_reservations_capacity_guard
        BEFORE INSERT ON campaign_budget_reservations
        FOR EACH ROW
        WHEN
            (SELECT limit_units
             FROM campaign_budget_limits
             WHERE campaign_id = NEW.campaign_id AND resource = NEW.resource) IS NULL
            OR NEW.units + (
                SELECT COALESCE(
                    SUM(
                        CASE
                            WHEN status = 'reserved' THEN units
                            WHEN status = 'committed' THEN committed_units
                            ELSE 0
                        END
                    ),
                    0
                )
                FROM campaign_budget_reservations
                WHERE campaign_id = NEW.campaign_id AND resource = NEW.resource
            ) > (
                SELECT limit_units
                FROM campaign_budget_limits
                WHERE campaign_id = NEW.campaign_id AND resource = NEW.resource
            )
        BEGIN
            SELECT RAISE(ABORT, 'Campaign budget exceeded');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_budget_reservations_update_guard
        BEFORE UPDATE ON campaign_budget_reservations
        FOR EACH ROW
        WHEN
            NEW.id <> OLD.id
            OR NEW.campaign_id <> OLD.campaign_id
            OR NEW.resource <> OLD.resource
            OR NEW.owner_id <> OLD.owner_id
            OR NEW.idempotency_key <> OLD.idempotency_key
            OR NEW.request_hash <> OLD.request_hash
            OR NEW.units <> OLD.units
            OR NEW.expires_at <> OLD.expires_at
            OR NEW.created_at <> OLD.created_at
            OR OLD.status <> 'reserved'
            OR NEW.status NOT IN ('committed','released','expired')
        BEGIN
            SELECT RAISE(ABORT, 'Invalid budget reservation transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_budget_reservations_no_delete
        BEFORE DELETE ON campaign_budget_reservations
        BEGIN
            SELECT RAISE(ABORT, 'Campaign budget reservations are durable');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
