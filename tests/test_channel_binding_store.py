"""v0.16 inert channel-binding and unverified observation persistence."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorPrincipal
from app.channel_binding_store import ChannelBindingStore
from app.channel_contracts import ChannelTextIngress, ChannelThreadBindingSpec
from app.models import (
    ChannelIngressObservationRecord,
    ChannelThreadBindingRecord,
    Conversation,
    ConversationTurn,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def thread(**overrides) -> ChannelThreadBindingSpec:
    params = {
        "channel": "whatsapp",
        "account_key": "a" * 64,
        "sender_key": "b" * 64,
        "thread_key": "c" * 64,
    }
    params.update(overrides)
    return ChannelThreadBindingSpec(**params)


def message(**overrides) -> ChannelTextIngress:
    value = "Test privately owned text that must never appear in ledger records."
    params = {
        "channel": "whatsapp",
        "account_key": "a" * 64,
        "sender_key": "b" * 64,
        "thread_key": "c" * 64,
        "message_key": "d" * 64,
        "text": value,
        "text_sha256": digest(value),
        "provider_received_at": datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    }
    params.update(overrides)
    return ChannelTextIngress(**params)


def conversation(database, title: str = "Channel test") -> str:
    with database.transaction() as session:
        row = Conversation(title=title)
        session.add(row)
        session.flush()
        return row.id


def bind(database):
    store = ChannelBindingStore(database)
    conversation_id = conversation(database)
    binding, created = store.bind_thread(
        thread(), conversation_id=conversation_id, principal=OperatorPrincipal()
    )
    assert created
    return store, conversation_id, binding


def test_operator_binding_requires_existing_conversation(database):
    store = ChannelBindingStore(database)
    with pytest.raises(LookupError, match="channel_conversation_not_found"):
        store.bind_thread(
            thread(),
            conversation_id="00000000-0000-4000-8000-000000000001",
            principal=OperatorPrincipal(),
        )


def test_operator_binding_idempotent_with_opaque_metadata(database):
    store, conversation_id, bound = bind(database)
    repeated, created = store.bind_thread(
        thread(), conversation_id=conversation_id, principal=OperatorPrincipal()
    )
    assert not created
    assert repeated == bound
    assert bound.thread_fingerprint == thread().fingerprint()
    assert bound.channel == "whatsapp"
    assert not bound.provider_authenticated
    assert not bound.transport_authority
    assert store.get_thread(bound.thread_fingerprint, principal=OperatorPrincipal()) == bound
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(ChannelThreadBindingRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 0


def test_thread_cannot_be_remapped_to_another_conversation(database):
    store, _old, bound = bind(database)
    different = conversation(database, "Second channel target")
    with pytest.raises(ValueError, match="channel_thread_already_bound"):
        store.bind_thread(thread(), conversation_id=different, principal=OperatorPrincipal())
    assert (
        store.get_thread(bound.thread_fingerprint, principal=OperatorPrincipal()).conversation_id
        != different
    )


@pytest.mark.parametrize(
    "different",
    [
        {"channel": "email"},
        {"account_key": "e" * 64},
        {"sender_key": "f" * 64},
        {"thread_key": "1" * 64},
    ],
)
def test_thread_identity_is_scoped_by_provider_account_sender_and_thread(database, different):
    store, conversation_id, bound = bind(database)
    other, created = store.bind_thread(
        thread(**different),
        conversation_id=conversation_id,
        principal=OperatorPrincipal(),
    )
    assert created
    assert other.thread_fingerprint != bound.thread_fingerprint


def test_binding_cannot_be_asserted_by_non_operator(database):
    store = ChannelBindingStore(database)
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store.bind_thread(thread(), conversation_id=conversation(database), principal="ahmad")
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store.get_thread("a" * 64, principal=None)


def test_message_observation_requires_existing_thread(database):
    store = ChannelBindingStore(database)
    with pytest.raises(ValueError, match="channel_thread_unbound"):
        store.observe_unverified(message(), principal=OperatorPrincipal())


def test_unverified_message_is_digest_only_never_a_mind_turn(database):
    store, _conversation_id, bound = bind(database)
    ingress = message()
    first, created = store.observe_unverified(ingress, principal=OperatorPrincipal())
    assert created
    assert first.message_fingerprint == ingress.message_fingerprint()
    assert first.thread_fingerprint == bound.thread_fingerprint
    assert first.text_sha256 == ingress.text_sha256
    assert first.state == "unverified"
    assert not first.mind_executed
    assert not first.delivery_authority
    assert store.get_observation(first.message_fingerprint, principal=OperatorPrincipal()) == first
    again, created = store.observe_unverified(ingress, principal=OperatorPrincipal())
    assert not created and again == first
    with database.read() as session:
        rows = session.scalars(select(ChannelIngressObservationRecord)).all()
        assert len(rows) == 1
        stored = rows[0]
        assert stored.message_key == ingress.message_key
        assert not stored.mind_executed
        assert not stored.delivery_authority
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 0
        assert ingress.text not in str(stored.__dict__)


def test_message_replay_with_same_identity_and_changed_text_is_refused(database):
    store, _id, _binding = bind(database)
    previous = message()
    store.observe_unverified(previous, principal=OperatorPrincipal())
    changed = "Edited content must not be silently replayed."
    new_value = message(text=changed, text_sha256=digest(changed))
    assert previous.message_fingerprint() == new_value.message_fingerprint()
    with pytest.raises(ValueError, match="channel_message_replay_conflict"):
        store.observe_unverified(new_value, principal=OperatorPrincipal())


def test_message_timestamp_drift_is_refused_for_same_message_id(database):
    store, _id, _binding = bind(database)
    previous = message()
    store.observe_unverified(previous, principal=OperatorPrincipal())
    changed = message(provider_received_at=previous.provider_received_at + timedelta(seconds=1))
    with pytest.raises(ValueError, match="channel_message_replay_conflict"):
        store.observe_unverified(changed, principal=OperatorPrincipal())


def test_same_message_key_in_distinct_bound_threads_has_distinct_identity(database):
    store, conversation_id, _binding = bind(database)
    store.bind_thread(
        thread(thread_key="f" * 64),
        conversation_id=conversation_id,
        principal=OperatorPrincipal(),
    )
    first, created = store.observe_unverified(message(), principal=OperatorPrincipal())
    assert created
    second, created = store.observe_unverified(
        message(thread_key="f" * 64), principal=OperatorPrincipal()
    )
    assert created
    assert first.message_fingerprint != second.message_fingerprint


def test_observation_requires_operator_even_for_replays(database):
    store, _id, _binding = bind(database)
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store.observe_unverified(message(), principal=None)
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store.get_observation(message().message_fingerprint(), principal="ahmad")


def test_channel_tables_are_append_only_in_sqlite(database):
    store, _id, binding = bind(database)
    observed, _ = store.observe_unverified(message(), principal=OperatorPrincipal())
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE channel_thread_bindings SET operator_id = 'ahmad' "
                    "WHERE thread_fingerprint=:id"
                ),
                {"id": binding.thread_fingerprint},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM channel_ingress_observations WHERE message_fingerprint=:id"),
                {"id": observed.message_fingerprint},
            )


def test_database_rejects_forged_provider_or_transport_authority(database):
    store, _id, bound = bind(database)
    with database.read() as session:
        row = session.get(ChannelThreadBindingRecord, bound.thread_fingerprint)
        fields = {
            "fingerprint": "f" * 64,
            "channel": "whatsapp",
            "account": "e" * 64,
            "sender": "b" * 64,
            "thread": "c" * 64,
            "conversation": row.conversation_id,
        }
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO channel_thread_bindings "
                    "(thread_fingerprint,channel,account_key,sender_key,thread_key,"
                    "conversation_id,operator_id,provider_authenticated,"
                    "transport_authority,created_at) "
                    "VALUES (:fingerprint,:channel,:account,:sender,:thread,:conversation,"
                    "'ahmad',1,0,:now)"
                ),
                {**fields, "now": datetime.now(UTC)},
            )
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(ChannelThreadBindingRecord)) == 1


def test_database_rejects_observation_execution_claim(database):
    _store, _conversation_id, binding = bind(database)
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO channel_ingress_observations "
                    "(message_fingerprint,thread_fingerprint,message_key,text_sha256,"
                    "provider_received_at,observed_at,operator_id,state,mind_executed,"
                    "delivery_authority) "
                    "VALUES (:message,:thread,:key,:digest,:received,:observed,"
                    "'ahmad','unverified',1,0)"
                ),
                {
                    "message": "a" * 64,
                    "thread": binding.thread_fingerprint,
                    "key": "e" * 64,
                    "digest": "f" * 64,
                    "received": datetime.now(UTC),
                    "observed": datetime.now(UTC),
                },
            )


def test_database_rejects_malformed_channel_identity_even_without_adapter(database):
    conversation_id = conversation(database)
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO channel_thread_bindings "
                    "(thread_fingerprint,channel,account_key,sender_key,thread_key,"
                    "conversation_id,operator_id,provider_authenticated,transport_authority,"
                    "created_at) "
                    "VALUES (:fingerprint,'whatsapp',:account,:sender,:thread,"
                    ":conversation,'ahmad',0,0,:now)"
                ),
                {
                    "fingerprint": "G" * 64,
                    "account": "a" * 64,
                    "sender": "b" * 64,
                    "thread": "c" * 64,
                    "conversation": conversation_id,
                    "now": datetime.now(UTC),
                },
            )
