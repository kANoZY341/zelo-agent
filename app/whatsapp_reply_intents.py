"""Private operator reply preparation and decision; no WhatsApp transport."""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import OperatorPrincipal
from app.channel_binding_store import ChannelBindingStore
from app.channel_contracts import (
    ChannelDeliveryIntent,
    channel_message_fingerprint,
    derive_opaque_channel_key,
)
from app.channel_operator_accounts import ChannelOperatorAccountStore
from app.mind import MindReplyAction
from app.models import (
    ChannelOperatorAccountRevocationRecord,
    ChannelOperatorAccountSelectionRecord,
    ChannelThreadBindingRecord,
    ConversationTurn,
    VerifiedWhatsAppIngressRecord,
    WhatsAppReplyDecisionRecord,
    WhatsAppReplyIntentRecord,
)
from app.storage import SessionStore
from app.whatsapp_text_events import validate_whatsapp_provider_identity
from app.whatsapp_verified_ingress import whatsapp_app_secret_identity


def _operator(principal: OperatorPrincipal) -> None:
    if type(principal) is not OperatorPrincipal or principal.subject != "ahmad":
        raise PermissionError("whatsapp_reply_operator_required")


@dataclass(frozen=True)
class PreparedWhatsAppReply:
    intent_fingerprint: str
    verified_message_fingerprint: str
    conversation_turn_id: str
    thread_fingerprint: str
    account_key: str
    recipient_key: str
    thread_key: str
    text_sha256: str
    prepared_at: datetime
    transport_authority: bool = False


@dataclass(frozen=True)
class WhatsAppOutboundProjection:
    intent_fingerprint: str
    waba_id: str = field(repr=False)
    phone_number_id: str = field(repr=False)
    recipient_wa_id: str = field(repr=False)
    inbound_message_id: str = field(repr=False)
    text: str = field(repr=False)
    text_sha256: str
    transport_authority: bool = False


@dataclass(frozen=True)
class WhatsAppReplyDecision:
    intent_fingerprint: str
    decision: str
    decided_at: datetime
    transport_authority: bool = False


