"""Trusted worker authority profiles and approval-binding policy."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from app.developer_process import environment
from app.developer_sandbox import ENGINE, REPO, sandbox_plan

AUTHORITY_POLICY_SCHEMA = "zelo-worker-authority-v2"


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


_CODEX_DISABLED_FEATURES = (
    "apps",
    "plugins",
    "remote_plugin",
    "hooks",
    "multi_agent",
    "browser_use",
    "computer_use",
    "image_generation",
    "shell_snapshot",
    "workspace_dependencies",
    "skill_mcp_dependency_install",
    "skill_search",
    "network_proxy",
    "guardian_approval",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _toml_table(values: dict[str, str]) -> str:
    return (
        "{"
        + ",".join(
            json.dumps(key) + "=" + json.dumps(value) for key, value in sorted(values.items())
        )
        + "}"
    )


def codex_permission_semantics(profile: WorkerAuthorityProfile) -> dict[str, object]:
    filesystem = {
        ":minimal": "read",
        "/opt/zelo-codex": "read",
        REPO: "write" if profile.workspace_write else "read",
        REPO + "/.git": "read",
        ENGINE: "deny",
    }
    return {
        "default_permissions": "zelo",
        "filesystem": filesystem,
        "network_enabled": profile.network,
        "approval_policy": "never",
        "web_search": "disabled",
        "model_reasoning_summary": "none",
        "show_raw_agent_reasoning": False,
        "allow_login_shell": False,
        "shell_environment_policy": {
            "inherit": "none",
            "set": environment(),
            "experimental_use_profile": False,
        },
        "projects": {REPO: {"trust_level": "untrusted"}},
        "features": {
            **{feature: False for feature in _CODEX_DISABLED_FEATURES},
            "skip_host_skill_discovery": True,
        },
    }


def codex_cli_overrides(profile: WorkerAuthorityProfile) -> tuple[str, ...]:
    semantics = codex_permission_semantics(profile)
    overrides = [
        'default_permissions="zelo"',
        "permissions.zelo.filesystem=" + _toml_table(semantics["filesystem"]),
        f"permissions.zelo.network.enabled={str(semantics['network_enabled']).lower()}",
        'approval_policy="never"',
        'web_search="disabled"',
        'model_reasoning_summary="none"',
        "show_raw_agent_reasoning=false",
        "allow_login_shell=false",
        'shell_environment_policy.inherit="none"',
        "shell_environment_policy.set=" + _toml_table(semantics["shell_environment_policy"]["set"]),
        "shell_environment_policy.experimental_use_profile=false",
        f'projects.{json.dumps(REPO)}.trust_level="untrusted"',
    ]
    for feature in _CODEX_DISABLED_FEATURES:
        overrides.append(f"features.{feature}=false")
    overrides.append("features.skip_host_skill_discovery=true")
    return tuple(overrides)


def sandbox_permission_semantics(profile: WorkerAuthorityProfile) -> dict[str, object]:
    return sandbox_plan(
        repo_present=True,
        workspace_write=profile.workspace_write,
        git_metadata_present=True,
        include_venv=False,
        include_codex=True,
        include_code_mode_host=True,
        include_auth=True,
        service_network=True,
        git_inspection=False,
    )


def realized_authority_policy(profile: WorkerAuthorityProfile) -> dict[str, object]:
    return {
        "name": profile.name,
        "authority": profile.authority,
        "codex": codex_permission_semantics(profile),
        "codex_cli_overrides": codex_cli_overrides(profile),
        "sandbox": sandbox_permission_semantics(profile),
    }


def authority_policy_fingerprint(profiles: Iterable[WorkerAuthorityProfile]) -> str:
    """Hash the complete permission semantics so policy changes invalidate approvals."""
    payload = {
        "schema": AUTHORITY_POLICY_SCHEMA,
        "profiles": [
            realized_authority_policy(profile)
            for profile in sorted(profiles, key=lambda item: (item.authority, item.name))
        ],
    }
    encoded = _canonical_json(payload)
    return hashlib.sha256(encoded).hexdigest()


def authority_policy_version() -> str:
    return authority_policy_fingerprint(_PROFILES.values())


AUTHORITY_POLICY_VERSION = authority_policy_version()


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
    if policy_version != authority_policy_version():
        return False
    try:
        return authority_profile(authority).name == profile_name
    except ValueError:
        return False
