"""Rebuild channel ledgers without rowids and close SQLite REPLACE bypasses."""

import sqlalchemy as sa
from alembic import op

revision = "0050_channel_ledger_replace_guards"
down_revision = "0049_skill_publication_promotion_receipt_link"
branch_labels = None
depends_on = None

_BINDINGS = "channel_thread_bindings"
_OBSERVATIONS = "channel_ingress_observations"
_NEW_BINDINGS = "_v015_channel_thread_bindings"
_NEW_OBSERVATIONS = "_v015_channel_ingress_observations"


def _opaque(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({name}) = 64 AND {name} NOT GLOB '*[^0-9a-f]*'",
        name=f"ck_channel_hex_{name}",
    )


def upgrade() -> None:
    # The 0036 UPDATE/DELETE guards do not fire for REPLACE's implicit delete
    # with SQLite's default recursive_triggers=OFF. Rebuild both linked tables
    # transactionally so their hidden rowids cannot become alternate identities.
    # migrations/env.py disables FK checks during the explicit transaction and
    # verifies all links before commit. Copy every logical column unchanged.
    op.create_table(
        _NEW_BINDINGS,
        sa.Column("thread_fingerprint", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("sender_key", sa.String(64), nullable=False),
        sa.Column("thread_key", sa.String(64), nullable=False),
        sa.Column(
            "conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False
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
            "channel IN ('browser_text','whatsapp','email')", name="ck_channel_thread_kind"
        ),
        sa.CheckConstraint("operator_id = 'ahmad'", name="ck_channel_thread_operator"),
        sa.CheckConstraint("provider_authenticated = 0", name="ck_channel_thread_unverified"),
        sa.CheckConstraint("transport_authority = 0", name="ck_channel_thread_transport"),
        _opaque("thread_fingerprint"),
        _opaque("account_key"),
        _opaque("sender_key"),
        _opaque("thread_key"),
        sqlite_with_rowid=False,
    )
    op.create_table(
        _NEW_OBSERVATIONS,
        sa.Column("message_fingerprint", sa.String(64), primary_key=True),
        sa.Column(
            "thread_fingerprint",
            sa.String(64),
            sa.ForeignKey(f"{_BINDINGS}.thread_fingerprint"),
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
            "thread_fingerprint", "message_key", name="uq_channel_ingress_message_identity"
        ),
        sa.CheckConstraint("operator_id = 'ahmad'", name="ck_channel_observation_operator"),
        sa.CheckConstraint("state = 'unverified'", name="ck_channel_observation_state"),
        sa.CheckConstraint("mind_executed = 0", name="ck_channel_observation_mind"),
        sa.CheckConstraint("delivery_authority = 0", name="ck_channel_observation_delivery"),
        _opaque("message_fingerprint"),
        _opaque("message_key"),
        _opaque("text_sha256"),
        sqlite_with_rowid=False,
    )

    binding_columns = (
        "thread_fingerprint,channel,account_key,sender_key,thread_key,"
        "conversation_id,operator_id,provider_authenticated,transport_authority,created_at"
    )
    observation_columns = (
        "message_fingerprint,thread_fingerprint,message_key,text_sha256,"
        "provider_received_at,observed_at,operator_id,state,mind_executed,delivery_authority"
    )
    op.execute(
        f"INSERT INTO {_NEW_BINDINGS} ({binding_columns}) SELECT {binding_columns} FROM {_BINDINGS}"
    )
    op.execute(
        f"INSERT INTO {_NEW_OBSERVATIONS} ({observation_columns}) "
        f"SELECT {observation_columns} FROM {_OBSERVATIONS}"
    )
    op.drop_table(_OBSERVATIONS)
    op.drop_table(_BINDINGS)
    op.rename_table(_NEW_BINDINGS, _BINDINGS)
    op.rename_table(_NEW_OBSERVATIONS, _OBSERVATIONS)
    op.create_index("ix_channel_thread_bindings_conversation_id", _BINDINGS, ["conversation_id"])
    op.create_index(
        "ix_channel_ingress_observations_thread_fingerprint",
        _OBSERVATIONS,
        ["thread_fingerprint"],
    )
    for table in (_BINDINGS, _OBSERVATIONS):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Channel ledger is append-only'); END"
            )
    op.execute(
        "CREATE TRIGGER channel_thread_bindings_no_duplicate_insert "
        "BEFORE INSERT ON channel_thread_bindings "
        "WHEN EXISTS (SELECT 1 FROM channel_thread_bindings WHERE "
        "thread_fingerprint=NEW.thread_fingerprint OR "
        "(channel=NEW.channel AND account_key=NEW.account_key AND "
        "sender_key=NEW.sender_key AND thread_key=NEW.thread_key)) "
        "BEGIN SELECT RAISE(ABORT, 'Channel ledger is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER channel_ingress_observations_no_duplicate_insert "
        "BEFORE INSERT ON channel_ingress_observations "
        "WHEN EXISTS (SELECT 1 FROM channel_ingress_observations WHERE "
        "message_fingerprint=NEW.message_fingerprint OR "
        "(thread_fingerprint=NEW.thread_fingerprint AND message_key=NEW.message_key)) "
        "BEGIN SELECT RAISE(ABORT, 'Channel ledger is append-only'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
