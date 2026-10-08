"""Offline signed WhatsApp text -> inert private channel contract.

This projects HMAC-verified *provider claims* into scoped opaque identifiers.
Neither a valid Meta signature nor an opaque key proves account ownership;
no caller may use this helper as authorization to admit a Mind turn.
"""

import hashlib
import hmac

from app.channel_contracts import ChannelTextIngress, IdentifierPurpose, derive_opaque_channel_key
from app.whatsapp_text_events import extract_signed_whatsapp_text_event

_CHANNEL = "whatsapp"


def _identity_key(secret: bytes, purpose: IdentifierPurpose, raw: str) -> str:
    return derive_opaque_channel_key(secret, channel=_CHANNEL, purpose=purpose, raw_identifier=raw)


def prepare_unverified_whatsapp_ingress(
    raw_body: bytes,
    signature_header: str,
    *,
    app_secret: bytes,
    identity_secret: bytes,
) -> ChannelTextIngress | None:
    """Prepare private, unverified replay facts for a *future* verified adapter.

    Two independent secrets are mandatory. The Meta App Secret authenticates
    raw payload integrity; the Core-owned identity secret pseudonymizes account,
    sender, thread and message claims. Both remain in memory only.

    The returned ChannelTextIngress is NOT bound to any approved WhatsApp
    account, does NOT validate freshness, does NOT write a ledger record, and
    gives no authority to send, execute, or admit a conversation turn.
    """

    if (
        type(identity_secret) is not bytes
        or not 32 <= len(identity_secret) <= 512
        or not any(identity_secret)
    ):
        raise ValueError("whatsapp_identity_secret_invalid")
    if type(app_secret) is bytes and hmac.compare_digest(app_secret, identity_secret):
        raise ValueError("whatsapp_identity_secret_reuse")

    event = extract_signed_whatsapp_text_event(raw_body, signature_header, app_secret=app_secret)
    if event is None:
        return None

    # Both provider account dimensions are necessary: one WABA can have
    # multiple receiving phones, and message IDs must be scoped to that phone
    # and its sender. Provider IDs are ASCII digits validated by the parser.
    provider_account = f"{event.waba_id}:{event.phone_number_id}"
    provider_thread = f"{provider_account}:{event.sender_wa_id}"
    return ChannelTextIngress(
        channel=_CHANNEL,
        account_key=_identity_key(identity_secret, "account", provider_account),
        sender_key=_identity_key(identity_secret, "sender", event.sender_wa_id),
        thread_key=_identity_key(identity_secret, "thread", provider_thread),
        message_key=_identity_key(identity_secret, "message", event.message_id),
        text=event.text,
        text_sha256=hashlib.sha256(event.text.encode("utf-8")).hexdigest(),
        provider_received_at=event.provider_timestamp,
    )
