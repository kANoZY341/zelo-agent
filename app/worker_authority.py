"""Trusted worker authority profiles and approval-binding policy."""

from dataclasses import dataclass
from typing import Iterable

AUTHORITY_POLICY_VERSION = "zelo-worker-authority-v1"


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
