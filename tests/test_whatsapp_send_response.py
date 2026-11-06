"""Private WhatsApp response-shape observation after one claimed send."""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth import OperatorPrincipal
from app.models import WhatsAppSendResponseRecord
from app.whatsapp_send_attempt import WhatsAppSendAttemptStore
from app.whatsapp_send_response import WhatsAppSendResponseStore
from tests.test_whatsapp_send_attempt import _arguments, _prepared

OUTBOUND_WAMID = "wamid.HBgLQTEyMzQ1Ng=="


def _success_body(*, recipient="15550001111", wamid=OUTBOUND_WAMID):
    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "contacts": [{"input": recipient, "wa_id": recipient}],
            "messages": [{"id": wamid}],
        }
    ).encode("utf-8")


def test_first_response_shape_links_one_claim_without_raw_provider_data(settings, database):
    prepared, _, _ = _prepared(settings, database)
    WhatsAppSendAttemptStore(database).claim(prepared.intent_fingerprint, **_arguments())

    observed = WhatsAppSendResponseStore(database).observe(
        prepared.intent_fingerprint,
        principal=OperatorPrincipal(),
        identity_secret=_arguments()["identity_secret"],
        recipient_wa_id="15550001111",
        http_status=200,
        response_body=_success_body(),
    )

    assert observed.outcome == "success_shape"
    assert observed.http_status == 200
    assert observed.response_message_key is not None
    assert len(observed.response_message_key) == 64
    assert observed.delivery_confirmed is False
    assert observed.transport_authority is False
    assert OUTBOUND_WAMID not in repr(observed)
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendResponseRecord)) == 1
        row = session.get(WhatsAppSendResponseRecord, prepared.intent_fingerprint)
        assert row.response_message_key == observed.response_message_key
        assert row.delivery_confirmed is False
        assert row.transport_authority is False
    for suffix in ("", "-wal", "-shm"):
        db_file = Path(f"{database.path}{suffix}")
        if db_file.exists():
            durable = db_file.read_bytes()
            assert OUTBOUND_WAMID.encode() not in durable
            assert b"15550001111" not in durable
            assert _success_body() not in durable


def test_direct_sql_cannot_mark_success_shape_without_http_status(settings, database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    prepared, _, _ = _prepared(settings, database)
    WhatsAppSendAttemptStore(database).claim(prepared.intent_fingerprint, **_arguments())
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO whatsapp_send_responses "
                    "(intent_fingerprint,observed_at,http_status,outcome,response_message_key,"
                    "delivery_confirmed,transport_authority) VALUES "
                    "(:fingerprint,CURRENT_TIMESTAMP,NULL,'success_shape',:message_key,0,0)"
                ),
                {
                    "fingerprint": prepared.intent_fingerprint,
                    "message_key": "a" * 64,
                },
            )
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendResponseRecord)) == 0


def _claimed(settings, database):
    prepared, ingress, _ = _prepared(settings, database)
    WhatsAppSendAttemptStore(database).claim(prepared.intent_fingerprint, **_arguments())
    return prepared.intent_fingerprint, ingress


def _observe(database, intent_fingerprint, *, status=200, body=None, recipient="15550001111"):
    return WhatsAppSendResponseStore(database).observe(
        intent_fingerprint,
        principal=OperatorPrincipal(),
        identity_secret=_arguments()["identity_secret"],
        recipient_wa_id=recipient,
        http_status=status,
        response_body=_success_body() if body is None and status == 200 else body,
    )


@pytest.mark.parametrize(
    "bad_body",
    [
        b'{"messaging_product":"whatsapp","messaging_product":"whatsapp"}',
        b'{"messaging_product":"whatsapp","contacts":[],"messages":[]}',
        _success_body(recipient="15550001112"),
        _success_body(wamid="not-a-wamid"),
        json.dumps(
            {
                "messaging_product": "whatsapp",
                "contacts": [{"input": "15550001111", "wa_id": "15550001111"}],
                "messages": [{"id": OUTBOUND_WAMID}, {"id": "wamid.other"}],
            }
        ).encode(),
        _success_body()[:-1] + b',"error":null}',
        _success_body() + b" " * 8192,
        b"\xff",
    ],
)
def test_invalid_200_body_is_recorded_without_message_link(settings, database, bad_body):
    fingerprint, _ = _claimed(settings, database)
    observed = _observe(database, fingerprint, body=bad_body)
    assert observed.outcome == "invalid_success_shape"
    assert observed.http_status == 200
    assert observed.response_message_key is None
    with database.read() as session:
        assert session.get(WhatsAppSendResponseRecord, fingerprint).response_message_key is None


