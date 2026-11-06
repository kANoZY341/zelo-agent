"""Offline Core WhatsApp text admission through real Mind persistence."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import OperatorPrincipal
from app.channel_binding_store import ChannelBindingStore
from app.channel_contracts import ChannelThreadBindingSpec
from app.channel_operator_accounts import ChannelOperatorAccountStore
from app.gateway import ModelResult
from app.main import create_app
from app.models import ConversationTurn, VerifiedWhatsAppIngressRecord
from app.whatsapp_opaque_ingress import prepare_unverified_whatsapp_ingress
from app.whatsapp_verified_ingress import (
    VerifiedWhatsAppTextAdmission,
    whatsapp_app_secret_identity,
)

APP_SECRET = bytes(range(32))
OTHER_APP_SECRET = bytes(range(32, 64))
IDENTITY_SECRET = bytes(range(64, 96))
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
OPERATOR = OperatorPrincipal()


class Provider:
    def __init__(self, action=None):
        self.action = action or {"type": "reply", "content": "Private reply"}
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def generate(self, request):
        self.calls += 1
        if self.block:
            self.entered.set()
            await self.release.wait()
        return ModelResult(
            text=json.dumps(self.action),
            provider="fake",
            model_id=request.model_id,
        )

    async def models(self):
        return ["qwen3:4b-instruct"]


def body(*, text="Hello Zelo", message_id="wamid.HBgLNTQ5MTExAA==", timestamp=NOW):
    value = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789012",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "987654321"},
                            "messages": [
                                {
                                    "from": "15550001111",
                                    "id": message_id,
                                    "timestamp": str(int(timestamp.timestamp())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def signed(raw, secret=APP_SECRET):
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()


def setup(database, mind, *, select_account=True, bind_thread=True, secret=APP_SECRET):
    raw = body()
    ingress = prepare_unverified_whatsapp_ingress(
        raw, signed(raw), app_secret=APP_SECRET, identity_secret=IDENTITY_SECRET
    )
    conversation = mind.create()
    if select_account:
        ChannelOperatorAccountStore(database).select(
            account_key=ingress.account_key,
            app_secret_identity=whatsapp_app_secret_identity(secret, IDENTITY_SECRET),
            principal=OPERATOR,
        )
    if bind_thread:
        ChannelBindingStore(database).bind_thread(
            ChannelThreadBindingSpec(
                channel="whatsapp",
                account_key=ingress.account_key,
                sender_key=ingress.sender_key,
                thread_key=ingress.thread_key,
            ),
            conversation_id=conversation.id,
            principal=OPERATOR,
        )
    adapter = VerifiedWhatsAppTextAdmission(
        mind,
        app_secret=APP_SECRET,
        identity_secret=IDENTITY_SECRET,
        clock=lambda: NOW,
    )
    return adapter, conversation.id, ingress


def counts(database):
    with database.read() as session:
        return (
            session.scalar(select(func.count()).select_from(VerifiedWhatsAppIngressRecord)),
            session.scalar(select(func.count()).select_from(ConversationTurn)),
        )


def test_verified_text_admits_one_reply_and_exact_retry(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, conversation_id, ingress = setup(database, client.app.state.mind)
        raw = body()
        first = asyncio.run(adapter.admit(raw, signed(raw)))
        second = asyncio.run(adapter.admit(raw, signed(raw)))
        assert first.id == second.id
        assert first.user_content == "Hello Zelo"
        assert first.assistant_content == "Private reply"
        assert first.core_task is None
        assert provider.calls == 1
        assert counts(database) == (1, 1)
        with database.read() as session:
            record = session.get(VerifiedWhatsAppIngressRecord, ingress.message_fingerprint())
            assert record.conversation_turn_id == first.id
            assert record.text_sha256 == ingress.text_sha256
            assert record.app_secret_identity == whatsapp_app_secret_identity(
                APP_SECRET, IDENTITY_SECRET
            )


@pytest.mark.parametrize("missing", ["account", "binding", "secret"])
def test_missing_authority_refuses_before_model_or_mind_write(settings, database, missing):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, _ = setup(
            database,
            client.app.state.mind,
            select_account=missing != "account",
            bind_thread=missing != "binding",
            secret=OTHER_APP_SECRET if missing == "secret" else APP_SECRET,
        )
        raw = body()
        with pytest.raises((ValueError, PermissionError), match="whatsapp_"):
            asyncio.run(adapter.admit(raw, signed(raw)))
        assert provider.calls == 0
        assert counts(database) == (0, 0)


def test_invalid_signature_stale_and_future_refuse_without_model(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        with pytest.raises(ValueError, match="signature"):
            asyncio.run(adapter.admit(body(), signed(body(), OTHER_APP_SECRET)))
        for timestamp in (NOW - timedelta(hours=25), NOW + timedelta(minutes=6)):
            raw = body(timestamp=timestamp)
            with pytest.raises(ValueError, match="freshness"):
                asyncio.run(adapter.admit(raw, signed(raw)))
        assert provider.calls == 0
        assert counts(database) == (0, 0)


def test_edit_same_message_id_refuses_replay_without_second_mind_turn(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        asyncio.run(adapter.admit(raw, signed(raw)))
        edited = body(text="Changed after first delivery")
        with pytest.raises(ValueError, match="replay_conflict"):
            asyncio.run(adapter.admit(edited, signed(edited)))
        assert provider.calls == 1
        assert counts(database) == (1, 1)


def test_model_task_action_refused_without_task_or_turn(settings, database):
    provider = Provider(
        {
            "type": "core_task",
            "content": "I will run this.",
            "task": {"kind": "echo", "payload": {"text": "danger"}},
        }
    )
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        raw = body()
        with pytest.raises(PermissionError, match="whatsapp_reply_only"):
            asyncio.run(adapter.admit(raw, signed(raw)))
        assert counts(database) == (0, 0)


def test_revocation_during_model_generation_prevents_commit(settings, database):
    provider = Provider()
    provider.block = True
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()

        async def race():
            pending = asyncio.create_task(adapter.admit(raw, signed(raw)))
            await asyncio.wait_for(provider.entered.wait(), 5)
            ChannelOperatorAccountStore(database).revoke(
                account_key=ingress.account_key, principal=OPERATOR
            )
            provider.release.set()
            with pytest.raises((ValueError, PermissionError), match="whatsapp_account"):
                await pending

        asyncio.run(race())
        assert counts(database) == (0, 0)


def test_existing_unverified_observation_cannot_be_silently_upgraded(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        ChannelBindingStore(database).observe_unverified(ingress, principal=OPERATOR)
        raw = body()
        with pytest.raises(ValueError, match="unverified_observation_conflict"):
            asyncio.run(adapter.admit(raw, signed(raw)))
        assert provider.calls == 0
        assert counts(database) == (0, 0)


def test_preexisting_unlinked_channel_turn_cannot_be_adopted(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        mind = client.app.state.mind
        adapter, conversation_id, ingress = setup(database, mind)
        asyncio.run(mind.send_turn(conversation_id, ingress.text, ingress.idempotency_key()))
        assert counts(database) == (0, 1)
        raw = body()
        with pytest.raises(ValueError, match="missing_verified_ledger"):
            asyncio.run(adapter.admit(raw, signed(raw)))
        assert provider.calls == 1
        assert counts(database) == (0, 1)


def test_revoked_account_cannot_replay_existing_reply(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        asyncio.run(adapter.admit(raw, signed(raw)))
        ChannelOperatorAccountStore(database).revoke(
            account_key=ingress.account_key, principal=OPERATOR
        )
        with pytest.raises(PermissionError, match="whatsapp_account_revoked"):
            asyncio.run(adapter.admit(raw, signed(raw)))
        assert provider.calls == 1
        assert counts(database) == (1, 1)


def test_verified_ledger_refuses_replace_and_update(settings, database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        asyncio.run(adapter.admit(raw, signed(raw)))
        with database.read() as session:
            sql = session.execute(
                text("SELECT sql FROM sqlite_master WHERE name='whatsapp_verified_ingress'")
            ).scalar_one()
            assert "WITHOUT ROWID" in sql.upper()
        for statement in (
            "UPDATE whatsapp_verified_ingress SET text_sha256=:digest "
            "WHERE message_fingerprint=:fingerprint",
            "DELETE FROM whatsapp_verified_ingress WHERE message_fingerprint=:fingerprint",
            "INSERT OR REPLACE INTO whatsapp_verified_ingress "
            "SELECT * FROM whatsapp_verified_ingress WHERE message_fingerprint=:fingerprint",
        ):
            with pytest.raises(IntegrityError, match="append-only"):
                with database.transaction() as session:
                    session.execute(
                        text(statement),
                        {"fingerprint": ingress.message_fingerprint(), "digest": "a" * 64},
                    )
        assert counts(database) == (1, 1)


def test_unverified_observation_cannot_follow_verified_admission(settings, database):
    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        first = asyncio.run(adapter.admit(raw, signed(raw)))
        with pytest.raises(ValueError, match="channel_message_already_verified"):
            ChannelBindingStore(database).observe_unverified(ingress, principal=OPERATOR)
        assert asyncio.run(adapter.admit(raw, signed(raw))).id == first.id
        assert counts(database) == (1, 1)


def test_database_refuses_cross_ledger_observation_after_verified(settings, database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, ingress = setup(database, client.app.state.mind)
        raw = body()
        asyncio.run(adapter.admit(raw, signed(raw)))
        with pytest.raises(IntegrityError, match="already verified"):
            with database.transaction() as session:
                session.execute(
                    text(
                        "INSERT INTO channel_ingress_observations "
                        "(message_fingerprint,thread_fingerprint,message_key,text_sha256,"
                        "provider_received_at,observed_at,operator_id,state,mind_executed,"
                        "delivery_authority) "
                        "VALUES (:fingerprint,:thread,:message,:digest,:received,:received,"
                        "'ahmad','unverified',0,0)"
                    ),
                    {
                        "fingerprint": ingress.message_fingerprint(),
                        "thread": ingress.conversation_fingerprint(),
                        "message": ingress.message_key,
                        "digest": ingress.text_sha256,
                        "received": ingress.provider_received_at,
                    },
                )


def test_adapter_credentials_cannot_drift_from_selected_secret(settings, database):
    from dataclasses import FrozenInstanceError

    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        adapter, _, _ = setup(database, client.app.state.mind)
        with pytest.raises(FrozenInstanceError):
            adapter.app_secret = OTHER_APP_SECRET
        raw = body()
        with pytest.raises(ValueError, match="signature"):
            asyncio.run(adapter.admit(raw, signed(raw, OTHER_APP_SECRET)))
        assert provider.calls == 0
        assert counts(database) == (0, 0)


def test_database_refuses_verified_insert_after_unverified_observation(settings, database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    provider = Provider()
    with TestClient(create_app(settings, provider=provider)) as client:
        mind = client.app.state.mind
        _, conversation_id, ingress = setup(database, mind)
        ChannelBindingStore(database).observe_unverified(ingress, principal=OPERATOR)
        turn = asyncio.run(mind.send_turn(conversation_id, ingress.text, ingress.idempotency_key()))
        with pytest.raises(IntegrityError, match="already observed unverified"):
            with database.transaction() as session:
                session.execute(
                    text(
                        "INSERT INTO whatsapp_verified_ingress "
                        "(message_fingerprint,thread_fingerprint,message_key,text_sha256,"
                        "provider_received_at,admitted_at,app_secret_identity,"
                        "conversation_turn_id) "
                        "VALUES (:fingerprint,:thread,:message,:digest,:received,:admitted,"
                        ":secret,:turn)"
                    ),
                    {
                        "fingerprint": ingress.message_fingerprint(),
                        "thread": ingress.conversation_fingerprint(),
                        "message": ingress.message_key,
                        "digest": ingress.text_sha256,
                        "received": ingress.provider_received_at,
                        "admitted": NOW,
                        "secret": whatsapp_app_secret_identity(APP_SECRET, IDENTITY_SECRET),
                        "turn": turn.id,
                    },
                )
