"""One durable possible WhatsApp send claim; no provider transport."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorPrincipal
from app.channel_operator_accounts import ChannelOperatorAccountStore
from app.main import create_app
from app.models import ConversationTurn, WhatsAppSendAttemptRecord
from app.whatsapp_reply_intents import WhatsAppReplyIntentStore
from app.whatsapp_send_attempt import WhatsAppSendAttemptStore
from tests.test_whatsapp_verified_ingress import (
    APP_SECRET,
    IDENTITY_SECRET,
    Provider,
    body,
    setup,
    signed,
)


def test_one_claim_burns_exact_approved_reply_without_raw_id_storage(settings, database):
    provider = Provider(action={"type": "reply", "content": "مرحبا أحمد 👋"})
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        replies = WhatsAppReplyIntentStore(database)
        prepared, _ = replies.prepare(turn.id, principal=OperatorPrincipal())
        replies.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())
        arguments = dict(
            principal=OperatorPrincipal(),
            app_secret=APP_SECRET,
            identity_secret=IDENTITY_SECRET,
            waba_id="123456789012",
            phone_number_id="987654321",
            recipient_wa_id="15550001111",
            inbound_message_id="wamid.HBgLNTQ5MTExAA==",
        )
        attempts = WhatsAppSendAttemptStore(database)
        claimed = attempts.claim(prepared.intent_fingerprint, **arguments)
        assert claimed.intent_fingerprint == prepared.intent_fingerprint
        assert claimed.projection.text == "مرحبا أحمد 👋"
        assert claimed.phase == "send_outcome_unresolved"
        assert claimed.send_may_have_occurred is True
        assert claimed.automatic_retry_allowed is False
        assert claimed.transport_authority is False
        assert "15550001111" not in repr(claimed)
        assert "مرحبا" not in repr(claimed)
        with pytest.raises(PermissionError, match="already_claimed"):
            attempts.claim(prepared.intent_fingerprint, **arguments)
        with database.read() as session:
            assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 1
        for suffix in ("", "-wal", "-shm"):
            db_file = Path(f"{database.path}{suffix}")
            if db_file.exists():
                durable_bytes = db_file.read_bytes()
                for field in (
                    "waba_id",
                    "phone_number_id",
                    "recipient_wa_id",
                    "inbound_message_id",
                ):
                    assert arguments[field].encode("utf-8") not in durable_bytes


def _prepared(settings, database, *, decision="approve"):
    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        turn = asyncio.run(adapter.admit(raw, signed(raw)))
        replies = WhatsAppReplyIntentStore(database)
        prepared, _ = replies.prepare(turn.id, principal=OperatorPrincipal())
        if decision is not None:
            replies.decide(prepared.intent_fingerprint, decision, principal=OperatorPrincipal())
        return prepared, ingress, turn.id


def _arguments():
    return dict(
        principal=OperatorPrincipal(),
        app_secret=APP_SECRET,
        identity_secret=IDENTITY_SECRET,
        waba_id="123456789012",
        phone_number_id="987654321",
        recipient_wa_id="15550001111",
        inbound_message_id="wamid.HBgLNTQ5MTExAA==",
    )


@pytest.mark.parametrize("state", ["undecided", "rejected", "revoked", "edited", "nonoperator"])
def test_claim_refuses_without_current_exact_operator_approval(settings, database, state):
    prepared, ingress, turn_id = _prepared(
        settings,
        database,
        decision=None if state == "undecided" else "reject" if state == "rejected" else "approve",
    )
    if state == "revoked":
        ChannelOperatorAccountStore(database).revoke(
            account_key=ingress.account_key, principal=OperatorPrincipal()
        )
    if state == "edited":
        with database.transaction() as session:
            session.get(ConversationTurn, turn_id).assistant_content = "Changed reply"
    arguments = _arguments()
    if state == "nonoperator":
        arguments["principal"] = None
    with pytest.raises(PermissionError):
        WhatsAppSendAttemptStore(database).claim(prepared.intent_fingerprint, **arguments)
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 0


@pytest.mark.parametrize(
    ("field", "bad_value", "error"),
    [
        ("waba_id", "123456789013", PermissionError),
        ("phone_number_id", "987654322", PermissionError),
        ("recipient_wa_id", "15550001112", PermissionError),
        ("inbound_message_id", "wamid.HBgLNTQ5MTExAB==", PermissionError),
        ("app_secret", bytes(range(32, 64)), PermissionError),
        ("identity_secret", bytes(range(96, 128)), PermissionError),
        ("waba_id", "not-an-id", ValueError),
    ],
)
def test_claim_refuses_wrong_or_malformed_provider_identity(
    settings, database, field, bad_value, error
):
    prepared, _, _ = _prepared(settings, database)
    arguments = _arguments()
    arguments[field] = bad_value
    with pytest.raises(error):
        WhatsAppSendAttemptStore(database).claim(prepared.intent_fingerprint, **arguments)
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 0


def test_concurrent_claims_have_one_durable_winner(settings, database):
    prepared, _, _ = _prepared(settings, database)
    barrier = Barrier(2)
    attempts = WhatsAppSendAttemptStore(database)

    def claim_once():
        barrier.wait(timeout=5)
        try:
            attempts.claim(prepared.intent_fingerprint, **_arguments())
        except PermissionError as exc:
            return str(exc)
        return "claimed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim_once(), range(2)))
    assert sorted(outcomes) == ["claimed", "whatsapp_send_attempt_already_claimed"]
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 1


def test_direct_sql_cannot_insert_rejected_attempt(settings, database):
    prepared, _, _ = _prepared(settings, database, decision="reject")
    with pytest.raises(IntegrityError, match="approval invalid"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO whatsapp_send_attempts "
                    "(intent_fingerprint,operator_id,claimed_at,phase,send_may_have_occurred,"
                    "automatic_retry_allowed,transport_authority) VALUES "
                    "(:fingerprint,'ahmad',CURRENT_TIMESTAMP,'send_outcome_unresolved',1,0,0)"
                ),
                {"fingerprint": prepared.intent_fingerprint},
            )
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 0


def test_claim_row_rejects_sql_update_delete_replace(settings, database):
    prepared, _, _ = _prepared(settings, database)
    WhatsAppSendAttemptStore(database).claim(prepared.intent_fingerprint, **_arguments())
    for statement in (
        "UPDATE whatsapp_send_attempts SET transport_authority=transport_authority "
        "WHERE intent_fingerprint=:fingerprint",
        "DELETE FROM whatsapp_send_attempts WHERE intent_fingerprint=:fingerprint",
        "INSERT OR REPLACE INTO whatsapp_send_attempts "
        "SELECT * FROM whatsapp_send_attempts WHERE intent_fingerprint=:fingerprint",
    ):
        with pytest.raises(IntegrityError, match="append-only"):
            with database.transaction() as session:
                session.execute(text(statement), {"fingerprint": prepared.intent_fingerprint})
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 1


def test_direct_sql_cannot_claim_revoked_account(settings, database):
    prepared, ingress, _ = _prepared(settings, database)
    ChannelOperatorAccountStore(database).revoke(
        account_key=ingress.account_key, principal=OperatorPrincipal()
    )
    with pytest.raises(IntegrityError, match="approval invalid"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO whatsapp_send_attempts "
                    "(intent_fingerprint,operator_id,claimed_at,phase,send_may_have_occurred,"
                    "automatic_retry_allowed,transport_authority) VALUES "
                    "(:fingerprint,'ahmad',CURRENT_TIMESTAMP,'send_outcome_unresolved',1,0,0)"
                ),
                {"fingerprint": prepared.intent_fingerprint},
            )
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendAttemptRecord)) == 0


def test_populated_0053_upgrade_preserves_approved_reply_and_allows_claim(settings):
    import hashlib

    from alembic import command

    from app.channel_binding_store import ChannelBindingStore
    from app.channel_contracts import ChannelThreadBindingSpec
    from app.channel_operator_accounts import ChannelOperatorAccountStore
    from app.database import Database, migration_config
    from app.models import Conversation, VerifiedWhatsAppIngressRecord
    from app.whatsapp_opaque_ingress import prepare_unverified_whatsapp_ingress
    from app.whatsapp_verified_ingress import whatsapp_app_secret_identity
    from tests.test_whatsapp_verified_ingress import NOW

    database = Database(settings.database_path)
    try:
        with database.engine.connect() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "0053_whatsapp_reply_intents")
        raw = body()
        ingress = prepare_unverified_whatsapp_ingress(
            raw, signed(raw), app_secret=APP_SECRET, identity_secret=IDENTITY_SECRET
        )
        with database.transaction() as session:
            conversation = Conversation(title="Pre-0054 reply")
            session.add(conversation)
            session.flush()
            turn = ConversationTurn(
                conversation_id=conversation.id,
                number=1,
                idempotency_key=ingress.idempotency_key(),
                user_content=ingress.text,
                assistant_content="Old exact reply",
                mind_action={"type": "reply", "content": "Old exact reply"},
                selected_alias="fixture",
                selected_model="fixture",
                usage={},
            )
            session.add(turn)
            session.flush()
            turn_id = turn.id
            conversation_id = conversation.id
        ChannelOperatorAccountStore(database).select(
            account_key=ingress.account_key,
            app_secret_identity=whatsapp_app_secret_identity(APP_SECRET, IDENTITY_SECRET),
            principal=OperatorPrincipal(),
        )
        ChannelBindingStore(database).bind_thread(
            ChannelThreadBindingSpec(
                channel="whatsapp",
                account_key=ingress.account_key,
                sender_key=ingress.sender_key,
                thread_key=ingress.thread_key,
            ),
            conversation_id=conversation_id,
            principal=OperatorPrincipal(),
        )
        with database.transaction() as session:
            session.add(
                VerifiedWhatsAppIngressRecord(
                    message_fingerprint=ingress.message_fingerprint(),
                    thread_fingerprint=ingress.conversation_fingerprint(),
                    message_key=ingress.message_key,
                    text_sha256=hashlib.sha256(ingress.text.encode()).hexdigest(),
                    provider_received_at=NOW,
                    admitted_at=NOW,
                    app_secret_identity=whatsapp_app_secret_identity(APP_SECRET, IDENTITY_SECRET),
                    conversation_turn_id=turn_id,
                )
            )
        replies = WhatsAppReplyIntentStore(database)
        prepared, created = replies.prepare(turn_id, principal=OperatorPrincipal())
        assert created
        replies.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())

        database.migrate()
        database.check_schema()
        with database.read() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0054_whatsapp_send_attempt"
            )
            assert session.get(ConversationTurn, turn_id).assistant_content == "Old exact reply"
            assert session.scalar(text("SELECT COUNT(*) FROM whatsapp_reply_intents")) == 1
            assert session.scalar(text("SELECT COUNT(*) FROM whatsapp_reply_decisions")) == 1
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
            assert session.scalar(text("PRAGMA integrity_check")) == "ok"
        claimed = WhatsAppSendAttemptStore(database).claim(
            prepared.intent_fingerprint, **_arguments()
        )
        assert claimed.projection.text == "Old exact reply"
        with database.engine.connect() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="Destructive downgrade is disabled"):
                command.downgrade(config, "-1")
        database.check_schema()
    finally:
        database.close()
