"""Inert channel binding and unverified replay metadata. No provider/Mind execution."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.auth import OperatorPrincipal
from app.channel_contracts import (
    ChannelTextIngress,
    ChannelThreadBindingSpec,
    channel_message_fingerprint,
)
from app.models import (
    ChannelIngressObservationRecord,
    ChannelThreadBindingRecord,
    Conversation,
    utcnow,
)
from app.storage import SessionStore


@dataclass(frozen=True)
class BoundChannelThread:
    thread_fingerprint: str
    conversation_id: str
    channel: str
    created_at: datetime
    provider_authenticated: bool = False
    transport_authority: bool = False


@dataclass(frozen=True)
class ObservedChannelMessage:
    message_fingerprint: str
    thread_fingerprint: str
    text_sha256: str
    provider_received_at: datetime
    observed_at: datetime
    state: str = "unverified"
    mind_executed: bool = False
    delivery_authority: bool = False


def _require_ahmad(principal: OperatorPrincipal) -> None:
    if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
        raise PermissionError("channel_operator_required")


class ChannelBindingStore:
    """Store operator-bound thread identity and unverified message metadata only.

    No external provider adapter, MindService, Core TaskService or transport
    action uses this store in this slice.
    """

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    @staticmethod
    def _verified_binding(row: ChannelThreadBindingRecord) -> BoundChannelThread:
        try:
            identity = ChannelThreadBindingSpec(
                channel=row.channel,
                account_key=row.account_key,
                sender_key=row.sender_key,
                thread_key=row.thread_key,
            )
        except ValueError:
            raise ValueError("channel_binding_identity_invalid") from None
        if (
            identity.fingerprint() != row.thread_fingerprint
            or row.operator_id != "ahmad"
            or row.provider_authenticated
            or row.transport_authority
        ):
            raise ValueError("channel_binding_identity_invalid")
        return BoundChannelThread(
            thread_fingerprint=row.thread_fingerprint,
            conversation_id=row.conversation_id,
            channel=row.channel,
            created_at=row.created_at,
        )

    @staticmethod
    def _verified_observation(
        row: ChannelIngressObservationRecord,
    ) -> ObservedChannelMessage:
        if (
            channel_message_fingerprint(row.thread_fingerprint, row.message_key)
            != row.message_fingerprint
            or row.operator_id != "ahmad"
            or row.state != "unverified"
            or row.mind_executed
            or row.delivery_authority
        ):
            raise ValueError("channel_observation_identity_invalid")
        return ObservedChannelMessage(
            message_fingerprint=row.message_fingerprint,
            thread_fingerprint=row.thread_fingerprint,
            text_sha256=row.text_sha256,
            provider_received_at=row.provider_received_at,
            observed_at=row.observed_at,
        )

    @staticmethod
    def _get_thread(session: Session, fingerprint: str) -> ChannelThreadBindingRecord | None:
        return session.get(ChannelThreadBindingRecord, fingerprint)

    def bind_thread(
        self,
        identity: ChannelThreadBindingSpec,
        *,
        conversation_id: str,
        principal: OperatorPrincipal,
    ) -> tuple[BoundChannelThread, bool]:
        """Immutable Ahmad-selected mapping; never authenticates the provider."""

        _require_ahmad(principal)
        identity = ChannelThreadBindingSpec.model_validate(
            identity.model_dump(mode="json"), strict=True
        )
        fingerprint = identity.fingerprint()
        with self.database.transaction() as session:
            if session.get(Conversation, conversation_id) is None:
                raise LookupError("channel_conversation_not_found")
            existing = self._get_thread(session, fingerprint)
            if existing is not None:
                stored = self._verified_binding(existing)
                if stored.conversation_id != conversation_id:
                    raise ValueError("channel_thread_already_bound")
                return stored, False

            row = ChannelThreadBindingRecord(
                thread_fingerprint=fingerprint,
                channel=identity.channel,
                account_key=identity.account_key,
                sender_key=identity.sender_key,
                thread_key=identity.thread_key,
                conversation_id=conversation_id,
                operator_id=principal.subject,
                provider_authenticated=False,
                transport_authority=False,
            )
            session.add(row)
            session.flush()
            return self._verified_binding(row), True

    def get_thread(
        self,
        thread_fingerprint: str,
        *,
        principal: OperatorPrincipal,
    ) -> BoundChannelThread | None:
        _require_ahmad(principal)
        with self.database.read() as session:
            row = self._get_thread(session, thread_fingerprint)
            return self._verified_binding(row) if row is not None else None

    def observe_unverified(
        self,
        ingress: ChannelTextIngress,
        *,
        principal: OperatorPrincipal,
    ) -> tuple[ObservedChannelMessage, bool]:
        """Retain digest/ID only, never insert a Mind turn or schedule delivery."""

        _require_ahmad(principal)
        ingress = ChannelTextIngress.model_validate(ingress.model_dump(mode="python"), strict=True)
        thread_fingerprint = ingress.conversation_fingerprint()
        fingerprint = ingress.message_fingerprint()
        with self.database.transaction() as session:
            binding = self._get_thread(session, thread_fingerprint)
            if binding is None:
                raise ValueError("channel_thread_unbound")
            self._verified_binding(binding)
            if (
                binding.channel != ingress.channel
                or binding.account_key != ingress.account_key
                or binding.sender_key != ingress.sender_key
                or binding.thread_key != ingress.thread_key
            ):
                raise ValueError("channel_binding_identity_invalid")

            existing = session.get(ChannelIngressObservationRecord, fingerprint)
            if existing is not None:
                stored = self._verified_observation(existing)
                if (
                    stored.thread_fingerprint != thread_fingerprint
                    or existing.message_key != ingress.message_key
                    or stored.text_sha256 != ingress.text_sha256
                    or stored.provider_received_at != ingress.provider_received_at
                ):
                    raise ValueError("channel_message_replay_conflict")
                return stored, False

            row = ChannelIngressObservationRecord(
                message_fingerprint=fingerprint,
                thread_fingerprint=thread_fingerprint,
                message_key=ingress.message_key,
                text_sha256=ingress.text_sha256,
                provider_received_at=ingress.provider_received_at,
                observed_at=utcnow(),
                operator_id=principal.subject,
                state="unverified",
                mind_executed=False,
                delivery_authority=False,
            )
            session.add(row)
            session.flush()
            return self._verified_observation(row), True

    def get_observation(
        self,
        message_fingerprint: str,
        *,
        principal: OperatorPrincipal,
    ) -> ObservedChannelMessage | None:
        _require_ahmad(principal)
        with self.database.read() as session:
            row = session.get(ChannelIngressObservationRecord, message_fingerprint)
            return self._verified_observation(row) if row is not None else None
