"""v0.16 inert channel contracts: no transport or Mind admission authority."""

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.channel_contracts import (
    CHANNEL_TEXT_BYTES,
    ChannelDeliveryIntent,
    ChannelTextIngress,
    derive_opaque_channel_key,
)

TURN = "00000000-0000-4000-8000-000000000011"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingress(**overrides) -> ChannelTextIngress:
    message = "Hello Zelo"
    payload = {
        "channel": "whatsapp",
        "account_key": "a" * 64,
        "sender_key": "b" * 64,
        "thread_key": "c" * 64,
        "message_key": "d" * 64,
        "text": message,
        "text_sha256": digest(message),
        "provider_received_at": datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return ChannelTextIngress.model_validate(payload)


def delivery(**overrides) -> ChannelDeliveryIntent:
    message = "A Zelo reply"
    payload = {
        "channel": "whatsapp",
        "account_key": "a" * 64,
        "recipient_key": "b" * 64,
        "thread_key": "c" * 64,
        "conversation_turn_id": TURN,
        "text": message,
        "text_sha256": digest(message),
    }
    payload.update(overrides)
    return ChannelDeliveryIntent.model_validate(payload)


def test_deterministic_message_and_thread_identity_without_plain_identifiers():
    left = ingress()
    duplicate = ingress()
    assert left == duplicate
    assert left.conversation_fingerprint() == duplicate.conversation_fingerprint()
    assert left.message_fingerprint() == duplicate.message_fingerprint()
    assert left.idempotency_key() == "channel:" + left.message_fingerprint()
    assert len(left.idempotency_key()) == 72
    assert left.execution_authority is False
    assert "whatsapp" not in left.idempotency_key()
    assert "Hello" not in left.idempotency_key()


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", "email"),
        ("account_key", "e" * 64),
        ("sender_key", "e" * 64),
        ("thread_key", "e" * 64),
    ],
)
def test_thread_scope_changes_with_channel_account_sender_or_thread(field, value):
    before = ingress()
    after = ingress(**{field: value})
    assert before.conversation_fingerprint() != after.conversation_fingerprint()
    assert before.message_fingerprint() != after.message_fingerprint()


def test_same_provider_message_key_cannot_be_replayed_into_another_thread():
    before = ingress()
    after = ingress(thread_key="f" * 64)
    assert before.message_key == after.message_key
    assert before.idempotency_key() != after.idempotency_key()


def test_same_message_key_with_changed_text_preserves_dedupe_identity_for_collision_check():
    before = ingress()
    after = ingress(text="Edited provider body", text_sha256=digest("Edited provider body"))
    assert before.idempotency_key() == after.idempotency_key()
    assert before.text_sha256 != after.text_sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", "phone_call"),
        ("account_key", "+971555555555"),
        ("sender_key", "someone@example.com"),
        ("message_key", "not-an-opaque-key"),
        ("text_sha256", "0" * 64),
        ("payload_kind", "attachment"),
        ("execution_authority", True),
    ],
)
def test_ingress_rejects_unqualified_transport_identity_or_authority(field, value):
    with pytest.raises(ValidationError):
        ingress(**{field: value})


def test_ingress_rejects_naive_and_non_utc_provider_times():
    with pytest.raises(ValidationError, match="channel_timestamp_not_utc"):
        ingress(provider_received_at=datetime(2026, 10, 7, 12, 0))
    with pytest.raises(ValidationError, match="channel_timestamp_not_utc"):
        ingress(
            provider_received_at=datetime(2026, 10, 7, 16, 0, tzinfo=timezone(timedelta(hours=4)))
        )


def test_ingress_rejects_unicode_byte_overflow_and_null():
    text = "🔐" * (CHANNEL_TEXT_BYTES // 4 + 1)
    assert len(text) < CHANNEL_TEXT_BYTES
    with pytest.raises(ValidationError, match="channel_text_invalid"):
        ingress(text=text, text_sha256=digest(text))
    with pytest.raises(ValidationError, match="channel_text_invalid"):
        ingress(text="zero\x00byte", text_sha256=digest("zero\x00byte"))


def test_ingress_rejects_extra_fields_and_forged_digest():
    with pytest.raises(ValidationError):
        ingress(operator_token="not-allowed")
    with pytest.raises(ValidationError, match="channel_text_digest_mismatch"):
        ingress(text="Different text")


def test_delivery_is_prepared_only_and_has_no_send_authority():
    planned = delivery()
    assert planned.state == "prepared"
    assert planned.transport_authority is False
    assert len(planned.delivery_fingerprint()) == 64
    assert planned.delivery_fingerprint() == delivery().delivery_fingerprint()


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", "email"),
        ("account_key", "e" * 64),
        ("recipient_key", "e" * 64),
        ("thread_key", "e" * 64),
        ("conversation_turn_id", "00000000-0000-4000-8000-000000000012"),
    ],
)
def test_delivery_identity_is_bound_to_exact_channel_destination_and_turn(field, value):
    assert delivery().delivery_fingerprint() != delivery(**{field: value}).delivery_fingerprint()


