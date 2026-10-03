"""Offline candidate identity/refusal proofs; no native policy installation."""

import hashlib
import io
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from qualification import media_syscall_policy as m

ROOT = Path(__file__).resolve().parents[1]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.fixture
def candidate():
    return m.build_media_syscall_policy()


def test_pinned_unmodified_vendor_and_notices():
    vendor = ROOT / "qualification/vendor/moby-profiles"
    assert digest((vendor / "default.json").read_bytes()) == m.UPSTREAM_SHA256
    assert digest((vendor / "LICENSE").read_bytes()) == m.LICENSE_SHA256
    notice = (vendor / "NOTICE.md").read_text()
    assert m.UPSTREAM_COMMIT in notice and m.UPSTREAM_SHA256 in notice
    assert "Apache License 2.0" in notice


def test_fixed_candidate_identity_and_false_claims(candidate):
    value = json.loads(candidate.policy_bytes)
    assert value["schema"] == candidate.schema == "zelo-media-stdlib-syscall-candidate-v1"
    assert candidate.digest == digest(candidate.policy_bytes)
    assert candidate.digest == "82478b3fed9fe1e6922cd5120477f5c94a610547d60a1f6ab6d5eded5324c55a"
    assert len(candidate.policy_bytes) == 4198
    assert value["architecture_intention"] == "native-x86-64-only"
    assert value["unsupported_abi_intention"] == "kill-whole-process-including-x32"
    assert value["default_action_intention"] == "errno" and value["default_errno_intention"] == 1
    assert value["purpose"] == "trusted-stdlib-scanner-probes-only"
    assert value["bootstrap_order_intention"] == [
        "trusted-single-threaded-bootstrap",
        "filter",
        "package-path-only-after-separate-authorization",
    ]
    assert candidate.instruction_trust == value["instruction_trust"] == "none"
    assert (
        not candidate.enforcement_verified
        and not candidate.decoder_ready
        and not candidate.runtime_authority
    )
    for name in (
        "compiled",
        "enforcement_verified",
        "decoder_ready",
        "aggregate_resources_verified",
        "runtime_authority",
    ):
        assert value[name] is False
    assert len(value["allow_rules"]) == 40
    assert candidate.policy_bytes == encode(value)
    assert 0 < len(candidate.policy_bytes) <= m.MAX_POLICY_BYTES


