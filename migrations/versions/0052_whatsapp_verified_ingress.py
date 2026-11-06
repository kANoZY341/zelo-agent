"""Append-only verified WhatsApp ingress linked to the admitted Mind turn."""

import sqlalchemy as sa
from alembic import op

revision = "0052_whatsapp_verified_ingress"
down_revision = "0051_skill_publication_rowid_guards"
branch_labels = None
depends_on = None


def _hex(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'",
        name=f"ck_whatsapp_verified_{name}",
    )


def upgrade() -> None:
    op.create_table(
        "whatsapp_verified_ingress",
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
        sa.Column("admitted_at", sa.DateTime(), nullable=False),
        sa.Column("app_secret_identity", sa.String(64), nullable=False),
        sa.Column(
            "conversation_turn_id",
            sa.String(36),
            sa.ForeignKey("conversation_turns.id"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "thread_fingerprint", "message_key", name="uq_whatsapp_verified_message_identity"
        ),
        sa.UniqueConstraint("conversation_turn_id", name="uq_whatsapp_verified_turn"),
        _hex("message_fingerprint"),
        _hex("message_key"),
        _hex("text_sha256"),
        _hex("app_secret_identity"),
        sqlite_with_rowid=False,
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER whatsapp_verified_ingress_no_{operation.lower()} "
            f"BEFORE {operation} ON whatsapp_verified_ingress "
            "BEGIN SELECT RAISE(ABORT, 'Verified WhatsApp ingress is append-only'); END"
        )
    op.execute(
        "CREATE TRIGGER whatsapp_verified_ingress_no_duplicate_insert "
        "BEFORE INSERT ON whatsapp_verified_ingress "
        "WHEN EXISTS (SELECT 1 FROM whatsapp_verified_ingress "
        "WHERE message_fingerprint=NEW.message_fingerprint OR "
        "(thread_fingerprint=NEW.thread_fingerprint AND message_key=NEW.message_key) OR "
        "conversation_turn_id=NEW.conversation_turn_id) "
        "BEGIN SELECT RAISE(ABORT, 'Verified WhatsApp ingress is append-only'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