def test_failed_message_status_does_not_look_like_success_shape(settings, database):
    fingerprint, _ = _claimed(settings, database)
    body = json.dumps(
        {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "15550001111", "wa_id": "15550001111"}],
            "messages": [{"id": OUTBOUND_WAMID, "message_status": "failed"}],
        }
    ).encode()
    observed = _observe(database, fingerprint, body=body)
    assert observed.outcome == "invalid_success_shape"
    assert observed.response_message_key is None


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(None, None, "no_response"), (500, b'{"error":"server"}', "http_non_success")],
)
def test_unknown_or_non_success_http_keeps_attempt_spent(
    settings, database, status, body, expected
):
    fingerprint, _ = _claimed(settings, database)
    observed = _observe(database, fingerprint, status=status, body=body)
    assert observed.outcome == expected
    assert observed.response_message_key is None
    assert observed.delivery_confirmed is False
    with pytest.raises(PermissionError, match="already_claimed"):
        WhatsAppSendAttemptStore(database).claim(fingerprint, **_arguments())


def test_wrong_recipient_or_nonoperator_cannot_record(settings, database):
    fingerprint, _ = _claimed(settings, database)
    with pytest.raises(PermissionError, match="recipient_mismatch"):
        _observe(database, fingerprint, recipient="15550001112")
    with pytest.raises(PermissionError):
        WhatsAppSendResponseStore(database).observe(
            fingerprint,
            principal=None,
            identity_secret=_arguments()["identity_secret"],
            recipient_wa_id="15550001111",
            http_status=200,
            response_body=_success_body(),
        )
    with database.read() as session:
        assert session.get(WhatsAppSendResponseRecord, fingerprint) is None


def test_observation_requires_prior_claim(settings, database):
    prepared, _, _ = _prepared(settings, database)
    with pytest.raises(LookupError, match="attempt_required"):
        _observe(database, prepared.intent_fingerprint)
    with database.read() as session:
        assert session.get(WhatsAppSendResponseRecord, prepared.intent_fingerprint) is None


def test_first_observation_is_immutable_even_after_revocation(settings, database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.channel_operator_accounts import ChannelOperatorAccountStore

    fingerprint, ingress = _claimed(settings, database)
    ChannelOperatorAccountStore(database).revoke(
        account_key=ingress.account_key, principal=OperatorPrincipal()
    )
    observed = _observe(database, fingerprint)
    assert observed.outcome == "success_shape"
    with pytest.raises(PermissionError, match="already_observed"):
        _observe(database, fingerprint, status=500, body=b"")
    for statement in (
        "UPDATE whatsapp_send_responses SET outcome=outcome WHERE intent_fingerprint=:fingerprint",
        "DELETE FROM whatsapp_send_responses WHERE intent_fingerprint=:fingerprint",
        "INSERT OR REPLACE INTO whatsapp_send_responses "
        "SELECT * FROM whatsapp_send_responses WHERE intent_fingerprint=:fingerprint",
    ):
        with pytest.raises(IntegrityError, match="append-only"):
            with database.transaction() as session:
                session.execute(text(statement), {"fingerprint": fingerprint})
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendResponseRecord)) == 1


def test_concurrent_responses_have_one_durable_winner(settings, database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    fingerprint, _ = _claimed(settings, database)
    barrier = Barrier(2)

    def observe_once():
        barrier.wait(timeout=5)
        try:
            _observe(database, fingerprint)
        except PermissionError as exc:
            return str(exc)
        return "observed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: observe_once(), range(2)))
    assert sorted(outcomes) == ["observed", "whatsapp_send_response_already_observed"]
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(WhatsAppSendResponseRecord)) == 1


def test_direct_sql_cannot_mark_invalid_shape_without_http_status(settings, database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    fingerprint, _ = _claimed(settings, database)
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO whatsapp_send_responses "
                    "(intent_fingerprint,observed_at,http_status,outcome,response_message_key,"
                    "delivery_confirmed,transport_authority) VALUES "
                    "(:fingerprint,CURRENT_TIMESTAMP,NULL,'invalid_success_shape',NULL,0,0)"
                ),
                {"fingerprint": fingerprint},
            )
    with database.read() as session:
        assert session.get(WhatsAppSendResponseRecord, fingerprint) is None


