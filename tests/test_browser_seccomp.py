"""Offline integrity proofs only; no Docker, browser, network or host policy."""

import ast
import hashlib
import io
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from qualification import browser_seccomp as seccomp

VENDOR = Path(__file__).resolve().parents[1] / "qualification/vendor/moby-profiles"
PROFILE_HASH = "6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6"
LICENSE_HASH = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
COMMIT = "2ceae35d351c156cb5a8efc0fdc4a08cf94569d8"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(profile):
    return json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()


@pytest.fixture(autouse=True)
def supported_host(monkeypatch):
    monkeypatch.setattr(seccomp.platform, "machine", lambda: "x86_64")


def test_provenance():
    assert seccomp.UPSTREAM_COMMIT == COMMIT
    assert seccomp.UPSTREAM_SHA256 == PROFILE_HASH
    assert digest((VENDOR / "default.json").read_bytes()) == PROFILE_HASH
    assert digest((VENDOR / "LICENSE").read_bytes()) == LICENSE_HASH
    notice = (VENDOR / "NOTICE.md").read_text()
    for value in (COMMIT, PROFILE_HASH, LICENSE_HASH, "Apache License 2.0"):
        assert value in notice


def test_exact_baseline_and_five_rules():
    baseline = json.loads((VENDOR / "default.json").read_bytes())
    candidate = json.loads(seccomp.build_profile().profile_bytes)
    appended = candidate["syscalls"][len(baseline["syscalls"]) :]
    candidate["syscalls"] = candidate["syscalls"][: len(baseline["syscalls"])]
    assert candidate == baseline
    assert candidate["defaultAction"] == "SCMP_ACT_ERRNO"
    assert candidate["defaultErrnoRet"] == 1
    expected = []
    for name, mask in (
        ("clone", 268435473),
        ("clone", 536870929),
        ("clone", 1879048209),
        ("unshare", 268435456),
    ):
        expected.append(
            {
                "names": [name],
                "action": "SCMP_ACT_ALLOW",
                "args": [{"index": 0, "value": mask, "op": "SCMP_CMP_EQ"}],
                "includes": {"arches": ["amd64"]},
            }
        )
    expected.append(
        {"names": ["chroot"], "action": "SCMP_ACT_ALLOW", "includes": {"arches": ["amd64"]}}
    )
    assert appended == expected


def test_deterministic_frozen_and_independent():
    first = seccomp.build_profile()
    second = seccomp.build_profile()
    assert first == second
    assert first is not second
    assert type(first.profile_bytes) is bytes
    assert first.digest == digest(first.profile_bytes)
    seccomp.validate_profile(first.profile_bytes, first.digest)
    with pytest.raises(FrozenInstanceError):
        first.digest = "0" * 64
    decoded = json.loads(first.profile_bytes)
    decoded["syscalls"].clear()
    assert seccomp.build_profile() == first
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.SeccompProfile(encode(decoded), digest(encode(decoded)))


@pytest.mark.parametrize("architecture", ["x86_64", "amd64"])
def test_supported_aliases(monkeypatch, architecture):
    monkeypatch.setattr(seccomp.platform, "machine", lambda: architecture)
    seccomp.build_profile()


@pytest.mark.parametrize("architecture", ["aarch64", "arm64", "i386", "x86", "", "AMD64"])
def test_unsupported_architecture(monkeypatch, architecture):
    valid = seccomp.build_profile()
    monkeypatch.setattr(seccomp.platform, "machine", lambda: architecture)
    with pytest.raises(seccomp.SeccompProfileError, match="unsupported_architecture"):
        seccomp.build_profile()
    with pytest.raises(seccomp.SeccompProfileError, match="unsupported_architecture"):
        seccomp.validate_profile(valid.profile_bytes, valid.digest)


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"\xff", b"x" * 65537])
def test_bad_source_and_drift_after_build(tmp_path, monkeypatch, raw):
    valid = seccomp.build_profile()
    source = tmp_path / "default.json"
    source.write_bytes(raw)
    monkeypatch.setattr(seccomp, "UPSTREAM_PATH", source)
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.build_profile()
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.validate_profile(valid.profile_bytes, valid.digest)


@pytest.mark.parametrize("kind", ["missing", "directory", "permission"])
def test_source_read_errors(tmp_path, monkeypatch, kind):
    source = tmp_path / "source"
    if kind == "directory":
        source.mkdir()
    if kind == "permission":

        def denied(*args, **kwargs):
            raise PermissionError("fixture")

        monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(seccomp, "UPSTREAM_PATH", source)
    with pytest.raises(seccomp.SeccompProfileError, match="upstream_read_failed"):
        seccomp.build_profile()


def test_bounded_read_before_hash_or_parse(monkeypatch):
    class Source(io.BytesIO):
        def read(self, size=-1):
            assert size == seccomp.MAX_PROFILE_BYTES + 1
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *a, **kw: Source(b"x" * 100000))
    with pytest.raises(seccomp.SeccompProfileError, match="upstream_oversized"):
        seccomp.build_profile()


