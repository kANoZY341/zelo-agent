"""Create inert operator-approved channel/thread bindings and replay observations."""

import sqlalchemy as sa
from alembic import op

revision = "0036_channel_thread_observations"
down_revision = "0035_skill_learning_promotion_links"
branch_labels = None
depends_on = None


def _opaque(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({name}) = 64 AND {name} NOT GLOB '*[^0-9a-f]*'",
        name=f"ck_channel_hex_{name}",
    )


def upgrade() -> None:
    op.create_table(
        "channel_thread_bindings",
        sa.Column("thread_fingerprint", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("sender_key", sa.String(64), nullable=False),
        sa.Column("thread_key", sa.String(64), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column(
            "provider_authenticated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("transport_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "channel",
            "account_key",
            "sender_key",
            "thread_key",
            name="uq_channel_bound_thread_identity",
        ),
        sa.CheckConstraint(
            "channel IN ('browser_text','whatsapp','email')",
            name="ck_channel_thread_kind",
        ),
        sa.CheckConstraint("operator_id = 'ahmad'", name="ck_channel_thread_operator"),
        sa.CheckConstraint("provider_authenticated = 0", name="ck_channel_thread_unverified"),
        sa.CheckConstraint("transport_authority = 0", name="ck_channel_thread_transport"),
        _opaque("thread_fingerprint"),
        _opaque("account_key"),
        _opaque("sender_key"),
        _opaque("thread_key"),
    )
    op.create_index(
        "ix_channel_thread_bindings_conversation_id",
        "channel_thread_bindings",
        ["conversation_id"],
    )

    op.create_table(
        "channel_ingress_observations",
        sa.Column("message_fingerprint", sa.String(64), primary_key=True),
        sa.Column(
            "thread_fingerprint",
            sa.String(64),
            sa.ForeignKey("channel_thread_bindings.thread_fingerprint"),
            nullable=False,
        ),
        sa.Column("message_key", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("provider_received_at", sa.DateTime(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("mind_executed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delivery_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "thread_fingerprint",
            "message_key",
            name="uq_channel_ingress_message_identity",
        ),
        sa.CheckConstraint("operator_id = 'ahmad'", name="ck_channel_observation_operator"),
        sa.CheckConstraint("state = 'unverified'", name="ck_channel_observation_state"),
        sa.CheckConstraint("mind_executed = 0", name="ck_channel_observation_mind"),
        sa.CheckConstraint("delivery_authority = 0", name="ck_channel_observation_delivery"),
        _opaque("message_fingerprint"),
        _opaque("message_key"),
        _opaque("text_sha256"),
    )
    op.create_index(
        "ix_channel_ingress_observations_thread_fingerprint",
        "channel_ingress_observations",
        ["thread_fingerprint"],
    )
    for table in ("channel_thread_bindings", "channel_ingress_observations"):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, 'Channel ledger is append-only'); END"
            )


def downgrade() -> None:
    for table in ("channel_ingress_observations", "channel_thread_bindings"):
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation.lower()}")
    op.drop_index(
        "ix_channel_ingress_observations_thread_fingerprint",
        table_name="channel_ingress_observations",
    )
    op.drop_table("channel_ingress_observations")
    op.drop_index(
        "ix_channel_thread_bindings_conversation_id",
        table_name="channel_thread_bindings",
    )
    op.drop_table("channel_thread_bindings")
