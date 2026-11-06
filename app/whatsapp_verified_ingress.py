"""Offline, reply-only WhatsApp text admission inside Zelo Core.

No webhook route, external account discovery, outbound send, or Core task
execution is exposed. A trusted caller must supply the server-owned secrets.
"""

import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.channel_binding_store import ChannelBindingStore
from app.channel_contracts import ChannelTextIngress
from app.channel_operator_accounts import ChannelOperatorAccountStore
from app.mind import MindService
from app.models import (
    ChannelIngressObservationRecord,
    ChannelOperatorAccountRevocationRecord,
    ChannelOperatorAccountSelectionRecord,
    ChannelThreadBindingRecord,
    ConversationTurn,
    VerifiedWhatsAppIngressRecord,
)
from app.schemas import ConversationTurnResponse
from app.storage import SessionStore
from app.whatsapp_opaque_ingress import prepare_unverified_whatsapp_ingress

_MAX_AGE = timedelta(hours=24)
_MAX_FUTURE = timedelta(minutes=5)
_IDENTITY_DOMAIN = b"zelo-whatsapp-app-secret-identity-v1\0"


def whatsapp_app_secret_identity(app_secret: bytes, identity_secret: bytes) -> str:
    """Opaque identity of one Core-held Meta App Secret selection."""

    if type(app_secret) is not bytes or not 16 <= len(app_secret) <= 512 or not any(app_secret):
        raise ValueError("whatsapp_signature_secret_invalid")
    if (
        type(identity_secret) is not bytes
        or not 32 <= len(identity_secret) <= 512
        or not any(identity_secret)
        or hmac.compare_digest(app_secret, identity_secret)
    ):
        raise ValueError("whatsapp_identity_secret_invalid")
    return hmac.digest(identity_secret, _IDENTITY_DOMAIN + app_secret, "sha256").hex()


class VerifiedWhatsAppTextAdmission:
    """Check signed, selected, bound text at Mind's write transaction.

    This adapter is deliberately not assembled into the ASGI app. The account
    and thread are historical operator choices; a matching signed event and
    current trusted secret are required anew for every admission and retry.
    """

    def __init__(
        self,
        database: SessionStore,
        mind: MindService,
        *,
        app_secret: bytes,
        identity_secret: bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.database = database
        self.mind = mind
        self.app_secret = app_secret
        self.identity_secret = identity_secret
        self.app_secret_identity = whatsapp_app_secret_identity(app_secret, identity_secret)
        self.clock = clock

    def _fresh(self, ingress: ChannelTextIngress) -> None:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("whatsapp_clock_invalid")
        age = now - ingress.provider_received_at
        if age < -_MAX_FUTURE or age > _MAX_AGE:
            raise ValueError("whatsapp_ingress_freshness_invalid")

    def _checked(
        self,
        session: Session,
        ingress: ChannelTextIngress,
        *,
        turn: ConversationTurn | None = None,
        created: bool | None = None,
    ) -> str:
        self._fresh(ingress)
        selection = session.get(ChannelOperatorAccountSelectionRecord, ingress.account_key)
        if selection is None:
            raise PermissionError("whatsapp_account_not_selected")
        revoked = session.get(ChannelOperatorAccountRevocationRecord, ingress.account_key)
        chosen = ChannelOperatorAccountStore._snapshot(selection, revoked=revoked is not None)
        if not chosen.currently_selected:
            raise PermissionError("whatsapp_account_revoked")
        if not hmac.compare_digest(chosen.app_secret_identity, self.app_secret_identity):
            raise PermissionError("whatsapp_account_secret_mismatch")

        thread_fingerprint = ingress.conversation_fingerprint()
        binding = session.get(ChannelThreadBindingRecord, thread_fingerprint)
        if binding is None:
            raise PermissionError("whatsapp_thread_unbound")
        bound = ChannelBindingStore._verified_binding(binding)
        if (
            bound.channel != "whatsapp"
            or binding.account_key != ingress.account_key
            or binding.sender_key != ingress.sender_key
            or binding.thread_key != ingress.thread_key
        ):
            raise PermissionError("whatsapp_thread_identity_invalid")

        fingerprint = ingress.message_fingerprint()
        if session.get(ChannelIngressObservationRecord, fingerprint) is not None:
            raise ValueError("whatsapp_unverified_observation_conflict")
        existing = session.get(VerifiedWhatsAppIngressRecord, fingerprint)
        if existing is not None:
            if (
                existing.thread_fingerprint != thread_fingerprint
                or existing.message_key != ingress.message_key
                or existing.text_sha256 != ingress.text_sha256
                or existing.provider_received_at != ingress.provider_received_at
                or existing.app_secret_identity != self.app_secret_identity
            ):
                raise ValueError("whatsapp_message_replay_conflict")
            if turn is not None and (created or existing.conversation_turn_id != turn.id):
                raise ValueError("whatsapp_message_replay_conflict")

        if turn is not None:
            if (
                turn.conversation_id != bound.conversation_id
                or turn.idempotency_key != ingress.idempotency_key()
                or turn.user_content != ingress.text
                or turn.mind_action.get("type") != "reply"
                or turn.core_task_id is not None
            ):
                raise PermissionError("whatsapp_reply_only")
            if existing is None:
                if not created:
                    raise ValueError("whatsapp_message_missing_verified_ledger")
                session.add(
                    VerifiedWhatsAppIngressRecord(
                        message_fingerprint=fingerprint,
                        thread_fingerprint=thread_fingerprint,
                        message_key=ingress.message_key,
                        text_sha256=ingress.text_sha256,
                        provider_received_at=ingress.provider_received_at,
                        admitted_at=self.clock(),
                        app_secret_identity=self.app_secret_identity,
                        conversation_turn_id=turn.id,
                    )
                )
                session.flush()
        elif existing is not None:
            saved = session.get(ConversationTurn, existing.conversation_turn_id)
            if (
                saved is None
                or saved.conversation_id != bound.conversation_id
                or saved.idempotency_key != ingress.idempotency_key()
                or saved.user_content != ingress.text
                or saved.mind_action.get("type") != "reply"
                or saved.core_task_id is not None
            ):
                raise ValueError("whatsapp_message_replay_conflict")
        return bound.conversation_id

    async def admit(
        self, raw_body: bytes, signature_header: str
    ) -> ConversationTurnResponse | None:
        """Admit one signed reply-only text; status-only events do nothing."""

        ingress = prepare_unverified_whatsapp_ingress(
            raw_body,
            signature_header,
            app_secret=self.app_secret,
            identity_secret=self.identity_secret,
        )
        if ingress is None:
            return None
        with self.database.read() as session:
            conversation_id = self._checked(session, ingress)

        # Mind generates outside a write transaction. Recheck and append the
        # verified ledger inside the same BEGIN IMMEDIATE transaction as the
        # turn write, so revocation cannot slip between policy and persistence.
        def admission(session: Session, turn: ConversationTurn, created: bool) -> None:
            self._checked(session, ingress, turn=turn, created=created)

        return await self.mind.send_turn(
            conversation_id,
            ingress.text,
            ingress.idempotency_key(),
            admission=admission,
        )
