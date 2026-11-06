"""Private shape observation for one WhatsApp send response; no transport."""

import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.auth import OperatorPrincipal
from app.channel_contracts import derive_opaque_channel_key
from app.models import (
    WhatsAppReplyIntentRecord,
    WhatsAppSendAttemptRecord,
    WhatsAppSendResponseRecord,
)
from app.storage import SessionStore
from app.whatsapp_reply_intents import _operator
from app.whatsapp_text_events import _MESSAGE_ID, _SENDER_ID

ResponseOutcome = Literal[
    "success_shape", "invalid_success_shape", "http_non_success", "no_response"
]
_MAX_RESPONSE_BYTES = 8192


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("whatsapp_send_response_duplicate_key")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("whatsapp_send_response_invalid")


def _classify(
    http_status: int | None, response_body: bytes | None, recipient_wa_id: str
) -> tuple[ResponseOutcome, str | None]:
    if http_status is None:
        if response_body not in (None, b""):
            raise ValueError("whatsapp_send_response_status_body_invalid")
        return "no_response", None
    if type(http_status) is not int or not 100 <= http_status <= 599:
        raise ValueError("whatsapp_send_response_http_status_invalid")
    if response_body is not None and type(response_body) is not bytes:
        raise ValueError("whatsapp_send_response_body_invalid")
    if http_status != 200:
        return "http_non_success", None
    if response_body is None or not response_body or len(response_body) > _MAX_RESPONSE_BYTES:
        return "invalid_success_shape", None
    try:
        root = json.loads(
            response_body.decode("utf-8"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return "invalid_success_shape", None
    if type(root) is not dict or root.get("messaging_product") != "whatsapp":
        return "invalid_success_shape", None
    if "error" in root:
        return "invalid_success_shape", None
    contacts = root.get("contacts")
    messages = root.get("messages")
    if (
        type(contacts) is not list
        or len(contacts) != 1
        or type(messages) is not list
        or len(messages) != 1
    ):
        return "invalid_success_shape", None
    contact, message = contacts[0], messages[0]
    if type(contact) is not dict or type(message) is not dict:
        return "invalid_success_shape", None
    if (
        "error" in contact
        or "error" in message
        or contact.get("wa_id") != recipient_wa_id
        or contact.get("input") != recipient_wa_id
    ):
        return "invalid_success_shape", None
    message_status = message.get("message_status")
    if "message_status" in message and message_status not in (
        "accepted",
        "held_for_quality_assessment",
    ):
        return "invalid_success_shape", None
    wamid = message.get("id")
    if type(wamid) is not str or _MESSAGE_ID.fullmatch(wamid) is None:
        return "invalid_success_shape", None
    return "success_shape", wamid


@dataclass(frozen=True)
class WhatsAppSendResponseObservation:
    intent_fingerprint: str
    observed_at: datetime
    outcome: ResponseOutcome
    http_status: int | None
    response_message_key: str | None
    delivery_confirmed: bool = False
    transport_authority: bool = False


class WhatsAppSendResponseStore:
    """Record one supplied response shape after a durable possible-send claim."""

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    def observe(
        self,
        intent_fingerprint: str,
        *,
        principal: OperatorPrincipal,
        identity_secret: bytes,
        recipient_wa_id: str,
        http_status: int | None,
        response_body: bytes | None,
    ) -> WhatsAppSendResponseObservation:
        _operator(principal)
        if (
            type(intent_fingerprint) is not str
            or len(intent_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in intent_fingerprint)
        ):
            raise ValueError("whatsapp_send_response_intent_invalid")
        if type(recipient_wa_id) is not str or _SENDER_ID.fullmatch(recipient_wa_id) is None:
            raise ValueError("whatsapp_send_response_recipient_invalid")
        recipient_key = derive_opaque_channel_key(
            identity_secret,
            channel="whatsapp",
            purpose="sender",
            raw_identifier=recipient_wa_id,
        )
        with self.database.transaction() as session:
            attempt = session.get(WhatsAppSendAttemptRecord, intent_fingerprint)
            if attempt is None:
                raise LookupError("whatsapp_send_response_attempt_required")
            intent = session.get(WhatsAppReplyIntentRecord, intent_fingerprint)
            if intent is None or not hmac.compare_digest(intent.recipient_key, recipient_key):
                raise PermissionError("whatsapp_send_response_recipient_mismatch")
            if session.get(WhatsAppSendResponseRecord, intent_fingerprint) is not None:
                raise PermissionError("whatsapp_send_response_already_observed")
            outcome, raw_wamid = _classify(http_status, response_body, recipient_wa_id)
            message_key = (
                derive_opaque_channel_key(
                    identity_secret,
                    channel="whatsapp",
                    purpose="message",
                    raw_identifier=raw_wamid,
                )
                if raw_wamid is not None
                else None
            )
            record = WhatsAppSendResponseRecord(
                intent_fingerprint=intent_fingerprint,
                http_status=http_status,
                outcome=outcome,
                response_message_key=message_key,
                delivery_confirmed=False,
                transport_authority=False,
            )
            session.add(record)
            session.flush()
            return WhatsAppSendResponseObservation(
                intent_fingerprint=record.intent_fingerprint,
                observed_at=record.observed_at,
                outcome=outcome,
                http_status=http_status,
                response_message_key=message_key,
            )
