"""Add bounded parent-child agent delegation contracts for v0.14."""

import sqlalchemy as sa
from alembic import op

revision = "0021_agent_delegations"
down_revision = "0020_agent_instances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_delegations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "parent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column(
            "child_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            nullable=False,
        ),
        sa.Column(
            "budget_reservation_id",
            sa.String(length=36),
            sa.ForeignKey("campaign_budget_reservations.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("allowed_task_kinds", sa.JSON(), nullable=False),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("budget_resource", sa.String(length=64), nullable=False),
        sa.Column("budget_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_reason", sa.String(length=512), nullable=True),
        sa.UniqueConstraint(
            "parent_instance_id",
            "idempotency_key",
            name="uq_agent_delegation_idempotency",
        ),
        sa.UniqueConstraint(
            "budget_reservation_id",
            name="uq_agent_delegation_budget_reservation",
        ),
        sa.CheckConstraint(
            "parent_instance_id <> child_instance_id",
            name="ck_agent_delegation_not_self",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_agent_delegation_status",
        ),
        sa.CheckConstraint(
            "depth >= 1 AND depth <= max_depth AND max_depth <= 4",
            name="ck_agent_delegation_depth",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_agent_delegation_revocation_shape",
        ),
    )
    op.create_index(
        "ix_agent_delegations_parent_instance_id",
        "agent_delegations",
        ["parent_instance_id"],
    )
    op.create_index(
        "ix_agent_delegations_child_instance_id",
        "agent_delegations",
        ["child_instance_id"],
    )
    op.create_index(
        "ix_agent_delegations_campaign_id",
        "agent_delegations",
        ["campaign_id"],
    )
    op.create_index("ix_agent_delegations_status", "agent_delegations", ["status"])
    op.create_index(
        "uq_agent_delegations_active_child",
        "agent_delegations",
        ["child_instance_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )

    op.execute(
        """
        CREATE TRIGGER agent_delegations_identity_immutable
        BEFORE UPDATE ON agent_delegations
        FOR EACH ROW
        WHEN NEW.parent_instance_id <> OLD.parent_instance_id
          OR NEW.child_instance_id <> OLD.child_instance_id
          OR NEW.campaign_id <> OLD.campaign_id
          OR NEW.budget_reservation_id <> OLD.budget_reservation_id
          OR NEW.idempotency_key <> OLD.idempotency_key
          OR NEW.request_hash <> OLD.request_hash
          OR NEW.objective <> OLD.objective
          OR NEW.allowed_task_kinds <> OLD.allowed_task_kinds
          OR NEW.required_capabilities <> OLD.required_capabilities
          OR NEW.depth <> OLD.depth
          OR NEW.max_depth <> OLD.max_depth
          OR NEW.budget_resource <> OLD.budget_resource
          OR NEW.budget_units <> OLD.budget_units
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'Agent delegation contract is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_status_transition_guard
        BEFORE UPDATE ON agent_delegations
        FOR EACH ROW
        WHEN NEW.status <> OLD.status
         AND NOT (
            (OLD.status = 'active' AND NEW.status = 'revoked'
             AND NEW.revoked_at IS NOT NULL)
            OR (OLD.status = 'revoked' AND NEW.status = 'revoked'
                AND NEW.revoked_at = OLD.revoked_at
                AND NEW.revocation_reason IS OLD.revocation_reason)
        )
        BEGIN
            SELECT RAISE(ABORT, 'Invalid agent delegation transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_no_delete
        BEFORE DELETE ON agent_delegations
        BEGIN
            SELECT RAISE(ABORT, 'Agent delegations cannot be deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_relation_guard
        BEFORE INSERT ON agent_delegations
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM agent_instances AS parent
            JOIN agent_instances AS child ON child.id = NEW.child_instance_id
            JOIN campaigns AS campaign ON campaign.id = NEW.campaign_id
            JOIN goals AS goal ON goal.id = campaign.goal_id
            WHERE parent.id = NEW.parent_instance_id
              AND parent.status = 'active'
              AND child.status = 'active'
              AND parent.project_id = child.project_id
              AND goal.project_id = parent.project_id
              AND campaign.status = 'active'
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent delegation relation is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_budget_guard
        BEFORE INSERT ON agent_delegations
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM campaign_budget_reservations AS reservation
            WHERE reservation.id = NEW.budget_reservation_id
              AND reservation.campaign_id = NEW.campaign_id
              AND reservation.owner_id = NEW.parent_instance_id
              AND reservation.resource = NEW.budget_resource
              AND reservation.units = NEW.budget_units
              AND reservation.status = 'reserved'
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent delegation budget proof is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_parent_authority_guard
        BEFORE INSERT ON agent_delegations
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM agent_instances AS parent
            WHERE parent.id = NEW.parent_instance_id
              AND json_extract(parent.spec, '$.allow_subagents') = 1
              AND (
                  SELECT COUNT(*)
                  FROM agent_delegations AS existing
                  WHERE existing.parent_instance_id = NEW.parent_instance_id
                    AND existing.status = 'active'
              ) < CAST(json_extract(parent.spec, '$.max_subagents') AS INTEGER)
        )
        BEGIN
            SELECT RAISE(ABORT, 'Parent agent delegation authority exceeded');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_depth_guard
        BEFORE INSERT ON agent_delegations
        FOR EACH ROW
        WHEN (
            EXISTS (
                SELECT 1
                FROM agent_delegations AS incoming
                WHERE incoming.child_instance_id = NEW.parent_instance_id
                  AND incoming.status = 'active'
            )
            AND NOT EXISTS (
                SELECT 1
                FROM agent_delegations AS incoming
                WHERE incoming.child_instance_id = NEW.parent_instance_id
                  AND incoming.status = 'active'
                  AND NEW.depth = incoming.depth + 1
                  AND NEW.max_depth = incoming.max_depth
            )
        )
        OR (
            NOT EXISTS (
                SELECT 1
                FROM agent_delegations AS incoming
                WHERE incoming.child_instance_id = NEW.parent_instance_id
                  AND incoming.status = 'active'
            )
            AND NEW.depth <> 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent delegation depth proof is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_delegations_cycle_guard
        BEFORE INSERT ON agent_delegations
        FOR EACH ROW
        WHEN EXISTS (
            WITH RECURSIVE descendants(id) AS (
                SELECT child_instance_id
                FROM agent_delegations
                WHERE parent_instance_id = NEW.child_instance_id
                  AND status = 'active'
                UNION
                SELECT delegation.child_instance_id
                FROM agent_delegations AS delegation
                JOIN descendants ON delegation.parent_instance_id = descendants.id
                WHERE delegation.status = 'active'
            )
            SELECT 1 FROM descendants WHERE id = NEW.parent_instance_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Agent delegation cycle is not allowed');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
