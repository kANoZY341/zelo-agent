"""Add durable company units and reporting assignments for v0.14."""

import sqlalchemy as sa
from alembic import op

revision = "0023_organizational_hierarchy"
down_revision = "0022_agent_execution_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_units",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("unit_type", sa.String(length=16), nullable=False),
        sa.Column(
            "parent_unit_id",
            sa.String(length=36),
            sa.ForeignKey("organization_units.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "unit_type IN ('division','department','team')",
            name="ck_organization_unit_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')",
            name="ck_organization_unit_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND retired_at IS NULL) OR "
            "(status = 'retired' AND retired_at IS NOT NULL)",
            name="ck_organization_unit_retirement_shape",
        ),
    )
    op.create_index("ix_organization_units_unit_type", "organization_units", ["unit_type"])
    op.create_index(
        "ix_organization_units_parent_unit_id",
        "organization_units",
        ["parent_unit_id"],
    )
    op.create_index("ix_organization_units_status", "organization_units", ["status"])

    op.create_table(
        "organization_assignments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "agent_instance_id",
            sa.String(length=36),
            sa.ForeignKey("agent_instances.id"),
            nullable=False,
        ),
        sa.Column(
            "unit_id",
            sa.String(length=36),
            sa.ForeignKey("organization_units.id"),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column(
            "reports_to_assignment_id",
            sa.String(length=36),
            sa.ForeignKey("organization_assignments.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("end_reason", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "level IN ('vp','director','manager','specialist')",
            name="ck_organization_assignment_level",
        ),
        sa.CheckConstraint(
            "status IN ('active','ended')",
            name="ck_organization_assignment_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND ended_at IS NULL AND end_reason IS NULL) OR "
            "(status = 'ended' AND ended_at IS NOT NULL AND end_reason IS NOT NULL)",
            name="ck_organization_assignment_end_shape",
        ),
    )
    op.create_index(
        "ix_organization_assignments_agent_instance_id",
        "organization_assignments",
        ["agent_instance_id"],
    )
    op.create_index(
        "ix_organization_assignments_unit_id",
        "organization_assignments",
        ["unit_id"],
    )
    op.create_index(
        "ix_organization_assignments_level",
        "organization_assignments",
        ["level"],
    )
    op.create_index(
        "ix_organization_assignments_reports_to",
        "organization_assignments",
        ["reports_to_assignment_id"],
    )
    op.create_index(
        "ix_organization_assignments_status",
        "organization_assignments",
        ["status"],
    )
    op.create_index(
        "uq_organization_assignments_active_agent",
        "organization_assignments",
        ["agent_instance_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_organization_assignments_active_leader",
        "organization_assignments",
        ["unit_id", "level"],
        unique=True,
        sqlite_where=sa.text(
            "status = 'active' AND level IN ('vp','director','manager')"
        ),
    )

    op.execute(
        """
        CREATE TRIGGER organization_units_insert_active_guard
        BEFORE INSERT ON organization_units
        FOR EACH ROW
        WHEN NEW.status <> 'active' OR NEW.retired_at IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'Organization units must be created active');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_units_parent_guard
        BEFORE INSERT ON organization_units
        FOR EACH ROW
        WHEN (
            NEW.unit_type = 'division'
            AND NEW.parent_unit_id IS NOT NULL
        )
        OR (
            NEW.unit_type = 'department'
            AND NOT EXISTS (
                SELECT 1
                FROM organization_units AS parent
                WHERE parent.id = NEW.parent_unit_id
                  AND parent.unit_type = 'division'
                  AND parent.status = 'active'
            )
        )
        OR (
            NEW.unit_type = 'team'
            AND NOT EXISTS (
                SELECT 1
                FROM organization_units AS parent
                WHERE parent.id = NEW.parent_unit_id
                  AND parent.unit_type = 'department'
                  AND parent.status = 'active'
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'Organization unit parent is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_units_duplicate_guard
        BEFORE INSERT ON organization_units
        FOR EACH ROW
        WHEN EXISTS (
            SELECT 1
            FROM organization_units AS existing
            WHERE existing.status = 'active'
              AND lower(existing.name) = lower(NEW.name)
              AND existing.parent_unit_id IS NEW.parent_unit_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Active organization unit already exists');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_units_identity_immutable
        BEFORE UPDATE ON organization_units
        FOR EACH ROW
        WHEN NEW.name <> OLD.name
          OR NEW.unit_type <> OLD.unit_type
          OR NEW.parent_unit_id IS NOT OLD.parent_unit_id
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'Organization unit identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_units_transition_guard
        BEFORE UPDATE OF status ON organization_units
        FOR EACH ROW
        WHEN NOT (
            (OLD.status = 'active' AND NEW.status = 'retired'
             AND NEW.retired_at IS NOT NULL)
            OR (OLD.status = 'retired' AND NEW.status = 'retired'
                AND NEW.retired_at = OLD.retired_at)
        )
        BEGIN
            SELECT RAISE(ABORT, 'Invalid organization unit transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_units_retire_guard
        BEFORE UPDATE OF status ON organization_units
        FOR EACH ROW
        WHEN NEW.status = 'retired'
         AND OLD.status = 'active'
         AND (
            EXISTS (
                SELECT 1
                FROM organization_units AS child
                WHERE child.parent_unit_id = OLD.id
                  AND child.status = 'active'
            )
            OR EXISTS (
                SELECT 1
                FROM organization_assignments AS assignment
                WHERE assignment.unit_id = OLD.id
                  AND assignment.status = 'active'
            )
         )
        BEGIN
            SELECT RAISE(ABORT, 'Organization unit still has active dependents');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_units_no_delete
        BEFORE DELETE ON organization_units
        BEGIN
            SELECT RAISE(ABORT, 'Organization units cannot be deleted');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER organization_assignments_insert_active_guard
        BEFORE INSERT ON organization_assignments
        FOR EACH ROW
        WHEN NEW.status <> 'active'
          OR NEW.ended_at IS NOT NULL
          OR NEW.end_reason IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'Organization assignments must be created active');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_assignments_relation_guard
        BEFORE INSERT ON organization_assignments
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM agent_instances AS holder
            JOIN organization_units AS unit ON unit.id = NEW.unit_id
            WHERE holder.id = NEW.agent_instance_id
              AND holder.status = 'active'
              AND unit.status = 'active'
              AND (
                (
                    NEW.level = 'vp'
                    AND unit.unit_type = 'division'
                    AND NEW.reports_to_assignment_id IS NULL
                )
                OR (
                    NEW.level = 'director'
                    AND unit.unit_type = 'department'
                    AND EXISTS (
                        SELECT 1
                        FROM organization_assignments AS parent_assignment
                        JOIN agent_instances AS parent_holder
                          ON parent_holder.id = parent_assignment.agent_instance_id
                        WHERE parent_assignment.id = NEW.reports_to_assignment_id
                          AND parent_assignment.status = 'active'
                          AND parent_assignment.level = 'vp'
                          AND parent_assignment.unit_id = unit.parent_unit_id
                          AND parent_holder.status = 'active'
                    )
                )
                OR (
                    NEW.level = 'manager'
                    AND unit.unit_type = 'team'
                    AND EXISTS (
                        SELECT 1
                        FROM organization_assignments AS parent_assignment
                        JOIN agent_instances AS parent_holder
                          ON parent_holder.id = parent_assignment.agent_instance_id
                        WHERE parent_assignment.id = NEW.reports_to_assignment_id
                          AND parent_assignment.status = 'active'
                          AND parent_assignment.level = 'director'
                          AND parent_assignment.unit_id = unit.parent_unit_id
                          AND parent_holder.status = 'active'
                    )
                )
                OR (
                    NEW.level = 'specialist'
                    AND unit.unit_type = 'team'
                    AND EXISTS (
                        SELECT 1
                        FROM organization_assignments AS parent_assignment
                        JOIN agent_instances AS parent_holder
                          ON parent_holder.id = parent_assignment.agent_instance_id
                        WHERE parent_assignment.id = NEW.reports_to_assignment_id
                          AND parent_assignment.status = 'active'
                          AND parent_assignment.level = 'manager'
                          AND parent_assignment.unit_id = unit.id
                          AND parent_holder.status = 'active'
                    )
                )
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'Organization assignment relation is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_assignments_identity_immutable
        BEFORE UPDATE ON organization_assignments
        FOR EACH ROW
        WHEN NEW.agent_instance_id <> OLD.agent_instance_id
          OR NEW.unit_id <> OLD.unit_id
          OR NEW.level <> OLD.level
          OR NEW.reports_to_assignment_id IS NOT OLD.reports_to_assignment_id
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'Organization assignment identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_assignments_transition_guard
        BEFORE UPDATE OF status ON organization_assignments
        FOR EACH ROW
        WHEN NOT (
            (OLD.status = 'active' AND NEW.status = 'ended'
             AND NEW.ended_at IS NOT NULL
             AND NEW.end_reason IS NOT NULL)
            OR (OLD.status = 'ended' AND NEW.status = 'ended'
                AND NEW.ended_at = OLD.ended_at
                AND NEW.end_reason = OLD.end_reason)
        )
        BEGIN
            SELECT RAISE(ABORT, 'Invalid organization assignment transition');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_assignments_end_guard
        BEFORE UPDATE OF status ON organization_assignments
        FOR EACH ROW
        WHEN NEW.status = 'ended'
         AND OLD.status = 'active'
         AND EXISTS (
            SELECT 1
            FROM organization_assignments AS child
            WHERE child.reports_to_assignment_id = OLD.id
              AND child.status = 'active'
         )
        BEGIN
            SELECT RAISE(ABORT, 'Organization assignment still has active reports');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_assignments_no_delete
        BEFORE DELETE ON organization_assignments
        BEGIN
            SELECT RAISE(ABORT, 'Organization assignments cannot be deleted');
        END
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
