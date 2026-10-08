"""Strict single signed text event only: no provider permission or Mind effects."""

import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from app.whatsapp_text_events import extract_signed_whatsapp_text_event

SECRET = bytes(range(32))


def payload() -> dict:
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
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": "987654321",
                            },
                            "contacts": [{"wa_id": "15550001111", "profile": {"name": "Person"}}],
                            "messages": [
                                {
                                    "from": "15550001111",
                                    "id": "wamid.HBgLNTQ5MTExAA==",
                                    "timestamp": "1760000000",
                                    "type": "text",
                                    "text": {"body": "Hello Zelo 👋"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def signed(value: dict | bytes, *, secret=SECRET, header=None):
    raw = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if isinstance(value, dict)
        else value
    )
    correct = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return extract_signed_whatsapp_text_event(
        raw, correct if header is None else header, app_secret=secret
    )


def test_valid_signed_text_event_is_untrusted_fact_only():
    event = signed(payload())
    assert event is not None
    assert event.waba_id == "123456789012"
    assert event.phone_number_id == "987654321"
    assert event.sender_wa_id == "15550001111"
    assert event.message_id.startswith("wamid.")
    assert event.provider_timestamp == datetime.fromtimestamp(1760000000, tz=UTC)
    assert event.text == "Hello Zelo 👋"
    assert not event.provider_account_approved
    assert not event.execution_authority
    assert not event.delivery_authority


def test_accidental_event_repr_does_not_expose_private_identifiers_or_text():
    event = signed(payload())
    description = repr(event)
    for private_value in (
        event.waba_id,
        event.phone_number_id,
        event.sender_wa_id,
        event.message_id,
        event.text,
    ):
        assert private_value not in description


def test_contacts_optional_not_a_source_of_authority():
    item = payload()
    del item["entry"][0]["changes"][0]["value"]["contacts"]
    assert signed(item).sender_wa_id == "15550001111"


def test_signed_delivery_status_is_not_text_message():
    item = payload()
    value = item["entry"][0]["changes"][0]["value"]
    del value["messages"]
    value["statuses"] = [{"id": "wamid.HBg", "status": "delivered"}]
    assert signed(item) is None


def test_same_json_data_requires_signature_on_actual_raw_bytes():
    value = payload()
    raw = json.dumps(value, separators=(", ", ": ")).encode("utf-8")
    changed = json.dumps(value, separators=(",", ":")).encode("utf-8")
    sig = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    assert signed(raw, header=sig).text == "Hello Zelo 👋"
    with pytest.raises(ValueError, match="signature_invalid"):
        signed(changed, header=sig)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda x: x.update(object="page"),
        lambda x: x.update(entry=[]),
        lambda x: x["entry"].append(copy.deepcopy(x["entry"][0])),
        lambda x: x["entry"][0].update(id="other"),
        lambda x: x["entry"][0].update(changes=[]),
        lambda x: x["entry"][0]["changes"][0].update(field="template_update"),
        lambda x: x["entry"][0]["changes"][0]["value"].update(messaging_product="other"),
        lambda x: x["entry"][0]["changes"][0]["value"]["metadata"].update(phone_number_id=""),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"].append(
            copy.deepcopy(x["entry"][0]["changes"][0]["value"]["messages"][0])
        ),
        lambda x: x["entry"][0]["changes"][0]["value"].update(statuses=[{"id": "wamid.X"}]),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(type="image"),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(
            id="untrusted-file-name"
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(timestamp="not time"),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(
            timestamp="999999999999"
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(
            **{"from": "not-raw-id"}
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["contacts"][0].update(wa_id="15559999999"),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0]["text"].update(body=""),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0]["text"].update(
            body="🚀" * 5000
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0]["text"].update(
            body="NUL\u0000test"
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(
            errors=[{"code": 123}]
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(
            image={"id": "fake-media"}
        ),
        lambda x: x["entry"][0]["changes"][0]["value"]["messages"][0].update(
            interactive={"type": "button_reply"}
        ),
        lambda x: x["entry"][0]["changes"][0]["value"].update(errors=[{"code": 123}]),
    ],
)
def test_unsupported_or_mixed_signed_envelopes_fail_closed(mutator):
    value = payload()
    mutator(value)
    with pytest.raises(ValueError, match="whatsapp_text_payload_unsupported"):
        signed(value)


@pytest.mark.parametrize(
    "raw",
    [
        b"not JSON",
        b'{"object":"a","object":"b"}',
        b'{"object":"whatsapp_business_account","entry":',
        b'{"entry":NaN}',
        b'{"entry":"\\ud800"}',
        b"\xff\xfe",
    ],
)
def test_malformed_signed_json_rejected(raw):
    with pytest.raises(ValueError, match="whatsapp_text_payload"):
        signed(raw)


def test_unpaired_surrogate_text_rejected():
    value = payload()
    value["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "\ud800"
    raw = json.dumps(value).encode("utf-8")
    with pytest.raises(ValueError, match="whatsapp_text_payload_unsupported"):
        signed(raw)


def test_empty_signed_payload_is_refused_at_signature_body_boundary():
    with pytest.raises(ValueError, match="whatsapp_text_signature_invalid"):
        signed(b"")


def test_signature_rejection_always_precedes_json_parser():
    with pytest.raises(ValueError, match="whatsapp_text_signature_invalid"):
        signed(b"not-json", header="sha256=" + "0" * 64)