def test_independent_frozen_values_and_direct_candidate_validation(candidate):
    other = m.build_media_syscall_policy()
    assert other == candidate and other is not candidate
    assert m.MediaSyscallCandidate(candidate.policy_bytes, candidate.digest) == candidate
    for name, value in (
        ("digest", "0" * 64),
        ("policy_bytes", b"{}"),
        ("enforcement_verified", True),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(candidate, name, value)
    decoded = json.loads(candidate.policy_bytes)
    decoded["allow_rules"].clear()
    assert m.build_media_syscall_policy() == candidate
    with pytest.raises(m.MediaSyscallPolicyError, match="identity"):
        m.MediaSyscallCandidate(encode(decoded), digest(encode(decoded)))


@pytest.mark.parametrize(
    "name",
    [
        "schema",
        "instruction_trust",
        "enforcement_verified",
        "decoder_ready",
        "runtime_authority",
        "compiled",
        "aggregate_resources_verified",
        "path",
        "compiler",
        "abi",
        "syscalls",
    ],
)
def test_constructor_fields_cannot_override_intentions(candidate, name):
    with pytest.raises(TypeError):
        m.MediaSyscallCandidate(candidate.policy_bytes, candidate.digest, **{name: True})


@pytest.mark.parametrize(
    "raw", [b"", b"x" * 16385, bytearray(b"{}"), memoryview(b"{}"), "{}", None, True, 1, {}, []]
)
def test_strict_immutable_bytes(candidate, raw):
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_bytes"):
        m.validate_media_syscall_policy(raw, candidate.digest)


@pytest.mark.parametrize(
    "bad", [None, True, 1, b"0" * 64, "", "0" * 63, "0" * 65, "G" * 64, "A" * 64]
)
def test_digest_types_encoding_and_size(candidate, bad):
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_digest"):
        m.validate_media_syscall_policy(candidate.policy_bytes, bad)


def test_hash_mismatch_before_vendor_access(candidate, monkeypatch):
    monkeypatch.setattr(
        m, "_canonical_policy", lambda: pytest.fail("vendor reached before input hash")
    )
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_digest"):
        m.validate_media_syscall_policy(candidate.policy_bytes, "0" * 64)


@pytest.mark.parametrize("index", range(40))
def test_each_removed_rule_refused_despite_matching_new_hash(candidate, index):
    changed = json.loads(candidate.policy_bytes)
    changed["allow_rules"].pop(index)
    raw = encode(changed)
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_identity"):
        m.validate_media_syscall_policy(raw, digest(raw))


@pytest.mark.parametrize(
    "name",
    [
        "fork",
        "clone",
        "clone3",
        "vfork",
        "execve",
        "execveat",
        "setsid",
        "setpgid",
        "socket",
        "socketpair",
        "prctl",
        "seccomp",
        "prlimit64",
        "ioctl",
        "mount",
        "unshare",
        "setns",
        "ptrace",
        "bpf",
        "io_uring_setup",
        "userfaultfd",
        "keyctl",
    ],
)
def test_forbidden_added_rule_refused_even_with_matching_hash(candidate, name):
    changed = json.loads(candidate.policy_bytes)
    assert name not in {row["name"] for row in changed["allow_rules"]}
    changed["allow_rules"].append({"name": name, "rationale": "caller", "arguments": []})
    raw = encode(changed)
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_identity"):
        m.validate_media_syscall_policy(raw, digest(raw))


@pytest.mark.parametrize(
    "kind",
    [
        "default",
        "errno",
        "abi",
        "bootstrap",
        "provenance",
        "duplicate",
        "reorder",
        "whitespace",
        "unknown",
        "compiled",
        "enforced",
        "decoder",
        "aggregate",
        "authority",
    ],
)
def test_policy_semantic_or_serialization_change_refused(candidate, kind):
    changed = json.loads(candidate.policy_bytes)
    if kind == "default":
        changed["default_action_intention"] = "allow"
    elif kind == "errno":
        changed["default_errno_intention"] = True
    elif kind == "abi":
        changed["architecture_intention"] = "all"
    elif kind == "bootstrap":
        changed["bootstrap_order_intention"].reverse()
    elif kind == "provenance":
        changed["vendor"]["sha256"] = "0" * 64
    elif kind == "duplicate":
        changed["allow_rules"].append(changed["allow_rules"][0])
    elif kind == "reorder":
        changed["allow_rules"].reverse()
    elif kind == "unknown":
        changed["extra"] = True
    elif kind in ("compiled", "enforced", "decoder", "aggregate", "authority"):
        changed[
            {
                "compiled": "compiled",
                "enforced": "enforcement_verified",
                "decoder": "decoder_ready",
                "aggregate": "aggregate_resources_verified",
                "authority": "runtime_authority",
            }[kind]
        ] = True
    raw = json.dumps(changed, indent=2).encode() if kind == "whitespace" else encode(changed)
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_identity"):
        m.validate_media_syscall_policy(raw, digest(raw))


def test_duplicate_json_key_not_normalized_or_blessed(candidate):
    raw = candidate.policy_bytes[:-1] + b',"runtime_authority":false}'
    assert json.loads(raw) == json.loads(candidate.policy_bytes)
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_identity"):
        m.validate_media_syscall_policy(raw, digest(raw))


def test_exact_masked_arguments_and_rationales(candidate):
    rules = json.loads(candidate.policy_bytes)["allow_rules"]
    assert [r["name"] for r in rules] == sorted({r["name"] for r in rules})
    assert all(type(r["rationale"]) is str and r["rationale"] for r in rules)
    constrained = {r["name"]: r["arguments"] for r in rules if r["arguments"]}
    assert constrained == {
        "openat": [{"index": 2, "operation": "masked-equal", "mask": 0x400643, "value": 0}],
        "mmap": [{"index": 2, "operation": "masked-equal", "mask": 4, "value": 0}],
        "mprotect": [{"index": 2, "operation": "masked-equal", "mask": 4, "value": 0}],
    }
    # Read-only scanner directory opens retain O_DIRECTORY; write/creation flags fail.
    mask = constrained["openat"][0]["mask"]
    assert (0x10000 | 0x20000 | 0x80000) & mask == 0
    for flag in (1, 2, 0x40, 0x200, 0x400, 0x400000):
        assert flag & mask
    for prot in (0, 1, 2, 3):
        assert prot & 4 == 0
    for prot in (4, 5, 6, 7):
        assert prot & 4 != 0


@pytest.mark.parametrize("name", ["openat", "mmap", "mprotect"])
@pytest.mark.parametrize("kind", ["remove", "index", "mask", "value", "operator", "boolean"])
def test_argument_rule_drift_refused(candidate, name, kind):
    changed = json.loads(candidate.policy_bytes)
    row = next(r for r in changed["allow_rules"] if r["name"] == name)
    if kind == "remove":
        row["arguments"] = []
    else:
        key, val = {
            "index": ("index", 1),
            "mask": ("mask", 0),
            "value": ("value", 4),
            "operator": ("operation", "equal"),
            "boolean": ("value", False),
        }[kind]
        row["arguments"][0][key] = val
    raw = encode(changed)
    with pytest.raises(m.MediaSyscallPolicyError, match="policy_identity"):
        m.validate_media_syscall_policy(raw, digest(raw))


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"x" * 65537])
def test_vendor_drift_refuses_build_and_existing_candidate(candidate, monkeypatch, raw):
    monkeypatch.setattr(Path, "open", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(m.MediaSyscallPolicyError):
        m.build_media_syscall_policy()
    with pytest.raises(m.MediaSyscallPolicyError):
        m.validate_media_syscall_policy(candidate.policy_bytes, candidate.digest)


def test_vendor_read_bound_and_pin_before_parse(monkeypatch):
    calls = []

    class Source(io.BytesIO):
        def read(self, size=-1):
            calls.append(size)
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *a, **k: Source(b"{}"))
    monkeypatch.setattr(
        m.json, "loads", lambda *a, **k: pytest.fail("parser reached before source pin")
    )
    with pytest.raises(m.MediaSyscallPolicyError, match="vendor_digest"):
        m.build_media_syscall_policy()
    assert calls == [m.MAX_VENDOR_BYTES + 1]