def test_populated_0054_upgrade_preserves_attempt_and_allows_observation(settings):
    import hashlib

    from alembic import command
    from sqlalchemy import text

    from app.channel_binding_store import ChannelBindingStore
    from app.channel_contracts import ChannelThreadBindingSpec
    from app.channel_operator_accounts import ChannelOperatorAccountStore
    from app.database import Database, migration_config
    from app.models import Conversation, ConversationTurn, VerifiedWhatsAppIngressRecord
    from app.whatsapp_opaque_ingress import prepare_unverified_whatsapp_ingress
    from app.whatsapp_reply_intents import WhatsAppReplyIntentStore
    from app.whatsapp_verified_ingress import whatsapp_app_secret_identity
    from tests.test_whatsapp_verified_ingress import APP_SECRET, IDENTITY_SECRET, NOW, body, signed

    old_database = Database(settings.database_path)
    try:
        with old_database.engine.connect() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "0054_whatsapp_send_attempt")
        raw = body()
        ingress = prepare_unverified_whatsapp_ingress(
            raw, signed(raw), app_secret=APP_SECRET, identity_secret=IDENTITY_SECRET
        )
        with old_database.transaction() as session:
            conversation = Conversation(title="Pre-0055 reply")
            session.add(conversation)
            session.flush()
            turn = ConversationTurn(
                conversation_id=conversation.id,
                number=1,
                idempotency_key=ingress.idempotency_key(),
                user_content=ingress.text,
                assistant_content="Preserved exact reply",
                mind_action={"type": "reply", "content": "Preserved exact reply"},
                selected_alias="fixture",
                selected_model="fixture",
                usage={},
            )
            session.add(turn)
            session.flush()
            turn_id = turn.id
            conversation_id = conversation.id
        ChannelOperatorAccountStore(old_database).select(
            account_key=ingress.account_key,
            app_secret_identity=whatsapp_app_secret_identity(APP_SECRET, IDENTITY_SECRET),
            principal=OperatorPrincipal(),
        )
        ChannelBindingStore(old_database).bind_thread(
            ChannelThreadBindingSpec(
                channel="whatsapp",
                account_key=ingress.account_key,
                sender_key=ingress.sender_key,
                thread_key=ingress.thread_key,
            ),
            conversation_id=conversation_id,
            principal=OperatorPrincipal(),
        )
        with old_database.transaction() as session:
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
        replies = WhatsAppReplyIntentStore(old_database)
        prepared, created = replies.prepare(turn_id, principal=OperatorPrincipal())
        assert created
        replies.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())
        WhatsAppSendAttemptStore(old_database).claim(prepared.intent_fingerprint, **_arguments())

        old_database.migrate()
        old_database.check_schema()
        with old_database.read() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0055_whatsapp_send_response"
            )
            assert session.get(ConversationTurn, turn_id).assistant_content == (
                "Preserved exact reply"
            )
            assert session.scalar(text("SELECT COUNT(*) FROM whatsapp_send_attempts")) == 1
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
            assert session.scalar(text("PRAGMA integrity_check")) == "ok"
        observed = _observe(old_database, prepared.intent_fingerprint)
        assert observed.outcome == "success_shape"
        with old_database.engine.connect() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="Destructive downgrade is disabled"):
                command.downgrade(config, "-1")
        old_database.check_schema()
    finally:
        old_database.close()


def test_replace_with_duplicate_outgoing_key_cannot_erase_prior_receipt(settings, database):
    import asyncio

    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.main import create_app
    from app.whatsapp_reply_intents import WhatsAppReplyIntentStore
    from tests.test_whatsapp_verified_ingress import Provider, body, setup, signed

    with TestClient(create_app(settings, provider=Provider())) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw_first = body()
        raw_second = body(message_id="wamid.HBgLNTQ5MTExAB==")
        first_turn = asyncio.run(adapter.admit(raw_first, signed(raw_first)))
        second_turn = asyncio.run(adapter.admit(raw_second, signed(raw_second)))
        replies = WhatsAppReplyIntentStore(database)
        first, _ = replies.prepare(first_turn.id, principal=OperatorPrincipal())
        second, _ = replies.prepare(second_turn.id, principal=OperatorPrincipal())
        for prepared in (first, second):
            replies.decide(prepared.intent_fingerprint, "approve", principal=OperatorPrincipal())
        attempts = WhatsAppSendAttemptStore(database)
        attempts.claim(first.intent_fingerprint, **_arguments())
        second_arguments = _arguments()
        second_arguments["inbound_message_id"] = "wamid.HBgLNTQ5MTExAB=="
        attempts.claim(second.intent_fingerprint, **second_arguments)
    first_observation = _observe(database, first.intent_fingerprint)
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO whatsapp_send_responses "
                    "(intent_fingerprint,observed_at,http_status,outcome,response_message_key,"
                    "delivery_confirmed,transport_authority) VALUES "
                    "(:fingerprint,CURRENT_TIMESTAMP,200,'success_shape',:message_key,0,0)"
                ),
                {
                    "fingerprint": second.intent_fingerprint,
                    "message_key": first_observation.response_message_key,
                },
            )
    with database.read() as session:
        assert session.get(WhatsAppSendResponseRecord, first.intent_fingerprint) is not None
        assert session.get(WhatsAppSendResponseRecord, second.intent_fingerprint) is None


def test_wrong_identity_secret_cannot_record_response(settings, database):
    fingerprint, _ = _claimed(settings, database)
    with pytest.raises(PermissionError, match="recipient_mismatch"):
        WhatsAppSendResponseStore(database).observe(
            fingerprint,
            principal=OperatorPrincipal(),
            identity_secret=bytes(range(96, 128)),
            recipient_wa_id="15550001111",
            http_status=200,
            response_body=_success_body(),
        )
    with database.read() as session:
        assert session.get(WhatsAppSendResponseRecord, fingerprint) is None


def test_http_200_without_body_is_not_a_message_link(settings, database):
    fingerprint, _ = _claimed(settings, database)
    observed = WhatsAppSendResponseStore(database).observe(
        fingerprint,
        principal=OperatorPrincipal(),
        identity_secret=_arguments()["identity_secret"],
        recipient_wa_id="15550001111",
        http_status=200,
        response_body=None,
    )
    assert observed.outcome == "invalid_success_shape"
    assert observed.response_message_key is None