def test_delivery_changed_body_keeps_identity_for_later_ledger_collision_refusal():
    a = delivery()
    b = delivery(text="Edited reply", text_sha256=digest("Edited reply"))
    assert a.delivery_fingerprint() == b.delivery_fingerprint()
    assert a.text_sha256 != b.text_sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "sent"),
        ("transport_authority", True),
        ("recipient_key", "external-person@example.com"),
        ("conversation_turn_id", "invalid"),
        ("text_sha256", "1" * 64),
    ],
)
def test_delivery_does_not_claim_sends_or_accept_untrusted_identifiers(field, value):
    with pytest.raises(ValidationError):
        delivery(**{field: value})


def test_delivery_rejects_extra_provider_fields_and_unsupported_attachment():
    with pytest.raises(ValidationError):
        delivery(provider_api_key="should-not-be-in-contract")
    with pytest.raises(ValidationError):
        delivery(attachment_url="https://example.invalid/attachment")


@pytest.mark.parametrize("purpose", ["account", "sender", "recipient", "thread", "message"])
def test_channel_identity_is_a_deterministic_keyed_pseudonym(purpose):
    key = bytes(range(32))
    value = derive_opaque_channel_key(
        key,
        channel="whatsapp",
        purpose=purpose,
        raw_identifier="+971555000000",
    )
    assert len(value) == 64
    assert value == derive_opaque_channel_key(
        key,
        channel="whatsapp",
        purpose=purpose,
        raw_identifier="+971555000000",
    )
    assert "+971" not in value
    assert "@" not in value


def test_channel_pseudonyms_are_separated_by_purpose_provider_and_secret():
    identifier = "same-identifier"
    first = derive_opaque_channel_key(
        bytes(range(32)),
        channel="whatsapp",
        purpose="sender",
        raw_identifier=identifier,
    )
    other_provider = derive_opaque_channel_key(
        bytes(range(32)),
        channel="email",
        purpose="sender",
        raw_identifier=identifier,
    )
    other_role = derive_opaque_channel_key(
        bytes(range(32)),
        channel="whatsapp",
        purpose="recipient",
        raw_identifier=identifier,
    )
    rotated_key = derive_opaque_channel_key(
        bytes(range(1, 33)),
        channel="whatsapp",
        purpose="sender",
        raw_identifier=identifier,
    )
    assert len({first, other_provider, other_role, rotated_key}) == 4


@pytest.mark.parametrize("secret", [b"", b"x" * 31, b"\x00" * 32, "not bytes"])
def test_channel_pseudonym_requires_a_nontrivial_secret(secret):
    with pytest.raises(ValueError, match="channel_identity_secret_invalid"):
        derive_opaque_channel_key(
            secret, channel="whatsapp", purpose="sender", raw_identifier="private-id"
        )


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"channel": "sms"}, "channel_identity_channel_invalid"),
        ({"purpose": "operator"}, "channel_identity_purpose_invalid"),
        ({"raw_identifier": ""}, "channel_identity_raw_invalid"),
        ({"raw_identifier": " leading"}, "channel_identity_raw_invalid"),
        ({"raw_identifier": "trailing "}, "channel_identity_raw_invalid"),
        ({"raw_identifier": "embedded\x00nul"}, "channel_identity_raw_invalid"),
        ({"raw_identifier": "x" * 513}, "channel_identity_raw_invalid"),
    ],
)
def test_channel_pseudonym_refuses_bad_namespace_or_identifier(overrides, error):
    params = {
        "channel": "whatsapp",
        "purpose": "sender",
        "raw_identifier": "private-id",
    }
    params.update(overrides)
    with pytest.raises(ValueError, match=error):
        derive_opaque_channel_key(bytes(range(32)), **params)
