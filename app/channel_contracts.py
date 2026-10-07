"""Transport-neutral, non-authoritative text channel contracts for v0.16.

Provider authentication, account binding, delivery and Mind routing are NOT
implemented here. Merely constructing a valid envelope grants no authority.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CHANNEL_INGRESS_SCHEMA = "zelo-channel-text-ingress-v1"
CHANNEL_DELIVERY_SCHEMA = "zelo-channel-delivery-intent-v1"
CHANNEL_TEXT_BYTES = 16_384

ChannelName = Literal["browser_text", "whatsapp", "email"]
IdentifierPurpose = Literal["account", "sender", "recipient", "thread", "message"]
OpaqueKey = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TurnId = Annotated[str, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]


def derive_opaque_channel_key(
    secret: bytes,
    *,
    channel: ChannelName,
    purpose: IdentifierPurpose,
    raw_identifier: str,
) -> str:
    """Keyed pseudonym for a verified provider adapter; never persist raw identifiers.

    The caller is responsible for provider authentication and protecting the
    per-account secret. This function does not create provider authority.
    """

    if not isinstance(secret, bytes) or len(secret) < 32 or not any(secret):
        raise ValueError("channel_identity_secret_invalid")
    if channel not in ("browser_text", "whatsapp", "email"):
        raise ValueError("channel_identity_channel_invalid")
    if purpose not in ("account", "sender", "recipient", "thread", "message"):
        raise ValueError("channel_identity_purpose_invalid")
    if (
        not isinstance(raw_identifier, str)
        or not raw_identifier
        or raw_identifier != raw_identifier.strip()
        or "\x00" in raw_identifier
        or len(raw_identifier.encode("utf-8")) > 512
    ):
        raise ValueError("channel_identity_raw_invalid")
    material = json.dumps(
        {
            "schema": "zelo-channel-opaque-identity-v1",
            "channel": channel,
            "purpose": purpose,
            "raw": raw_identifier,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def _fingerprint(domain: str, value: dict[str, str]) -> str:
    encoded = json.dumps(
        {"domain": domain, "identity": value},
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_text(text: str, digest: str) -> None:
    if "\x00" in text or len(text.encode("utf-8")) > CHANNEL_TEXT_BYTES:
        raise ValueError("channel_text_invalid")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise ValueError("channel_text_digest_mismatch")


class ChannelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ChannelTextIngress(ChannelContract):
    """Untrusted provider facts; a separate verified adapter must admit them."""

    schema_version: Literal["zelo-channel-text-ingress-v1"] = CHANNEL_INGRESS_SCHEMA
    channel: ChannelName
    account_key: OpaqueKey
    sender_key: OpaqueKey
    thread_key: OpaqueKey
    message_key: OpaqueKey
    text: str = Field(min_length=1, max_length=CHANNEL_TEXT_BYTES)
    text_sha256: OpaqueKey
    provider_received_at: datetime
    payload_kind: Literal["text"] = "text"
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def _validate_envelope(self):
        _validate_text(self.text, self.text_sha256)
        if (
            self.provider_received_at.tzinfo is None
            or self.provider_received_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("channel_timestamp_not_utc")
        return self

    def conversation_fingerprint(self) -> str:
        """Scoped provider-thread identity, not a Zelo conversation binding."""

        return _fingerprint(
            "channel-conversation-v1",
            {
                "channel": self.channel,
                "account": self.account_key,
                "sender": self.sender_key,
                "thread": self.thread_key,
            },
        )

    def message_fingerprint(self) -> str:
        """Stable across exact provider retries; independent of mutable text."""

        return _fingerprint(
            "channel-message-v1",
            {
                "conversation": self.conversation_fingerprint(),
                "message": self.message_key,
            },
        )

    def idempotency_key(self) -> str:
        """Reserved for a future Core-verified ingress adapter, never public HTTP."""

        return f"channel:{self.message_fingerprint()}"


class ChannelDeliveryIntent(ChannelContract):
    """Prepared reply metadata only; no transport invocation or delivery claim."""

    schema_version: Literal["zelo-channel-delivery-intent-v1"] = CHANNEL_DELIVERY_SCHEMA
    channel: ChannelName
    account_key: OpaqueKey
    recipient_key: OpaqueKey
    thread_key: OpaqueKey
    conversation_turn_id: TurnId
    text: str = Field(min_length=1, max_length=CHANNEL_TEXT_BYTES)
    text_sha256: OpaqueKey
    state: Literal["prepared"] = "prepared"
    transport_authority: Literal[False] = False

    @model_validator(mode="after")
    def _validate_intent(self):
        _validate_text(self.text, self.text_sha256)
        return self

    def delivery_fingerprint(self) -> str:
        return _fingerprint(
            "channel-delivery-v1",
            {
                "channel": self.channel,
                "account": self.account_key,
                "recipient": self.recipient_key,
                "thread": self.thread_key,
                "turn": self.conversation_turn_id,
            },
        )
