"""Offline Meta WhatsApp webhook signature check; no transport or Mind authority.

This pure check is not a webhook listener, provider account binding, message
replay verifier or proof that a specific sender controls a WhatsApp account.
"""

import hashlib
import hmac

MAX_WHATSAPP_WEBHOOK_BYTES = 1_048_576
_SIGNATURE_PREFIX = "sha256="
_LOWER_HEX = frozenset("0123456789abcdef")


def verify_whatsapp_webhook_signature(
    raw_body: bytes,
    signature_header: str,
    *,
    app_secret: bytes,
) -> bool:
    """Check Meta's SHA256 HMAC of the *exact unmodified* received body.

    The server-owned secret is supplied by a trusted future adapter. Never
    compare signatures against parsed/re-serialized JSON or accept an unsigned
    notification. This returns only signature validity, not account identity,
    source permission, freshness, idempotency or authority to execute/send.
    """

    if not isinstance(app_secret, bytes) or not 16 <= len(app_secret) <= 512 or not any(app_secret):
        raise ValueError("whatsapp_signature_secret_invalid")

    if (
        not isinstance(raw_body, bytes)
        or not 1 <= len(raw_body) <= MAX_WHATSAPP_WEBHOOK_BYTES
        or not isinstance(signature_header, str)
        or len(signature_header) != len(_SIGNATURE_PREFIX) + 64
        or not signature_header.startswith(_SIGNATURE_PREFIX)
    ):
        return False

    digest_hex = signature_header[len(_SIGNATURE_PREFIX) :]
    if any(char not in _LOWER_HEX for char in digest_hex):
        return False

    expected = hmac.new(app_secret, raw_body, hashlib.sha256).digest()
    provided = bytes.fromhex(digest_hex)
    return hmac.compare_digest(expected, provided)
