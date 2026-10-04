"""Exact vendored Moby default seccomp profile for the Media reader gate."""

import hashlib
import json

from qualification import browser_seccomp

MAX_BYTES = browser_seccomp.MAX_PROFILE_BYTES
SHA256 = browser_seccomp.UPSTREAM_SHA256


class Refused(ValueError):
    """Fixed source/profile refusal."""


def profile_bytes() -> bytes:
    try:
        with browser_seccomp.UPSTREAM_PATH.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
    except OSError as exc:
        raise Refused("reader_seccomp_read") from exc
    if not 0 < len(raw) <= MAX_BYTES:
        raise Refused("reader_seccomp_size")
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise Refused("reader_seccomp_pin")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Refused("reader_seccomp_json") from exc
    if (
        type(value) is not dict
        or value.get("defaultAction") != "SCMP_ACT_ERRNO"
        or type(value.get("syscalls")) is not list
    ):
        raise Refused("reader_seccomp_shape")
    return raw


def profile_digest() -> str:
    return hashlib.sha256(profile_bytes()).hexdigest()
