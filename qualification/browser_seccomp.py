"""Fixed, offline native-browser seccomp candidate; no runtime activation.

Code, pinned constants and repository ancestors are trusted operator inputs, not
page/caller configuration. The digest authenticates vendor bytes against that
trusted pin, not against an independently compromised Python process. Consumers
must validate the exact immutable bytes they use; this does not attach a filter,
verify a container's architecture, or enforce other container/sandbox settings.
"""

import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path

from qualification.runtime_contracts import validate_digest

UPSTREAM_COMMIT = "2ceae35d351c156cb5a8efc0fdc4a08cf94569d8"
UPSTREAM_SHA256 = "6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6"
UPSTREAM_PATH = Path(__file__).parent / "vendor" / "moby-profiles" / "default.json"
MAX_PROFILE_BYTES = 64 * 1024


class SeccompProfileError(ValueError):
    """The pinned qualification candidate is unavailable or does not match."""


def _canonical_profile_bytes() -> bytes:
    if platform.machine() not in ("x86_64", "amd64"):
        raise SeccompProfileError("unsupported_architecture")
    try:
        with UPSTREAM_PATH.open("rb") as source:
            raw = source.read(MAX_PROFILE_BYTES + 1)
    except OSError as exc:
        raise SeccompProfileError("upstream_read_failed") from exc
    if len(raw) > MAX_PROFILE_BYTES:
        raise SeccompProfileError("upstream_oversized")
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SHA256:
        raise SeccompProfileError("upstream_digest_mismatch")
    # Parse only authenticated bytes. No shared mutable dict or policy cache.
    try:
        profile = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SeccompProfileError("invalid_upstream_json") from exc
    if (
        type(profile) is not dict
        or profile.get("defaultAction") != "SCMP_ACT_ERRNO"
        or type(profile.get("syscalls")) is not list
    ):
        raise SeccompProfileError("invalid_upstream_profile")
    for name, flags in (
        ("clone", 0x10000011),
        ("clone", 0x20000011),
        ("clone", 0x70000011),
        ("unshare", 0x10000000),
    ):
        profile["syscalls"].append(
            {
                "names": [name],
                "action": "SCMP_ACT_ALLOW",
                "args": [{"index": 0, "value": flags, "op": "SCMP_CMP_EQ"}],
                "includes": {"arches": ["amd64"]},
            }
        )
    profile["syscalls"].append(
        {
            "names": ["chroot"],
            "action": "SCMP_ACT_ALLOW",
            "includes": {"arches": ["amd64"]},
        }
    )
    result = json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(result) > MAX_PROFILE_BYTES:
        raise SeccompProfileError("profile_oversized")
    return result


def validate_profile(profile_bytes: bytes, digest: str) -> None:
    """Fail closed unless bytes and digest match the current pinned candidate.

    Only this exact serialization is accepted, including its preserved baseline.
    Rehashing altered JSON cannot authorize a new policy. Re-read the fixed vendor
    file on every validation so drift after generation also fails closed.
    """
    if type(profile_bytes) is not bytes or not 0 < len(profile_bytes) <= MAX_PROFILE_BYTES:
        raise SeccompProfileError("invalid_profile_bytes")
    try:
        validate_digest(digest)
    except ValueError as exc:
        raise SeccompProfileError("invalid_profile_digest") from exc
    if hashlib.sha256(profile_bytes).hexdigest() != digest:
        raise SeccompProfileError("profile_digest_mismatch")
    if profile_bytes != _canonical_profile_bytes():
        raise SeccompProfileError("profile_policy_mismatch")


@dataclass(frozen=True)
class SeccompProfile:
    profile_bytes: bytes
    digest: str

    def __post_init__(self) -> None:
        validate_profile(self.profile_bytes, self.digest)


def build_profile() -> SeccompProfile:
    """Build the sole qualification candidate for the local x86-64 host."""
    raw = _canonical_profile_bytes()
    return SeccompProfile(raw, hashlib.sha256(raw).hexdigest())
