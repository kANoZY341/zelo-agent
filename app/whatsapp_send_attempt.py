"""Private one-use possible WhatsApp send claim; no provider transport."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.auth import OperatorPrincipal
from app.models import WhatsAppSendAttemptRecord
from app.storage import SessionStore
from app.whatsapp_reply_intents import (
    WhatsAppOutboundProjection,
    WhatsAppReplyIntentStore,
    _operator,
)


@dataclass(frozen=True)
class ClaimedWhatsAppSendAttempt:
    intent_fingerprint: str
    claimed_at: datetime
    projection: WhatsAppOutboundProjection
    phase: Literal["send_outcome_unresolved"] = "send_outcome_unresolved"
    send_may_have_occurred: bool = True
    automatic_retry_allowed: bool = False
    transport_authority: bool = False


class WhatsAppSendAttemptStore:
    """Commit one unresolved attempt before any possible future network send."""

    def __init__(self, database: SessionStore) -> None:
        self.database = database
        self.replies = WhatsAppReplyIntentStore(database)

    def claim(
        self,
        intent_fingerprint: str,
        *,
        principal: OperatorPrincipal,
        app_secret: bytes,
        identity_secret: bytes,
        waba_id: str,
        phone_number_id: str,
        recipient_wa_id: str,
        inbound_message_id: str,
    ) -> ClaimedWhatsAppSendAttempt:
        _operator(principal)
        with self.database.transaction() as session:
            projection = self.replies._project_outbound_in_session(
                session,
                intent_fingerprint,
                principal=principal,
                app_secret=app_secret,
                identity_secret=identity_secret,
                waba_id=waba_id,
                phone_number_id=phone_number_id,
                recipient_wa_id=recipient_wa_id,
                inbound_message_id=inbound_message_id,
            )
            if session.get(WhatsAppSendAttemptRecord, intent_fingerprint) is not None:
                raise PermissionError("whatsapp_send_attempt_already_claimed")
            record = WhatsAppSendAttemptRecord(
                intent_fingerprint=intent_fingerprint,
                operator_id=principal.subject,
                phase="send_outcome_unresolved",
                send_may_have_occurred=True,
                automatic_retry_allowed=False,
                transport_authority=False,
            )
            session.add(record)
            session.flush()
            return ClaimedWhatsAppSendAttempt(
                intent_fingerprint=record.intent_fingerprint,
                claimed_at=record.claimed_at,
                projection=projection,
            )
