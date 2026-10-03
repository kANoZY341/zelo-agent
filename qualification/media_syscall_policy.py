"""Fixed candidate syscall intentions, never compilation or enforcement evidence.

Trusted source pins and ancestors are operator inputs. Exact validation rereads the
original vendor bytes; a changed candidate's own matching hash never authorizes it.
No native library, process, filter, copied package or application is accessed.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from qualification.runtime_contracts import validate_digest

UPSTREAM_COMMIT = "2ceae35d351c156cb5a8efc0fdc4a08cf94569d8"
UPSTREAM_SHA256 = "6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6"
LICENSE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
UPSTREAM_PATH = Path(__file__).parent / "vendor" / "moby-profiles" / "default.json"
MAX_VENDOR_BYTES = 65536
MAX_POLICY_BYTES = 16384

# Linux x86-64 intentions; no constants here are installed into the kernel.
PROT_EXEC_MASK = 0x4
OPEN_WRITE_MASK = 0x3 | 0x40 | 0x200 | 0x400 | 0x400000
# O_ACCMODE, O_CREAT, O_TRUNC, O_APPEND and __O_TMPFILE. O_DIRECTORY remains allowed.

_GROUPS = (
    ("bounded-channel-io", ("read", "readv", "write", "writev")),
    (
        "readonly-scanner-files",
        (
            "close",
            "fstat",
            "getdents64",
            "lseek",
            "newfstatat",
            "openat",
            "pread64",
            "readlink",
            "readlinkat",
            "stat",
            "statx",
        ),
    ),
    ("nonexecuting-memory-management", ("brk", "madvise", "mmap", "mprotect", "munmap")),
    (
        "python-signal-and-lock-runtime",
        ("futex", "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "sched_yield"),
    ),
    ("fixed-bootstrap-clock", ("clock_gettime", "clock_nanosleep")),
    (
        "stdlib-local-identity-and-diagnostics",
        (
            "getcwd",
            "getegid",
            "geteuid",
            "getgid",
            "getpid",
            "getrandom",
            "getrlimit",
            "getrusage",
            "gettid",
            "getuid",
            "uname",
        ),
    ),
    ("owned-guest-exit", ("exit", "exit_group")),
)

_ARGUMENTS = (
    ("openat", 2, OPEN_WRITE_MASK, 0),
    ("mmap", 2, PROT_EXEC_MASK, 0),
    ("mprotect", 2, PROT_EXEC_MASK, 0),
)


class MediaSyscallPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _eligible_names(profile: dict) -> set[str]:
    if (
        type(profile) is not dict
        or profile.get("defaultAction") != "SCMP_ACT_ERRNO"
        or type(profile.get("defaultErrnoRet")) is not int
        or profile["defaultErrnoRet"] != 1
        or type(profile.get("syscalls")) is not list
        or len(profile["syscalls"]) > 512
    ):
        raise MediaSyscallPolicyError("media_syscall_vendor_shape")
    result = set()
    for rule in profile["syscalls"]:
        if type(rule) is not dict:
            raise MediaSyscallPolicyError("media_syscall_vendor_shape")
        # Even empty conditional fields are not flattened into eligibility.
        if set(rule) != {"names", "action"} or rule["action"] != "SCMP_ACT_ALLOW":
            continue
        names = rule["names"]
        if type(names) is not list or len(names) > 512 or any(type(n) is not str for n in names):
            raise MediaSyscallPolicyError("media_syscall_vendor_shape")
        result.update(names)
    return result


def _read_vendor() -> dict:
    try:
        with UPSTREAM_PATH.open("rb") as source:
            raw = source.read(MAX_VENDOR_BYTES + 1)
    except OSError as exc:
        raise MediaSyscallPolicyError("media_syscall_vendor_read") from exc
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_VENDOR_BYTES:
        raise MediaSyscallPolicyError("media_syscall_vendor_bytes")
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SHA256:
        raise MediaSyscallPolicyError("media_syscall_vendor_digest")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MediaSyscallPolicyError("media_syscall_vendor_json") from exc


def _canonical_policy() -> bytes:
    eligible = _eligible_names(_read_vendor())
    selected = tuple(sorted((name, reason) for reason, names in _GROUPS for name in names))
    if len({name for name, _ in selected}) != len(selected) or any(
        name not in eligible for name, _ in selected
    ):
        raise MediaSyscallPolicyError("media_syscall_vendor_eligibility")
    rules = []
    for name, reason in selected:
        args = [
            {"index": index, "operation": "masked-equal", "mask": mask, "value": value}
            for syscall, index, mask, value in _ARGUMENTS
            if syscall == name
        ]
        rules.append({"name": name, "rationale": reason, "arguments": args})
    policy = {
        "schema": "zelo-media-stdlib-syscall-candidate-v1",
        "purpose": "trusted-stdlib-scanner-probes-only",
        "architecture_intention": "native-x86-64-only",
        "unsupported_abi_intention": "kill-whole-process-including-x32",
        "default_action_intention": "errno",
        "default_errno_intention": 1,
        "allow_rules": rules,
        "other_allowed_arguments_restricted": False,
        "bootstrap_order_intention": (
            "trusted-single-threaded-bootstrap",
            "filter",
            "package-path-only-after-separate-authorization",
        ),
        "vendor": {
            "repository": "moby/profiles",
            "commit": UPSTREAM_COMMIT,
            "sha256": UPSTREAM_SHA256,
            "license": "Apache-2.0",
            "license_sha256": LICENSE_SHA256,
            "eligibility": "exact-names-action-unconditional-allow-rule-only",
        },
        "instruction_trust": "none",
        "compiled": False,
        "enforcement_verified": False,
        "decoder_ready": False,
        "aggregate_resources_verified": False,
        "runtime_authority": False,
    }
    raw = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(raw) > MAX_POLICY_BYTES:
        raise MediaSyscallPolicyError("media_syscall_policy_bytes")
    return raw


def validate_media_syscall_policy(policy_bytes: bytes, digest: str) -> None:
    """Validate exact current candidate identity; never attach or certify a filter."""
    if type(policy_bytes) is not bytes or not 0 < len(policy_bytes) <= MAX_POLICY_BYTES:
        raise MediaSyscallPolicyError("media_syscall_policy_bytes")
    try:
        validate_digest(digest)
    except ValueError as exc:
        raise MediaSyscallPolicyError("media_syscall_policy_digest") from exc
    if hashlib.sha256(policy_bytes).hexdigest() != digest:
        raise MediaSyscallPolicyError("media_syscall_policy_digest")
    if policy_bytes != _canonical_policy():
        raise MediaSyscallPolicyError("media_syscall_policy_identity")


@dataclass(frozen=True)
class MediaSyscallCandidate:
    """Retained candidate bytes, not proof of ABI, compilation, enforcement or cleanup."""

    policy_bytes: bytes
    digest: str
    schema: str = field(default="zelo-media-stdlib-syscall-candidate-v1", init=False)
    instruction_trust: str = field(default="none", init=False)
    enforcement_verified: bool = field(default=False, init=False)
    decoder_ready: bool = field(default=False, init=False)
    runtime_authority: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        validate_media_syscall_policy(self.policy_bytes, self.digest)


def build_media_syscall_policy() -> MediaSyscallCandidate:
    """Build the sole fixed candidate; fresh validation also rechecks vendor integrity."""
    raw = _canonical_policy()
    return MediaSyscallCandidate(raw, hashlib.sha256(raw).hexdigest())
