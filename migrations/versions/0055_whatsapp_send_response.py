"""Append-only shape observation for one claimed WhatsApp send."""

import sqlalchemy as sa
from alembic import op

revision = "0055_whatsapp_send_response"
down_revision = "0054_whatsapp_send_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_send_responses",
        sa.Column(
            "intent_fingerprint",
            sa.String(64),
            sa.ForeignKey("whatsapp_send_attempts.intent_fingerprint"),
            primary_key=True,
        ),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("response_message_key", sa.String(64), nullable=True, unique=True),
        sa.Column("delivery_confirmed", sa.Boolean(), nullable=False),
        sa.Column("transport_authority", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "length(intent_fingerprint)=64 AND intent_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_whatsapp_send_response_intent",
        ),
        sa.CheckConstraint(
            "response_message_key IS NULL OR "
            "(length(response_message_key)=64 AND response_message_key NOT GLOB '*[^0-9a-f]*')",
            name="ck_whatsapp_send_response_message_key",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_whatsapp_send_response_http_status",
        ),
        sa.CheckConstraint(
            "outcome IN ('success_shape','invalid_success_shape','http_non_success','no_response')",
            name="ck_whatsapp_send_response_outcome",
        ),
        sa.CheckConstraint(
            "(outcome='success_shape' AND http_status IS NOT NULL AND http_status=200 "
            "AND response_message_key IS NOT NULL) "
            "OR (outcome='invalid_success_shape' AND http_status IS NOT NULL AND http_status=200 "
            "AND response_message_key IS NULL) "
            "OR (outcome='http_non_success' AND http_status IS NOT NULL "
            "AND http_status!=200 AND response_message_key IS NULL) "
            "OR (outcome='no_response' AND http_status IS NULL "
            "AND response_message_key IS NULL)",
            name="ck_whatsapp_send_response_shape",
        ),
        sa.CheckConstraint("delivery_confirmed=0", name="ck_whatsapp_send_response_no_delivery"),
        sa.CheckConstraint("transport_authority=0", name="ck_whatsapp_send_response_no_transport"),
        sqlite_with_rowid=False,
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER whatsapp_send_responses_no_{operation.lower()} "
            f"BEFORE {operation} ON whatsapp_send_responses "
            "BEGIN SELECT RAISE(ABORT, 'whatsapp_send_responses is append-only'); END"
        )
    op.execute(
        "CREATE TRIGGER whatsapp_send_responses_no_duplicate_insert "
        "BEFORE INSERT ON whatsapp_send_responses "
        "WHEN EXISTS (SELECT 1 FROM whatsapp_send_responses "
        "WHERE intent_fingerprint=NEW.intent_fingerprint "
        "OR (NEW.response_message_key IS NOT NULL "
        "AND response_message_key=NEW.response_message_key)) "
        "BEGIN SELECT RAISE(ABORT, 'whatsapp_send_responses is append-only'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
