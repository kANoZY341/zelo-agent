"""Non-network verification of raw-body Meta webhook HMAC signatures."""

import hashlib
import hmac
import json

import pytest

from app.whatsapp_signature import (
    MAX_WHATSAPP_WEBHOOK_BYTES,
    verify_whatsapp_webhook_signature,
)

SECRET = bytes(range(32))
BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def signature(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify(body=BODY, header=None, secret=SECRET):
    return verify_whatsapp_webhook_signature(
        body, signature(body) if header is None else header, app_secret=secret
    )


def test_valid_raw_signed_webhook():
    assert verify()
    assert verify(b'{"entry":[{"changes":[]}]}')
    assert verify(b'{"body":"\xc3\xa9\xf0\x9f\x94\x90"}')


def test_changed_body_rejected_even_when_json_is_semantically_equal():
    original = b'{"object":"whatsapp_business_account", "entry":[]}'
    resaved = json.dumps(json.loads(original), separators=(",", ":")).encode()
    assert resaved != original
    assert verify(original, signature(original))
    assert not verify(resaved, signature(original))


@pytest.mark.parametrize(
    "body",
    [BODY + b" ", BODY + b"\n", BODY.replace(b"entry", b"ENTRY"), BODY[:-1]],
)
def test_byte_modifications_refused(body):
    assert not verify(body, signature(BODY))


def test_wrong_secret_rejected():
    assert not verify(BODY, signature(BODY, bytes(range(1, 33))))


@pytest.mark.parametrize(
    "header",
    [
        "",
        "sha256=",
        "sha1=" + "a" * 40,
        "SHA256=" + "a" * 64,
        "sha256=" + "a" * 63,
        "sha256=" + "A" * 64,
        "sha256=" + "g" * 64,
        "sha256=" + "a" * 64 + "\n",
        " sha256=" + "a" * 64,
        "sha256=" + "a" * 64 + ", sha256=" + "b" * 64,
    ],
)
def test_missing_or_noncanonical_signature_header_fails_closed(header):
    assert not verify(BODY, header)


@pytest.mark.parametrize("body", [b"", None, "not raw bytes", memoryview(b"data")])
def test_invalid_or_absent_body_fails_closed(body):
    assert not verify(body, signature(BODY))


def test_oversized_body_is_rejected_before_hashing():
    body = b"x" * (MAX_WHATSAPP_WEBHOOK_BYTES + 1)
    assert not verify(body, signature(body))


def test_exact_body_byte_limit_still_supported():
    body = b"x" * MAX_WHATSAPP_WEBHOOK_BYTES
    assert verify(body, signature(body))


@pytest.mark.parametrize("secret", [b"", b"x" * 15, b"\x00" * 32, b"x" * 513, "not bytes"])
def test_missing_weak_or_wrong_type_server_secret_refused(secret):
    with pytest.raises(ValueError, match="whatsapp_signature_secret_invalid"):
        verify(BODY, signature(BODY), secret)


def test_no_implicit_provider_sender_or_execution_authentication():
    assert type(verify(BODY, signature(BODY))) is bool
    assert not verify(BODY, "sha256=" + "0" * 64)