class WhatsAppReplyIntentStore:
    """Append one exact reply intent and one Ahmad decision after verified admission."""

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    @staticmethod
    def _current(
        session: Session, turn_id: str
    ) -> tuple[VerifiedWhatsAppIngressRecord, ChannelThreadBindingRecord, ChannelDeliveryIntent]:
        turn = session.get(ConversationTurn, turn_id)
        if turn is None:
            raise LookupError("whatsapp_reply_turn_not_found")
        verified = session.scalar(
            select(VerifiedWhatsAppIngressRecord).where(
                VerifiedWhatsAppIngressRecord.conversation_turn_id == turn_id
            )
        )
        if verified is None:
            raise PermissionError("whatsapp_reply_verified_ingress_required")
        if (
            channel_message_fingerprint(verified.thread_fingerprint, verified.message_key)
            != verified.message_fingerprint
        ):
            raise ValueError("whatsapp_reply_verified_identity_invalid")
        binding = session.get(ChannelThreadBindingRecord, verified.thread_fingerprint)
        if binding is None:
            raise PermissionError("whatsapp_reply_binding_required")
        bound = ChannelBindingStore._verified_binding(binding)
        if bound.channel != "whatsapp" or bound.conversation_id != turn.conversation_id:
            raise PermissionError("whatsapp_reply_binding_invalid")
        selection = session.get(ChannelOperatorAccountSelectionRecord, binding.account_key)
        if selection is None:
            raise PermissionError("whatsapp_reply_account_not_selected")
        revoked = session.get(ChannelOperatorAccountRevocationRecord, binding.account_key)
        chosen = ChannelOperatorAccountStore._snapshot(selection, revoked=revoked is not None)
        if not chosen.currently_selected:
            raise PermissionError("whatsapp_reply_account_revoked")
        if not hmac.compare_digest(chosen.app_secret_identity, verified.app_secret_identity):
            raise PermissionError("whatsapp_reply_secret_mismatch")
        try:
            action = MindReplyAction.model_validate(turn.mind_action, strict=True)
        except ValidationError:
            raise PermissionError("whatsapp_reply_only") from None
        if (
            turn.idempotency_key != f"channel:{verified.message_fingerprint}"
            or hashlib.sha256(turn.user_content.encode("utf-8")).hexdigest() != verified.text_sha256
            or turn.core_task_id is not None
            or action.content != turn.assistant_content
        ):
            raise PermissionError("whatsapp_reply_only")
        reply = ChannelDeliveryIntent(
            channel="whatsapp",
            account_key=binding.account_key,
            recipient_key=binding.sender_key,
            thread_key=binding.thread_key,
            conversation_turn_id=turn.id,
            text=turn.assistant_content,
            text_sha256=hashlib.sha256(turn.assistant_content.encode("utf-8")).hexdigest(),
        )
        return verified, binding, reply

    @staticmethod
    def _prepared(
        row: WhatsAppReplyIntentRecord,
        verified: VerifiedWhatsAppIngressRecord,
        binding: ChannelThreadBindingRecord,
        reply: ChannelDeliveryIntent,
    ) -> PreparedWhatsAppReply:
        if (
            row.intent_fingerprint != reply.delivery_fingerprint()
            or row.verified_message_fingerprint != verified.message_fingerprint
            or row.conversation_turn_id != reply.conversation_turn_id
            or row.thread_fingerprint != verified.thread_fingerprint
            or row.account_key != binding.account_key
            or row.recipient_key != binding.sender_key
            or row.thread_key != binding.thread_key
            or row.text_sha256 != reply.text_sha256
            or row.operator_id != "ahmad"
            or row.transport_authority
        ):
            raise ValueError("whatsapp_reply_intent_conflict")
        return PreparedWhatsAppReply(
            intent_fingerprint=row.intent_fingerprint,
            verified_message_fingerprint=row.verified_message_fingerprint,
            conversation_turn_id=row.conversation_turn_id,
            thread_fingerprint=row.thread_fingerprint,
            account_key=row.account_key,
            recipient_key=row.recipient_key,
            thread_key=row.thread_key,
            text_sha256=row.text_sha256,
            prepared_at=row.prepared_at,
        )

    @staticmethod
    def _decision(row: WhatsAppReplyDecisionRecord) -> WhatsAppReplyDecision:
        if (
            row.decision not in ("approve", "reject")
            or row.operator_id != "ahmad"
            or row.transport_authority
        ):
            raise ValueError("whatsapp_reply_decision_record_invalid")
        return WhatsAppReplyDecision(
            intent_fingerprint=row.intent_fingerprint,
            decision=row.decision,
            decided_at=row.decided_at,
        )

    def prepare(
        self, turn_id: str, *, principal: OperatorPrincipal
    ) -> tuple[PreparedWhatsAppReply, bool]:
        _operator(principal)
        if type(turn_id) is not str or not turn_id:
            raise ValueError("whatsapp_reply_turn_invalid")
        with self.database.transaction() as session:
            verified, binding, reply = self._current(session, turn_id)
            existing = session.scalar(
                select(WhatsAppReplyIntentRecord).where(
                    WhatsAppReplyIntentRecord.conversation_turn_id == turn_id
                )
            )
            if existing is not None:
                return self._prepared(existing, verified, binding, reply), False
            row = WhatsAppReplyIntentRecord(
                intent_fingerprint=reply.delivery_fingerprint(),
                verified_message_fingerprint=verified.message_fingerprint,
                conversation_turn_id=turn_id,
                thread_fingerprint=verified.thread_fingerprint,
                account_key=binding.account_key,
                recipient_key=binding.sender_key,
                thread_key=binding.thread_key,
                text_sha256=reply.text_sha256,
                operator_id=principal.subject,
                transport_authority=False,
            )
            session.add(row)
            session.flush()
            return self._prepared(row, verified, binding, reply), True

    def decide(
        self, intent_fingerprint: str, decision: str, *, principal: OperatorPrincipal
    ) -> tuple[WhatsAppReplyDecision, bool]:
        _operator(principal)
        if type(intent_fingerprint) is not str or len(intent_fingerprint) != 64:
            raise ValueError("whatsapp_reply_intent_identity_invalid")
        if type(decision) is not str or decision not in ("approve", "reject"):
            raise ValueError("whatsapp_reply_decision_invalid")
        with self.database.transaction() as session:
            row = session.get(WhatsAppReplyIntentRecord, intent_fingerprint)
            if row is None:
                raise LookupError("whatsapp_reply_intent_not_found")
            verified, binding, reply = self._current(session, row.conversation_turn_id)
            self._prepared(row, verified, binding, reply)
            existing = session.get(WhatsAppReplyDecisionRecord, intent_fingerprint)
            if existing is not None:
                saved = self._decision(existing)
                if saved.decision != decision:
                    raise ValueError("whatsapp_reply_decision_conflict")
                return saved, False
            record = WhatsAppReplyDecisionRecord(
                intent_fingerprint=intent_fingerprint,
                decision=decision,
                operator_id=principal.subject,
                transport_authority=False,
            )
            session.add(record)
            session.flush()
            return self._decision(record), True

    def _project_outbound_in_session(
        self,
        session: Session,
        intent_fingerprint: str,
        *,
        principal: OperatorPrincipal,
        app_secret: bytes,
        identity_secret: bytes,
        waba_id: str,
        phone_number_id: str,
        recipient_wa_id: str,
        inbound_message_id: str,
    ) -> WhatsAppOutboundProjection:
        """Recheck exact outbound facts against one current database snapshot."""

        _operator(principal)
        if type(intent_fingerprint) is not str or len(intent_fingerprint) != 64:
            raise ValueError("whatsapp_reply_intent_identity_invalid")
        validate_whatsapp_provider_identity(
            waba_id, phone_number_id, recipient_wa_id, inbound_message_id
        )
        current_secret_identity = whatsapp_app_secret_identity(app_secret, identity_secret)
        row = session.get(WhatsAppReplyIntentRecord, intent_fingerprint)
        if row is None:
            raise LookupError("whatsapp_reply_intent_not_found")
        verified, binding, reply = self._current(session, row.conversation_turn_id)
        self._prepared(row, verified, binding, reply)
        saved = session.get(WhatsAppReplyDecisionRecord, intent_fingerprint)
        if saved is None or self._decision(saved).decision != "approve":
            raise PermissionError("whatsapp_outbound_approval_required")
        provider_account = f"{waba_id}:{phone_number_id}"
        provider_thread = f"{provider_account}:{recipient_wa_id}"
        identities = (
            (verified.app_secret_identity, current_secret_identity),
            (
                binding.account_key,
                derive_opaque_channel_key(
                    identity_secret,
                    channel="whatsapp",
                    purpose="account",
                    raw_identifier=provider_account,
                ),
            ),
            (
                binding.sender_key,
                derive_opaque_channel_key(
                    identity_secret,
                    channel="whatsapp",
                    purpose="sender",
                    raw_identifier=recipient_wa_id,
                ),
            ),
            (
                binding.thread_key,
                derive_opaque_channel_key(
                    identity_secret,
                    channel="whatsapp",
                    purpose="thread",
                    raw_identifier=provider_thread,
                ),
            ),
            (
                verified.message_key,
                derive_opaque_channel_key(
                    identity_secret,
                    channel="whatsapp",
                    purpose="message",
                    raw_identifier=inbound_message_id,
                ),
            ),
        )
        if any(
            not hmac.compare_digest(saved_key, current_key) for saved_key, current_key in identities
        ):
            raise PermissionError("whatsapp_outbound_identity_mismatch")
        return WhatsAppOutboundProjection(
            intent_fingerprint=intent_fingerprint,
            waba_id=waba_id,
            phone_number_id=phone_number_id,
            recipient_wa_id=recipient_wa_id,
            inbound_message_id=inbound_message_id,
            text=reply.text,
            text_sha256=reply.text_sha256,
        )

    def project_outbound(
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
    ) -> WhatsAppOutboundProjection:
        """Project one approved exact reply; this never grants send authority."""

        with self.database.read() as session:
            return self._project_outbound_in_session(
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
