"""Fixed synthetic Debian archives for native-reader qualification only."""

import hashlib

from qualification import media_tar_isolation as ustar

STDIN_BYTES = 262144
STDOUT_BYTES = 65536
STDERR_BYTES = 16384
CASES = ("positive", "bad_version", "wrong_order", "truncated")


class Refused(ValueError):
    """Fixed synthetic-fixture/result refusal."""


def _require(condition, code):
    if not condition:
        raise Refused("reader_fixture_" + code)


def _ar_member(name: str, payload: bytes) -> bytes:
    import re

    _require(
        type(name) is str
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", name) is not None
        and type(payload) is bytes,
        "member",
    )
    header = b"".join(
        (
            name.encode("ascii").ljust(16, b" "),
            b"0".ljust(12, b" "),
            b"0".ljust(6, b" "),
            b"0".ljust(6, b" "),
            b"100644".ljust(8, b" "),
            str(len(payload)).encode("ascii").ljust(10, b" "),
            b"`\n",
        )
    )
    _require(len(header) == 60, "header")
    return header + payload + (b"\n" if len(payload) & 1 else b"")


def expected_tar() -> bytes:
    raw = ustar.fixture("positive")
    _require(type(raw) is bytes and 0 < len(raw) <= STDOUT_BYTES, "tar")
    return raw


def fixture(case: str) -> bytes:
    _require(type(case) is str and case in CASES, "case")
    version = b"9.0\n" if case == "bad_version" else b"2.0\n"
    members = (
        _ar_member("debian-binary", version),
        _ar_member("control.tar", b""),
        _ar_member("data.tar", expected_tar()),
    )
    if case == "wrong_order":
        members = (members[0], members[2], members[1])
    raw = b"!<arch>\n" + b"".join(members)
    if case == "truncated":
        raw = raw[:-1]
    _require(0 < len(raw) <= STDIN_BYTES, "deb")
    return raw


def identify(payload: bytes) -> str:
    _require(type(payload) is bytes and len(payload) <= STDIN_BYTES, "deb")
    found = [case for case in CASES if payload == fixture(case)]
    _require(len(found) == 1, "identity")
    return found[0]


def validate_result(
    case,
    stdout,
    stderr,
    exit_code,
    cleanup_confirmed,
    *,
    saturated=False,
    cancelled=False,
):
    _require(type(case) is str and case in CASES, "case")
    _require(cleanup_confirmed is True, "cleanup")
    _require(saturated is False and cancelled is False, "terminal")
    _require(type(stdout) is bytes and len(stdout) <= STDOUT_BYTES, "stdout")
    _require(type(stderr) is bytes and len(stderr) <= STDERR_BYTES, "stderr")
    payload = fixture(case)
    value = {
        "schema": "zelo-media-native-reader-synthetic-result-v1",
        "case": case,
        "input_sha256": hashlib.sha256(payload).hexdigest(),
        "input_bytes": len(payload),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stdout_bytes": len(stdout),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stderr_bytes": len(stderr),
        "cleanup_confirmed": True,
        "runtime_authority": False,
        "package_provenance_verified": False,
        "image_decoder_authority": False,
    }
    if case == "positive":
        _require(type(exit_code) is int and exit_code == 0, "exit")
        _require(stderr == b"", "positive_stderr")
        expected = expected_tar()
        _require(stdout == expected, "positive_stdout")
        value.update(
            status="streamed-synthetic-tar",
            tar_sha256=hashlib.sha256(expected).hexdigest(),
            tar_bytes=len(expected),
        )
    else:
        _require(type(exit_code) is int and exit_code != 0, "negative_exit")
        value["status"] = "refused-synthetic-input"
    return value
