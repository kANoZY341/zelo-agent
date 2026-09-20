"""Trusted worker authority profiles and approval-binding policy."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass

AUTHORITY_POLICY_SCHEMA = "zelo-worker-authority-v1"


@dataclass(frozen=True, slots=True)
class WorkerAuthorityProfile:
    """Concrete execution policy resolved from a trusted descriptor authority."""

    name: str
    authority: tuple[str, ...]
    workspace_write: bool
    network: bool


_WORKSPACE_WRITE = WorkerAuthorityProfile(
    name="workspace-write-no-network-v1",
    authority=("workspace-write",),
    workspace_write=True,
    network=False,
)

_PROFILES = {
    _WORKSPACE_WRITE.authority: _WORKSPACE_WRITE,
}


def authority_policy_fingerprint(profiles: Iterable[WorkerAuthorityProfile]) -> str:
    """Hash the complete permission semantics so policy changes invalidate approvals."""
    payload = {
        "schema": AUTHORITY_POLICY_SCHEMA,
        "profiles": [
            asdict(profile)
            for profile in sorted(profiles, key=lambda item: (item.authority, item.name))
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


AUTHORITY_POLICY_VERSION = authority_policy_fingerprint(_PROFILES.values())


def authority_profile(authority: Iterable[str]) -> WorkerAuthorityProfile:
    """Resolve trusted descriptor authority to an enforced profile or fail closed."""
    key = tuple(authority)
    try:
        return _PROFILES[key]
    except KeyError:
        raise ValueError("worker_authority_unsupported") from None


def authority_snapshot_matches(
    authority: Iterable[str], profile_name: str, policy_version: str
) -> bool:
    if policy_version != AUTHORITY_POLICY_VERSION:
        return False
    try:
        return authority_profile(authority).name == profile_name
    except ValueError:
        return False
