"""One append-only possible WhatsApp send claim, without transport."""

import sqlalchemy as sa
from alembic import op

revision = "0054_whatsapp_send_attempt"
down_revision = "0053_whatsapp_reply_intents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_send_attempts",
        sa.Column(
            "intent_fingerprint",
            sa.String(64),
            sa.ForeignKey("whatsapp_reply_decisions.intent_fingerprint"),
            primary_key=True,
        ),
        sa.Column("operator_id", sa.String(64), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("send_may_have_occurred", sa.Boolean(), nullable=False),
        sa.Column("automatic_retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("transport_authority", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "length(intent_fingerprint)=64 AND intent_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_whatsapp_send_attempt_intent_fingerprint",
        ),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_whatsapp_send_attempt_operator"),
        sa.CheckConstraint(
            "phase='send_outcome_unresolved'", name="ck_whatsapp_send_attempt_phase"
        ),
        sa.CheckConstraint(
            "send_may_have_occurred=1", name="ck_whatsapp_send_attempt_possible_send"
        ),
        sa.CheckConstraint("automatic_retry_allowed=0", name="ck_whatsapp_send_attempt_no_retry"),
        sa.CheckConstraint("transport_authority=0", name="ck_whatsapp_send_attempt_no_transport"),
        sqlite_with_rowid=False,
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER whatsapp_send_attempts_no_{operation.lower()} "
            f"BEFORE {operation} ON whatsapp_send_attempts "
            "BEGIN SELECT RAISE(ABORT, 'whatsapp_send_attempts is append-only'); END"
        )
    op.execute(
        "CREATE TRIGGER whatsapp_send_attempts_no_duplicate_insert "
        "BEFORE INSERT ON whatsapp_send_attempts "
        "WHEN EXISTS (SELECT 1 FROM whatsapp_send_attempts "
        "WHERE intent_fingerprint=NEW.intent_fingerprint) "
        "BEGIN SELECT RAISE(ABORT, 'whatsapp_send_attempts is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER whatsapp_send_attempts_approved_current_link "
        "BEFORE INSERT ON whatsapp_send_attempts "
        "WHEN NOT EXISTS (SELECT 1 FROM whatsapp_reply_decisions d "
        "JOIN whatsapp_reply_intents i ON i.intent_fingerprint=d.intent_fingerprint "
        "JOIN channel_operator_account_selections s ON s.account_key=i.account_key "
        "LEFT JOIN channel_operator_account_revocations r ON r.account_key=i.account_key "
        "WHERE d.intent_fingerprint=NEW.intent_fingerprint AND d.decision='approve' "
        "AND d.operator_id='ahmad' AND i.operator_id='ahmad' AND r.account_key IS NULL) "
        "BEGIN SELECT RAISE(ABORT, 'WhatsApp send attempt approval invalid'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
