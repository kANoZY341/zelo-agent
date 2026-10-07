"""Pure signed WhatsApp text-shape qualification; no provider/account/Mind authority."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.channel_contracts import CHANNEL_TEXT_BYTES
from app.whatsapp_signature import verify_whatsapp_webhook_signature

_ACCOUNT_ID = re.compile(r"[0-9]{1,32}\Z", flags=re.ASCII)
_SENDER_ID = re.compile(r"[0-9]{6,20}\Z", flags=re.ASCII)
_MESSAGE_ID = re.compile(r"wamid\.[A-Za-z0-9_+=/.-]{1,245}\Z", flags=re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{9,12}\Z", flags=re.ASCII)
_MIN_TIMESTAMP = 946684800  # 2000-01-01 UTC, not a replay-window policy
_MAX_TIMESTAMP = 4102444800  # 2100-01-01 UTC, not a freshness assertion


@dataclass(frozen=True, repr=False)
class UntrustedWhatsAppTextEvent:
    """Signed provider *claims*; explicit Core account binding still required.

    Raw identifiers/text are ephemeral and must never be logged or persisted by
    this parser. Do not treat this object as an admitted Mind turn.
    """

    waba_id: str
    phone_number_id: str
    sender_wa_id: str
    message_id: str
    text: str
    provider_timestamp: datetime
    provider_account_approved: bool = False
    execution_authority: bool = False
    delivery_authority: bool = False


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("whatsapp_text_payload_unsupported")
    return value


def _only_item(value: Any) -> Any:
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError("whatsapp_text_payload_unsupported")
    return value[0]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("whatsapp_text_payload_duplicate_key")
        output[key] = value
    return output


def _reject_json_constant(_: str) -> None:
    raise ValueError("whatsapp_text_payload_invalid")


def _provider_id(value: Any, rule: re.Pattern[str]) -> str:
    if type(value) is not str or rule.fullmatch(value) is None:
        raise ValueError("whatsapp_text_payload_unsupported")
    return value


def _text(value: Any) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError("whatsapp_text_payload_unsupported")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("whatsapp_text_payload_unsupported") from None
    if len(encoded) > CHANNEL_TEXT_BYTES:
        raise ValueError("whatsapp_text_payload_unsupported")
    return value


def _utc_claim(value: Any) -> datetime:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError("whatsapp_text_payload_unsupported")
    timestamp = int(value)
    if not _MIN_TIMESTAMP <= timestamp < _MAX_TIMESTAMP:
        raise ValueError("whatsapp_text_payload_unsupported")
    return datetime.fromtimestamp(timestamp, tz=UTC)


def extract_signed_whatsapp_text_event(
    raw_body: bytes,
    signature_header: str,
    *,
    app_secret: bytes,
) -> UntrustedWhatsAppTextEvent | None:
    """Verify HMAC then extract *one* text event, without admitting it to Core.

    For this first bounded version, a body must contain exactly one entry,
    change and (for text) message. Proper status-only events yield None. Media,
    mixed statuses/messages, batches, malformed or unknown message kinds fail
    closed rather than dropping content or silently selecting the first item.

    No caller-controlled flag can disable signature verification. The Meta
    timestamp is an untrusted provider claim, NOT independent freshness proof.
    """

    if not verify_whatsapp_webhook_signature(raw_body, signature_header, app_secret=app_secret):
        raise ValueError("whatsapp_text_signature_invalid")
    try:
        root = _object(
            json.loads(
                raw_body.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("whatsapp_text_payload_invalid") from None

    if root.get("object") != "whatsapp_business_account":
        raise ValueError("whatsapp_text_payload_unsupported")
    entry = _object(_only_item(root.get("entry")))
    waba_id = _provider_id(entry.get("id"), _ACCOUNT_ID)
    change = _object(_only_item(entry.get("changes")))
    if change.get("field") != "messages":
        raise ValueError("whatsapp_text_payload_unsupported")
    value = _object(change.get("value"))
    if value.get("messaging_product") != "whatsapp":
        raise ValueError("whatsapp_text_payload_unsupported")
    metadata = _object(value.get("metadata"))
    phone_id = _provider_id(metadata.get("phone_number_id"), _ACCOUNT_ID)

    messages = value.get("messages")
    statuses = value.get("statuses")
    if statuses is not None and (not isinstance(statuses, list) or not statuses):
        raise ValueError("whatsapp_text_payload_unsupported")
    if messages is None:
        if statuses is not None:
            return None  # Signed delivery receipt, not an inbound text turn.
        raise ValueError("whatsapp_text_payload_unsupported")
    if statuses is not None or value.get("errors"):
        raise ValueError("whatsapp_text_payload_unsupported")
    message = _object(_only_item(messages))
    # A type=text claim with any simultaneously populated non-text payload
    # must not be silently downgraded into an apparently ordinary text turn.
    other_message_kinds = {
        "audio",
        "video",
        "image",
        "document",
        "sticker",
        "interactive",
        "location",
        "contacts",
        "order",
        "reaction",
        "button",
        "system",
        "errors",
    }
    if message.get("type") != "text" or any(kind in message for kind in other_message_kinds):
        raise ValueError("whatsapp_text_payload_unsupported")
    sender = _provider_id(message.get("from"), _SENDER_ID)
    message_id = _provider_id(message.get("id"), _MESSAGE_ID)
    provider_timestamp = _utc_claim(message.get("timestamp"))
    text = _text(_object(message.get("text")).get("body"))

    contacts = value.get("contacts")
    if contacts is not None:
        contact = _object(_only_item(contacts))
        if _provider_id(contact.get("wa_id"), _SENDER_ID) != sender:
            raise ValueError("whatsapp_text_payload_unsupported")

    return UntrustedWhatsAppTextEvent(
        waba_id=waba_id,
        phone_number_id=phone_id,
        sender_wa_id=sender,
        message_id=message_id,
        text=text,
        provider_timestamp=provider_timestamp,
    )
