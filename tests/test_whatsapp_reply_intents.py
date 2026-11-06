"""Offline, operator-only WhatsApp reply intent and decision evidence."""

from sqlalchemy import text


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
