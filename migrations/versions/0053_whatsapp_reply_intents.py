"""Append-only prepared WhatsApp replies and operator decisions, without delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0053_whatsapp_reply_intents"
down_revision = "0052_whatsapp_verified_ingress"
branch_labels = None
depends_on = None


def _hex(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'",
        name=f"ck_whatsapp_reply_{name}",
    )


def _append_only(table: str, *, extra_identity: str = "") -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER {table}_no_{operation.lower()} "
            f"BEFORE {operation} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
    op.execute(
        f"CREATE TRIGGER {table}_no_duplicate_insert "
        f"BEFORE INSERT ON {table} "
        f"WHEN EXISTS (SELECT 1 FROM {table} "
        f"WHERE intent_fingerprint=NEW.intent_fingerprint{extra_identity}) "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
    )


def upgrade() -> None:
    op.create_table(
        "whatsapp_reply_intents",
        sa.Column("intent_fingerprint", sa.String(64), primary_key=True),
        sa.Column(
            "verified_message_fingerprint",
            sa.String(64),
            sa.ForeignKey("whatsapp_verified_ingress.message_fingerprint"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "conversation_turn_id",
            sa.String(36),
            sa.ForeignKey("conversation_turns.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("thread_fingerprint", sa.String(64), nullable=False),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("recipient_key", sa.String(64), nullable=False),
        sa.Column("thread_key", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("operator_id", sa.String(64), nullable=False),
        sa.Column("prepared_at", sa.DateTime(), nullable=False),
        sa.Column("transport_authority", sa.Boolean(), nullable=False),
        _hex("intent_fingerprint"),
        _hex("verified_message_fingerprint"),
        _hex("thread_fingerprint"),
        _hex("account_key"),
        _hex("recipient_key"),
        _hex("thread_key"),
        _hex("text_sha256"),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_whatsapp_reply_intent_operator"),
        sa.CheckConstraint("transport_authority=0", name="ck_whatsapp_reply_intent_no_transport"),
        sqlite_with_rowid=False,
    )
    _append_only(
        "whatsapp_reply_intents",
        extra_identity=(
            " OR verified_message_fingerprint=NEW.verified_message_fingerprint"
            " OR conversation_turn_id=NEW.conversation_turn_id"
        ),
    )
    op.execute(
        "CREATE TRIGGER whatsapp_reply_intents_verified_link "
        "BEFORE INSERT ON whatsapp_reply_intents "
        "WHEN NOT EXISTS (SELECT 1 FROM whatsapp_verified_ingress v "
        "JOIN channel_thread_bindings b ON b.thread_fingerprint=v.thread_fingerprint "
        "WHERE v.message_fingerprint=NEW.verified_message_fingerprint "
        "AND v.conversation_turn_id=NEW.conversation_turn_id "
        "AND v.thread_fingerprint=NEW.thread_fingerprint "
        "AND b.channel='whatsapp' "
        "AND b.account_key=NEW.account_key "
        "AND b.sender_key=NEW.recipient_key "
        "AND b.thread_key=NEW.thread_key) "
        "BEGIN SELECT RAISE(ABORT, 'WhatsApp reply verified link invalid'); END"
    )
    op.create_table(
        "whatsapp_reply_decisions",
        sa.Column(
            "intent_fingerprint",
            sa.String(64),
            sa.ForeignKey("whatsapp_reply_intents.intent_fingerprint"),
            primary_key=True,
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("operator_id", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("transport_authority", sa.Boolean(), nullable=False),
        _hex("intent_fingerprint"),
        sa.CheckConstraint("decision IN ('approve','reject')", name="ck_whatsapp_reply_decision"),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_whatsapp_reply_decision_operator"),
        sa.CheckConstraint("transport_authority=0", name="ck_whatsapp_reply_decision_no_transport"),
        sqlite_with_rowid=False,
    )
    _append_only("whatsapp_reply_decisions")


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
