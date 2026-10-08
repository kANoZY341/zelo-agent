"""Offline WhatsApp signed text -> opaque metadata, never account admission."""

import copy
import hashlib
import hmac
import json
import re

import pytest

from app.channel_contracts import ChannelDeliveryIntent, ChannelTextIngress
from app.whatsapp_opaque_ingress import prepare_unverified_whatsapp_ingress

APP_SECRET = bytes(range(32))
IDENTITY_SECRET = bytes(range(32, 64))
RAW_IDENTIFIER_STRINGS = ("123456789012", "987654321", "15550001111")


def payload():
    return {
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
                                    "id": "wamid.HBgLNTQ5MTExAA==",
                                    "timestamp": "1760000000",
                                    "type": "text",
                                    "text": {"body": "Private message: hello 👋"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def project(value=None, *, app_secret=APP_SECRET, identity_secret=IDENTITY_SECRET, signature=None):
    body = json.dumps(
        payload() if value is None else value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    header = "sha256=" + hmac.new(app_secret, body, hashlib.sha256).hexdigest()
    return prepare_unverified_whatsapp_ingress(
        body,
        header if signature is None else signature,
        app_secret=app_secret,
        identity_secret=identity_secret,
    )


def test_deterministic_opaque_projection_preserves_inert_contract():
    first = project()
    second = project()
    assert isinstance(first, ChannelTextIngress)
    assert first == second
    assert first.channel == "whatsapp"
    assert first.execution_authority is False
    assert first.payload_kind == "text"
    assert first.text == "Private message: hello 👋"
    assert first.text_sha256 == hashlib.sha256(first.text.encode("utf-8")).hexdigest()
    assert first.idempotency_key().startswith("channel:")
    assert re.fullmatch(r"channel:[0-9a-f]{64}", first.idempotency_key())
    for field in ("account_key", "sender_key", "thread_key", "message_key"):
        assert re.fullmatch(r"[0-9a-f]{64}", getattr(first, field))
    exposed_metadata = "|".join(
        (first.account_key, first.sender_key, first.thread_key, first.message_key)
    )
    for raw in RAW_IDENTIFIER_STRINGS:
        assert raw not in exposed_metadata
    assert "Private message" not in repr(first)


@pytest.mark.parametrize(
    "field,raw_value,change",
    [
        ("waba", "123456789013", ("account", "thread", "conversation", "message")),
        ("phone", "987654322", ("account", "thread", "conversation", "message")),
        ("sender", "15550001112", ("sender", "thread", "conversation", "message")),
        ("message", "wamid.HBgLNTQ5MTExBB==", ("message",)),
    ],
)
def test_provider_dimensions_and_message_id_are_scoped(field, raw_value, change):
    baseline = project()
    value = payload()
    entry = value["entry"][0]
    data = entry["changes"][0]["value"]
    message = data["messages"][0]
    if field == "waba":
        entry["id"] = raw_value
    elif field == "phone":
        data["metadata"]["phone_number_id"] = raw_value
    elif field == "sender":
        message["from"] = raw_value
    else:
        message["id"] = raw_value
    changed = project(value)
    for key, attribute in [
        ("account", "account_key"),
        ("sender", "sender_key"),
        ("thread", "thread_key"),
        ("message", "message_key"),
    ]:
        # Message-specific HMAC key is independent of other dimensions, while
        # the full message fingerprint is scoped by the account/thread.
        if key == "message" and field != "message":
            assert getattr(changed, attribute) == getattr(baseline, attribute)
        elif key in change:
            assert getattr(changed, attribute) != getattr(baseline, attribute)
        else:
            assert getattr(changed, attribute) == getattr(baseline, attribute)
    if field in ("waba", "phone", "sender"):
        assert changed.conversation_fingerprint() != baseline.conversation_fingerprint()
    else:
        assert changed.conversation_fingerprint() == baseline.conversation_fingerprint()
    assert changed.message_fingerprint() != baseline.message_fingerprint()


def test_edit_same_message_id_keeps_identity_but_digest_changes():
    value = payload()
    before = project(value)
    changed = copy.deepcopy(value)
    changed["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = (
        "Edited private text"
    )
    after = project(changed)
    assert after.account_key == before.account_key
    assert after.message_fingerprint() == before.message_fingerprint()
    assert after.text_sha256 != before.text_sha256
    assert after.text != before.text


def test_rotating_identity_secret_changes_opaque_keys_not_provider_facts():
    baseline = project()
    changed = project(identity_secret=bytes(range(64, 96)))
    assert baseline.account_key != changed.account_key
    assert baseline.sender_key != changed.sender_key
    assert baseline.thread_key != changed.thread_key
    assert baseline.message_key != changed.message_key
    assert baseline.text_sha256 == changed.text_sha256


def test_status_only_signed_payload_has_no_ingress():
    value = payload()
    data = value["entry"][0]["changes"][0]["value"]
    del data["messages"]
    data["statuses"] = [{"id": "wamid.HBgLNTQ5MTExAA==", "status": "read"}]
    assert project(value) is None


@pytest.mark.parametrize(
    "identity_secret",
    [b"", b"x" * 15, b"\x00" * 32, b"x" * 513, "not bytes", None],
)
def test_identity_secret_must_be_separate_and_strong(identity_secret):
    with pytest.raises(ValueError, match="whatsapp_identity_secret_invalid"):
        project(identity_secret=identity_secret)


def test_provider_app_secret_cannot_be_reused_for_opaque_identity():
    with pytest.raises(ValueError, match="whatsapp_identity_secret_reuse"):
        project(identity_secret=APP_SECRET)


def test_wrong_raw_signature_cannot_be_projected():
    with pytest.raises(ValueError, match="whatsapp_text_signature_invalid"):
        project(signature="sha256=" + "0" * 64)


def test_invalid_provider_app_secret_cannot_be_projected():
    with pytest.raises(ValueError, match="whatsapp_signature_secret_invalid"):
        project(app_secret=b"x" * 15)


def test_prepared_reply_contract_repr_does_not_expose_text():
    projected = project()
    reply = ChannelDeliveryIntent(
        channel="whatsapp",
        account_key=projected.account_key,
        recipient_key=projected.sender_key,
        thread_key=projected.thread_key,
        conversation_turn_id="01234567-89ab-4def-8abc-0123456789ab",
        text="Private outbound text",
        text_sha256=hashlib.sha256(b"Private outbound text").hexdigest(),
    )
    assert "Private outbound text" not in repr(reply)
    assert reply.state == "prepared"
    assert reply.transport_authority is False


def test_untrusted_projection_cannot_admit_task_or_provider_authority():
    ingress = project()
    assert ingress.execution_authority is False
    fields = ChannelTextIngress.model_fields
    assert fields["execution_authority"].default is False
    assert "provider_authenticated" not in fields
    assert "transport_authority" not in fields
    assert "provider_account_approved" not in fields
    assert "delivery_authority" not in fields
