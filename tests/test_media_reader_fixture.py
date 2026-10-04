"""Offline fixed native-reader fixture and seccomp-source tests."""

import hashlib
import json

import pytest

from qualification import browser_seccomp
from qualification import media_reader_fixture as fixture
from qualification import media_reader_seccomp as seccomp


def parse_ar(raw: bytes):
    """Independent test-only decoder for the hand-built synthetic archive."""
    assert raw.startswith(b"!<arch>\n")
    pos = 8
    rows = []
    while pos < len(raw):
        header = raw[pos : pos + 60]
        assert len(header) == 60 and header[58:60] == b"`\n"
        name = header[:16].decode("ascii").rstrip(" /")
        size = int(header[48:58].decode("ascii").strip())
        start = pos + 60
        payload = raw[start : start + size]
        assert len(payload) == size
        rows.append((name, payload))
        pos = start + size + (size & 1)
    assert pos == len(raw)
    return rows


def test_seccomp_is_exact_vendored_moby_baseline():
    raw = seccomp.profile_bytes()
    assert hashlib.sha256(raw).hexdigest() == browser_seccomp.UPSTREAM_SHA256 == seccomp.SHA256
    value = json.loads(raw)
    assert value["defaultAction"] == "SCMP_ACT_ERRNO"
    assert isinstance(value["syscalls"], list)
    assert raw != browser_seccomp.build_profile().profile_bytes


@pytest.mark.parametrize("fault", ["digest", "oversize", "shape"])
def test_seccomp_refuses_source_drift(tmp_path, monkeypatch, fault):
    original = browser_seccomp.UPSTREAM_PATH.read_bytes()
    target = tmp_path / "default.json"
    if fault == "digest":
        target.write_bytes(original + b" ")
    elif fault == "oversize":
        target.write_bytes(b"x" * (seccomp.MAX_BYTES + 1))
    else:
        target.write_bytes(b"[]")
        monkeypatch.setattr(
            browser_seccomp,
            "UPSTREAM_SHA256",
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(seccomp, "SHA256", hashlib.sha256(target.read_bytes()).hexdigest())
    monkeypatch.setattr(browser_seccomp, "UPSTREAM_PATH", target)
    with pytest.raises(seccomp.Refused):
        seccomp.profile_bytes()


@pytest.mark.parametrize("case", fixture.CASES)
def test_fixed_fixture_identity_and_bounds(case):
    raw = fixture.fixture(case)
    assert 0 < len(raw) <= fixture.STDIN_BYTES
    assert fixture.identify(raw) == case


def test_all_fixture_hashes_are_distinct():
    values = {hashlib.sha256(fixture.fixture(case)).hexdigest() for case in fixture.CASES}
    assert len(values) == len(fixture.CASES)


def test_positive_fixture_exact_ar_layout_and_tar_bytes():
    rows = parse_ar(fixture.fixture("positive"))
    assert [name for name, _ in rows] == ["debian-binary", "control.tar", "data.tar"]
    assert rows[0][1] == b"2.0\n"
    assert rows[1][1] == b""
    assert rows[2][1] == fixture.expected_tar()
    assert len(rows[2][1]) <= fixture.STDOUT_BYTES


def test_bad_version_changes_only_required_version_payload():
    good = parse_ar(fixture.fixture("positive"))
    bad = parse_ar(fixture.fixture("bad_version"))
    assert [name for name, _ in bad] == [name for name, _ in good]
    assert good[0][1] == b"2.0\n" and bad[0][1] == b"9.0\n"
    assert good[1:] == bad[1:]


def test_wrong_order_is_visible_without_running_reader():
    rows = parse_ar(fixture.fixture("wrong_order"))
    assert [name for name, _ in rows] == ["debian-binary", "data.tar", "control.tar"]


def test_truncated_fixture_is_not_complete_ar():
    with pytest.raises(AssertionError):
        parse_ar(fixture.fixture("truncated"))


@pytest.mark.parametrize("payload", [b"", b"wrong", b"x" * (fixture.STDIN_BYTES + 1)])
def test_unknown_fixture_refused(payload):
    with pytest.raises(fixture.Refused):
        fixture.identify(payload)


def test_positive_result_requires_exact_tar_empty_stderr_exit_zero_and_cleanup():
    value = fixture.validate_result("positive", fixture.expected_tar(), b"", 0, True)
    assert value["status"] == "streamed-synthetic-tar"
    assert value["runtime_authority"] is False
    assert value["package_provenance_verified"] is False


@pytest.mark.parametrize(
    "stdout,stderr,code,cleanup,saturated,cancelled,refusal",
    [
        (b"", b"", 0, True, False, False, "positive_stdout"),
        (fixture.expected_tar(), b"x", 0, True, False, False, "positive_stderr"),
        (fixture.expected_tar(), b"", 1, True, False, False, "exit"),
        (fixture.expected_tar(), b"", 0, False, False, False, "cleanup"),
        (fixture.expected_tar(), b"", 0, True, True, False, "terminal"),
        (fixture.expected_tar(), b"", 0, True, False, True, "terminal"),
    ],
)
def test_positive_result_failure_paths(
    stdout, stderr, code, cleanup, saturated, cancelled, refusal
):
    with pytest.raises(fixture.Refused, match=f"reader_fixture_{refusal}"):
        fixture.validate_result(
            "positive",
            stdout,
            stderr,
            code,
            cleanup,
            saturated=saturated,
            cancelled=cancelled,
        )


@pytest.mark.parametrize("case", ["bad_version", "wrong_order", "truncated"])
def test_negative_result_accepts_only_bounded_nonzero_refusal(case):
    value = fixture.validate_result(case, b"partial", b"bounded diagnostic", 2, True)
    assert value["status"] == "refused-synthetic-input"
    assert value["runtime_authority"] is False
    with pytest.raises(fixture.Refused, match="negative_exit"):
        fixture.validate_result(case, b"", b"", 0, True)


def test_result_never_promotes_negative_stderr_to_authority():
    value = fixture.validate_result("bad_version", b"", b"peer text", 2, True)
    assert "peer text" not in json.dumps(value)
    assert value["stderr_bytes"] == len(b"peer text")
    assert value["stderr_sha256"] == hashlib.sha256(b"peer text").hexdigest()
