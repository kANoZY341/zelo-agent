"""Offline, operator-only WhatsApp reply intent and decision evidence."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.auth import OperatorPrincipal
from app.channel_operator_accounts import ChannelOperatorAccountStore
from app.main import create_app
from app.models import ConversationTurn, WhatsAppReplyDecisionRecord, WhatsAppReplyIntentRecord
from app.whatsapp_reply_intents import WhatsAppReplyIntentStore
from tests.test_whatsapp_verified_ingress import Provider, body, setup, signed


def test_reply_intent_tables_are_append_only_without_rowids(database):
    with database.read() as session:
        definitions = dict(
            session.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type IN ('table','trigger')")
            ).all()
        )
    for name in ("whatsapp_reply_intents", "whatsapp_reply_decisions"):
        assert "WITHOUT ROWID" in definitions[name].upper()
        for operation in ("update", "delete", "duplicate_insert"):
            assert f"{name}_no_{operation}" in definitions


def test_reply_intent_cannot_replace_existing_turn_under_new_fingerprint(settings, database):
    import asyncio
    import hashlib

    import pytest
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import IntegrityError

    from app.channel_contracts import ChannelDeliveryIntent
    from app.main import create_app
    from tests.test_whatsapp_verified_ingress import Provider, body, setup, signed

    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        intent = ChannelDeliveryIntent(
            channel="whatsapp",
            account_key=ingress.account_key,
            recipient_key=ingress.sender_key,
            thread_key=ingress.thread_key,
            conversation_turn_id=turn.id,
            text=turn.assistant_content,
            text_sha256=hashlib.sha256(turn.assistant_content.encode()).hexdigest(),
        )
        values = {
            "fingerprint": intent.delivery_fingerprint(),
            "message": ingress.message_fingerprint(),
            "turn": turn.id,
            "thread": ingress.conversation_fingerprint(),
            "account": ingress.account_key,
            "recipient": ingress.sender_key,
            "thread_key": ingress.thread_key,
            "digest": intent.text_sha256,
        }
        insert = text(
            "INSERT OR REPLACE INTO whatsapp_reply_intents "
            "(intent_fingerprint,verified_message_fingerprint,conversation_turn_id,"
            "thread_fingerprint,account_key,recipient_key,thread_key,text_sha256,"
            "operator_id,prepared_at,transport_authority) "
            "VALUES (:fingerprint,:message,:turn,:thread,:account,:recipient,:thread_key,"
            ":digest,'ahmad',CURRENT_TIMESTAMP,0)"
        )
        with database.transaction() as session:
            session.execute(insert, values)
        with pytest.raises(IntegrityError, match="append-only"):
            with database.transaction() as session:
                session.execute(insert, {**values, "fingerprint": "e" * 64})
        with database.read() as session:
            assert session.execute(
                text("SELECT intent_fingerprint FROM whatsapp_reply_intents")
            ).scalars().all() == [intent.delivery_fingerprint()]


def test_operator_prepares_and_decides_exact_verified_reply(settings, database):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        store = WhatsAppReplyIntentStore(database)
        prepared, created = store.prepare(turn.id, principal=OperatorPrincipal())
        assert created is True
        assert prepared.conversation_turn_id == turn.id
        assert prepared.account_key == ingress.account_key
        assert prepared.recipient_key == ingress.sender_key
        assert prepared.transport_authority is False
        assert store.prepare(turn.id, principal=OperatorPrincipal()) == (prepared, False)
        decision, created = store.decide(
            prepared.intent_fingerprint, "approve", principal=OperatorPrincipal()
        )
        assert created is True
        assert decision.decision == "approve"
        assert decision.transport_authority is False
        assert store.decide(
            prepared.intent_fingerprint, "approve", principal=OperatorPrincipal()
        ) == (decision, False)
        with pytest.raises(ValueError, match="decision_conflict"):
            store.decide(prepared.intent_fingerprint, "reject", principal=OperatorPrincipal())
        with database.read() as session:
            assert session.scalar(select(func.count()).select_from(WhatsAppReplyIntentRecord)) == 1
            assert (
                session.scalar(select(func.count()).select_from(WhatsAppReplyDecisionRecord)) == 1
            )


def test_revocation_after_prepare_blocks_decision(settings, database):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        store = WhatsAppReplyIntentStore(database)
        prepared, _ = store.prepare(turn.id, principal=OperatorPrincipal())
        ChannelOperatorAccountStore(database).revoke(
            account_key=ingress.account_key, principal=OperatorPrincipal()
        )
        with pytest.raises(PermissionError, match="account_revoked"):
            store.prepare(turn.id, principal=OperatorPrincipal())
        with pytest.raises(PermissionError, match="account_revoked"):
            store.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())
        with database.read() as session:
            assert (
                session.scalar(select(func.count()).select_from(WhatsAppReplyDecisionRecord)) == 0
            )


def test_unverified_turn_and_nonoperator_cannot_prepare(settings, database):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, conversation_id, _ = setup(database, client.app.state.mind)
        turn = asyncio.run(client.app.state.mind.send_turn(conversation_id, "ordinary", "ordinary"))
        store = WhatsAppReplyIntentStore(database)
        with pytest.raises(PermissionError, match="operator"):
            store.prepare(turn.id, principal=None)
        with pytest.raises(PermissionError, match="verified"):
            store.prepare(turn.id, principal=OperatorPrincipal())


def test_changed_saved_reply_cannot_reuse_prepared_intent(settings, database):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        store = WhatsAppReplyIntentStore(database)
        prepared, _ = store.prepare(turn.id, principal=OperatorPrincipal())
        with database.transaction() as session:
            session.get(ConversationTurn, turn.id).assistant_content = "Edited reply"
        with pytest.raises(ValueError, match="intent_conflict"):
            store.prepare(turn.id, principal=OperatorPrincipal())
        with pytest.raises(ValueError, match="intent_conflict"):
            store.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())


def test_forged_verified_message_fingerprint_cannot_prepare_reply(settings, database):
    import hashlib

    from app.models import VerifiedWhatsAppIngressRecord
    from app.whatsapp_verified_ingress import whatsapp_app_secret_identity
    from tests.test_whatsapp_verified_ingress import APP_SECRET, IDENTITY_SECRET, NOW

    with TestClient(create_app(settings, provider=Provider())) as client:
        _, conversation_id, ingress = setup(database, client.app.state.mind)
        false_fingerprint = "f" * 64
        turn = asyncio.run(
            client.app.state.mind.send_turn(
                conversation_id, "ordinary", f"channel:{false_fingerprint}"
            )
        )
        with database.transaction() as session:
            session.add(
                VerifiedWhatsAppIngressRecord(
                    message_fingerprint=false_fingerprint,
                    thread_fingerprint=ingress.conversation_fingerprint(),
                    message_key=ingress.message_key,
                    text_sha256=hashlib.sha256(b"ordinary").hexdigest(),
                    provider_received_at=NOW,
                    admitted_at=NOW,
                    app_secret_identity=whatsapp_app_secret_identity(APP_SECRET, IDENTITY_SECRET),
                    conversation_turn_id=turn.id,
                )
            )
        with pytest.raises(ValueError, match="verified_identity_invalid"):
            WhatsAppReplyIntentStore(database).prepare(turn.id, principal=OperatorPrincipal())


def test_task_or_empty_saved_reply_cannot_be_prepared(settings, database):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        store = WhatsAppReplyIntentStore(database)
        with database.transaction() as session:
            session.get(ConversationTurn, turn.id).mind_action = {"type": "core_task"}
        with pytest.raises(PermissionError, match="reply_only"):
            store.prepare(turn.id, principal=OperatorPrincipal())
        with database.transaction() as session:
            saved = session.get(ConversationTurn, turn.id)
            saved.mind_action = {"type": "reply", "content": ""}
            saved.assistant_content = ""
        with pytest.raises(PermissionError, match="reply_only"):
            store.prepare(turn.id, principal=OperatorPrincipal())


def test_rejection_is_immutable_and_decision_input_is_strict(settings, database):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        store = WhatsAppReplyIntentStore(database)
        prepared, _ = store.prepare(turn.id, principal=OperatorPrincipal())
        with pytest.raises(ValueError, match="decision_invalid"):
            store.decide(prepared.intent_fingerprint, "send", principal=OperatorPrincipal())
        rejected, created = store.decide(
            prepared.intent_fingerprint, "reject", principal=OperatorPrincipal()
        )
        assert created and rejected.decision == "reject"
        with pytest.raises(ValueError, match="decision_conflict"):
            store.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())


@pytest.mark.parametrize("table", ["whatsapp_reply_intents", "whatsapp_reply_decisions"])
def test_reply_evidence_rejects_sql_update_delete_replace(settings, database, table):
    from sqlalchemy.exc import IntegrityError

    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        store = WhatsAppReplyIntentStore(database)
        prepared, _ = store.prepare(turn.id, principal=OperatorPrincipal())
        store.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())
        for statement in (
            f"UPDATE {table} SET transport_authority=transport_authority "
            "WHERE intent_fingerprint=:fingerprint",
            f"DELETE FROM {table} WHERE intent_fingerprint=:fingerprint",
            f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} "
            "WHERE intent_fingerprint=:fingerprint",
        ):
            with pytest.raises(IntegrityError, match="append-only"):
                with database.transaction() as session:
                    session.execute(text(statement), {"fingerprint": prepared.intent_fingerprint})