@pytest.mark.parametrize("error", [FileNotFoundError, IsADirectoryError, PermissionError])
def test_vendor_read_errors_sanitized(monkeypatch, error):
    def refused(*a, **k):
        raise error("private diagnostic path")

    monkeypatch.setattr(Path, "open", refused)
    with pytest.raises(m.MediaSyscallPolicyError) as result:
        m.build_media_syscall_policy()
    assert str(result.value) == "media_syscall_vendor_read"


@pytest.mark.parametrize(
    "field,value",
    [
        ("args", []),
        ("includes", {}),
        ("excludes", {}),
        ("args", [{"index": 0}]),
        ("includes", {"arches": ["amd64"]}),
        ("extra", True),
    ],
)
def test_conditional_fields_never_flattened(field, value):
    profile = {
        "defaultAction": "SCMP_ACT_ERRNO",
        "defaultErrnoRet": 1,
        "syscalls": [{"names": ["read"], "action": "SCMP_ACT_ALLOW", field: value}],
    }
    assert m._eligible_names(profile) == set()
    profile["syscalls"][0].pop(field)
    assert m._eligible_names(profile) == {"read"}


def test_fresh_interpreter_audit_no_optional_execution():
    code = r"""
import sys, os
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if (event.startswith(('subprocess.', 'socket.', 'ctypes.'))
        or event in ('os.system', 'os.fork', 'os.posix_spawn', 'os.exec')):
        raise AssertionError('forbidden execution:' + event)
    blocked = ('PIL', 'ctypes', '_ctypes', 'sigstore', 'pypi_attestations',
               'app', 'subprocess', 'socket')
    if event == 'import' and args[0].split('.')[0] in blocked:
        raise AssertionError('forbidden import:' + args[0])
sys.addaudithook(audit)
def noenv(*a, **k): raise AssertionError('environment-derived configuration')
os.getenv = noenv
class NoEnvironment(dict):
    __getitem__ = noenv
    get = noenv
    __contains__ = noenv
    __iter__ = noenv
os.environ = NoEnvironment()
from qualification import media_syscall_policy as policy
first = policy.build_media_syscall_policy()
policy.validate_media_syscall_policy(first.policy_bytes, first.digest)
assert first.enforcement_verified is False and first.decoder_ready is False
assert not any(name == 'PIL' or name.startswith('PIL.') for name in sys.modules)
print('PURE_CANDIDATE_AUDIT_PASS')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PURE_CANDIDATE_AUDIT_PASS"


def test_vendor_drift_between_generation_and_constructor_validation(monkeypatch):
    original = m.UPSTREAM_PATH.read_bytes()
    reads = iter((original, original + b" "))
    monkeypatch.setattr(Path, "open", lambda *a, **k: io.BytesIO(next(reads)))
    with pytest.raises(m.MediaSyscallPolicyError, match="vendor_digest"):
        m.build_media_syscall_policy()


def test_vendor_close_failure_refuses_success(monkeypatch):
    original = m.UPSTREAM_PATH.read_bytes()

    class Source(io.BytesIO):
        def __exit__(self, *args):
            self.close()
            raise OSError("reported close uncertainty")

    monkeypatch.setattr(Path, "open", lambda *a, **k: Source(original))
    with pytest.raises(m.MediaSyscallPolicyError, match="vendor_read"):
        m.build_media_syscall_policy()


@pytest.mark.parametrize(
    "profile",
    [
        None,
        [],
        {},
        {"defaultAction": "SCMP_ACT_ALLOW", "defaultErrnoRet": 1, "syscalls": []},
        {"defaultAction": "SCMP_ACT_ERRNO", "defaultErrnoRet": True, "syscalls": []},
        {"defaultAction": "SCMP_ACT_ERRNO", "defaultErrnoRet": 1, "syscalls": [None]},
        {
            "defaultAction": "SCMP_ACT_ERRNO",
            "defaultErrnoRet": 1,
            "syscalls": [{"names": "read", "action": "SCMP_ACT_ALLOW"}],
        },
    ],
)
def test_invalid_vendor_shape_refuses_fixed_error(profile):
    with pytest.raises(m.MediaSyscallPolicyError, match="vendor_shape"):
        m._eligible_names(profile)


def test_missing_unconditional_eligibility_refuses_factory(monkeypatch):
    monkeypatch.setattr(
        m,
        "_read_vendor",
        lambda: {
            "defaultAction": "SCMP_ACT_ERRNO",
            "defaultErrnoRet": 1,
            "syscalls": [{"names": ["read"], "action": "SCMP_ACT_ALLOW"}],
        },
    )
    with pytest.raises(m.MediaSyscallPolicyError, match="vendor_eligibility"):
        m.build_media_syscall_policy()