def test_hash_before_parse(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"{invalid")
    monkeypatch.setattr(seccomp, "UPSTREAM_PATH", source)

    def must_not_parse(*args, **kwargs):
        pytest.fail("unauthenticated bytes reached JSON parser")

    monkeypatch.setattr(seccomp.json, "loads", must_not_parse)
    with pytest.raises(seccomp.SeccompProfileError, match="upstream_digest_mismatch"):
        seccomp.build_profile()


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"null", b"[]", b"{}", b'{"syscalls":[]}'])
def test_invalid_authenticated_source_seam(tmp_path, monkeypatch, raw):
    source = tmp_path / "source"
    source.write_bytes(raw)
    monkeypatch.setattr(seccomp, "UPSTREAM_PATH", source)
    monkeypatch.setattr(seccomp, "UPSTREAM_SHA256", digest(raw))
    with pytest.raises(seccomp.SeccompProfileError, match="invalid_upstream"):
        seccomp.build_profile()


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff", b"null", b"x" * 65537])
def test_invalid_output_even_with_matching_digest(raw):
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.validate_profile(raw, digest(raw))


@pytest.mark.parametrize("bad_digest", ["", "0" * 64, "G" * 64, None, 42])
def test_bad_output_digest(bad_digest):
    raw = seccomp.build_profile().profile_bytes
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.validate_profile(raw, bad_digest)


def test_reject_mutable_output_and_reserialization():
    valid = seccomp.build_profile()
    for raw in (bytearray(valid.profile_bytes), memoryview(valid.profile_bytes)):
        with pytest.raises(seccomp.SeccompProfileError):
            seccomp.validate_profile(raw, valid.digest)
    for raw in (valid.profile_bytes[:-1], valid.profile_bytes + b"\n"):
        with pytest.raises(seccomp.SeccompProfileError):
            seccomp.validate_profile(raw, digest(raw))


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("mutation", ["missing", "extra", "architecture", "action", "names"])
def test_reject_rule_mutations(index, mutation):
    profile = json.loads(seccomp.build_profile().profile_bytes)
    rule = profile["syscalls"][-5 + index]
    if mutation == "missing":
        profile["syscalls"].pop(-5 + index)
    elif mutation == "extra":
        profile["syscalls"].append(rule.copy())
    elif mutation == "architecture":
        rule.pop("includes")
    elif mutation == "action":
        rule["action"] = "SCMP_ACT_LOG"
    else:
        rule["names"].append("setns")
    raw = encode(profile)
    with pytest.raises(seccomp.SeccompProfileError, match="profile_policy_mismatch"):
        seccomp.validate_profile(raw, digest(raw))


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("mutation", ["no_args", "mask", "operator", "index", "value_two"])
def test_reject_broadened_argument_rules(index, mutation):
    profile = json.loads(seccomp.build_profile().profile_bytes)
    rule = profile["syscalls"][-5 + index]
    if mutation == "no_args":
        rule.pop("args")
    else:
        argument = rule["args"][0]
        key, value = {
            "mask": ("value", argument["value"] | 0x20000),
            "operator": ("op", "SCMP_CMP_MASKED_EQ"),
            "index": ("index", 1),
            "value_two": ("valueTwo", 0),
        }[mutation]
        argument[key] = value
    raw = encode(profile)
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.validate_profile(raw, digest(raw))


@pytest.mark.parametrize("name", ["clone", "unshare", "setns", "clone3", "mount"])
def test_reject_new_unconditional_calls(name):
    profile = json.loads(seccomp.build_profile().profile_bytes)
    profile["syscalls"].append({"names": [name], "action": "SCMP_ACT_ALLOW"})
    raw = encode(profile)
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.validate_profile(raw, digest(raw))


@pytest.mark.parametrize("mutation", ["default", "baseline", "arch_map", "capability"])
def test_reject_baseline_and_setting_changes(mutation):
    profile = json.loads(seccomp.build_profile().profile_bytes)
    if mutation == "default":
        profile["defaultAction"] = "SCMP_ACT_ALLOW"
    elif mutation == "baseline":
        profile["syscalls"].pop(0)
    elif mutation == "arch_map":
        profile["archMap"] = []
    else:
        profile["capabilities"] = ["SYS_ADMIN"]
    raw = encode(profile)
    with pytest.raises(seccomp.SeccompProfileError):
        seccomp.validate_profile(raw, digest(raw))


def test_no_production_import():
    # Static source proof avoids importing/starting the production application.
    root = Path(__file__).resolve().parents[1]
    for source in (root / "app").rglob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("qualification") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("qualification")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "qualification.browser_seccomp" not in node.value


def test_generated_output_bound(tmp_path, monkeypatch):
    # Trusted-pin seam reaches the output ceiling without a large source read.
    raw = encode({"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [], "padding": "x" * 65000})
    assert len(raw) <= seccomp.MAX_PROFILE_BYTES
    source = tmp_path / "source"
    source.write_bytes(raw)
    monkeypatch.setattr(seccomp, "UPSTREAM_PATH", source)
    monkeypatch.setattr(seccomp, "UPSTREAM_SHA256", digest(raw))
    with pytest.raises(seccomp.SeccompProfileError, match="profile_oversized"):
        seccomp.build_profile()
