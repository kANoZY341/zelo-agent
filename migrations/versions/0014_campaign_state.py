"""Add v0.12 Campaign ownership, dependencies, events, and checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0014_campaign_state"
down_revision = "0013_project_goal_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("goal_id", sa.String(length=36), sa.ForeignKey("goals.id"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','paused','completed','cancelled')",
            name="ck_campaign_status",
        ),
    )
    op.create_index("ix_campaigns_goal_id", "campaigns", ["goal_id"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    op.create_table(
        "campaign_task_links",
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_campaign_task_links_campaign_id", "campaign_task_links", ["campaign_id"])

    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column(
            "depends_on_task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            primary_key=True,
        ),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependency_not_self"),
    )
    op.create_index("ix_task_dependencies_task_id", "task_dependencies", ["task_id"])
    op.create_index(
        "ix_task_dependencies_depends_on_task_id",
        "task_dependencies",
        ["depends_on_task_id"],
    )

    op.create_table(
        "campaign_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint("campaign_id", "sequence", name="uq_campaign_event_sequence"),
    )
    op.create_index("ix_campaign_events_campaign_id", "campaign_events", ["campaign_id"])
    op.create_index("ix_campaign_events_task_id", "campaign_events", ["task_id"])

    op.create_table(
        "campaign_checkpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(length=36),
            sa.ForeignKey("campaigns.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "version", name="uq_campaign_checkpoint_version"),
    )
    op.create_index(
        "ix_campaign_checkpoints_campaign_id",
        "campaign_checkpoints",
        ["campaign_id"],
    )

    op.execute(
        """
        CREATE TRIGGER task_dependencies_same_campaign
        BEFORE INSERT ON task_dependencies
        FOR EACH ROW
        WHEN
            (SELECT campaign_id FROM campaign_task_links WHERE task_id = NEW.task_id) IS NULL
            OR (SELECT campaign_id FROM campaign_task_links
                WHERE task_id = NEW.depends_on_task_id) IS NULL
            OR (SELECT campaign_id FROM campaign_task_links WHERE task_id = NEW.task_id)
                <> (SELECT campaign_id FROM campaign_task_links
                    WHERE task_id = NEW.depends_on_task_id)
        BEGIN
            SELECT RAISE(ABORT, 'Task dependencies must belong to the same campaign');
        END
        """
    )

    immutable_tables = (
        ("campaign_task_links", "Campaign task ownership is immutable"),
        ("task_dependencies", "Task dependencies are immutable"),
        ("campaign_events", "Campaign events are append-only"),
        ("campaign_checkpoints", "Campaign checkpoints are immutable"),
    )
    for table, message in immutable_tables:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
