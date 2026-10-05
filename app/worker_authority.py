"""Trusted worker authority profiles and approval-binding policy."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app import developer_sandbox
from app.developer_process import environment

AUTHORITY_POLICY_SCHEMA = "zelo-worker-authority-v7"


@dataclass(frozen=True, slots=True)
class WorkerAuthorityProfile:
    """Concrete execution policy resolved from a trusted descriptor authority."""

    name: str
    authority: tuple[str, ...]
    workspace_write: bool
    network: bool
    workspace_read: bool = True
    surface: Literal["workspace", "python", "api", "browser", "shell", "computer"] = "workspace"


_WORKSPACE_WRITE = WorkerAuthorityProfile(
    name="workspace-write-no-network-v1",
    authority=("workspace-write",),
    workspace_write=True,
    network=False,
)

_WORKSPACE_READ = WorkerAuthorityProfile(
    name="workspace-read-no-network-v1",
    authority=("workspace-read",),
    workspace_write=False,
    network=False,
)

_ISOLATED_COMPUTE = WorkerAuthorityProfile(
    name="isolated-compute-no-network-v1",
    authority=("isolated-compute",),
    workspace_write=False,
    network=False,
    workspace_read=False,
    surface="python",
)

_PUBLIC_API_READ = WorkerAuthorityProfile(
    name="public-api-read-no-credentials-v1",
    authority=("public-api-read",),
    workspace_write=False,
    network=True,
    workspace_read=False,
    surface="api",
)

_PUBLIC_DOCUMENT_RENDER = WorkerAuthorityProfile(
    name="public-document-render-no-credentials-v1",
    authority=("public-document-render",),
    workspace_write=False,
    network=True,
    workspace_read=False,
    surface="browser",
)

_ISOLATED_SHELL = WorkerAuthorityProfile(
    name="isolated-shell-compute-no-network-v1",
    authority=("isolated-shell-compute",),
    workspace_write=False,
    network=False,
    workspace_read=False,
    surface="shell",
)

_AGENT_COMPUTER_READ = WorkerAuthorityProfile(
    name="agent-computer-read-v1",
    authority=("agent-computer-read",),
    workspace_write=False,
    network=False,
    workspace_read=False,
    surface="computer",
)

_AGENT_COMPUTER_NAVIGATE = WorkerAuthorityProfile(
    name="agent-computer-navigate-v1",
    authority=("agent-computer-navigate",),
    workspace_write=False,
    network=False,
    workspace_read=False,
    surface="computer",
)

_PROFILES = {
    _AGENT_COMPUTER_NAVIGATE.authority: _AGENT_COMPUTER_NAVIGATE,
    _AGENT_COMPUTER_READ.authority: _AGENT_COMPUTER_READ,
    _ISOLATED_SHELL.authority: _ISOLATED_SHELL,
    _PUBLIC_DOCUMENT_RENDER.authority: _PUBLIC_DOCUMENT_RENDER,
    _ISOLATED_COMPUTE.authority: _ISOLATED_COMPUTE,
    _PUBLIC_API_READ.authority: _PUBLIC_API_READ,
    _WORKSPACE_READ.authority: _WORKSPACE_READ,
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
    workspace_access = (
        "deny" if not profile.workspace_read else ("write" if profile.workspace_write else "read")
    )
    filesystem = {
        ":minimal": "read",
        "/opt/zelo-codex": "read",
        developer_sandbox.REPO: workspace_access,
        developer_sandbox.REPO + "/.git": "read" if profile.workspace_read else "deny",
        developer_sandbox.ENGINE: "deny",
    }
    return {
        "default_permissions": "zelo",
        "filesystem": filesystem,
        "network_enabled": profile.network if profile.surface == "workspace" else False,
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
        "projects": {developer_sandbox.REPO: {"trust_level": "untrusted"}},
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
        f'projects.{json.dumps(developer_sandbox.REPO)}.trust_level="untrusted"',
    ]
    for feature in _CODEX_DISABLED_FEATURES:
        overrides.append(f"features.{feature}=false")
    overrides.append("features.skip_host_skill_discovery=true")
    return tuple(overrides)


def claude_code_tools(profile: WorkerAuthorityProfile) -> tuple[str, ...]:
    if not profile.workspace_read:
        return ()
    base = ("Bash", "Glob", "Grep", "Read")
    return (*base, "Edit", "Write") if profile.workspace_write else base


def claude_code_settings(profile: WorkerAuthorityProfile) -> dict[str, object]:
    """CLI-supplied settings; repository/user settings are excluded by restricted mode."""
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "network": {
                "allowedDomains": [],
                "strictAllowlist": True,
            },
        },
        "permissions": {
            "blockReadsOutsideWorkingDirectories": True,
        },
    }


def claude_code_permission_semantics(profile: WorkerAuthorityProfile) -> dict[str, object]:
    tools = claude_code_tools(profile)
    return {
        "bare": True,
        "restricted": True,
        "permission_mode": "dontAsk",
        "permission_prompts": "none",
        "session_persistence": False,
        "tools": tools,
        "allowed_tools": tools,
        "disallowed_tools": ("WebFetch", "WebSearch", "mcp__*"),
        "subprocess_env_scrub": True,
        "settings": claude_code_settings(profile),
    }


OPENCODE_SAFE_BASH_RESOURCES = (
    "git status --short",
    "git status --short *",
    "git diff --no-ext-diff --no-textconv",
    "git diff --no-ext-diff --no-textconv *",
    "git diff --cached --no-ext-diff --no-textconv",
    "git diff --cached --no-ext-diff --no-textconv *",
    "git rev-parse *",
    "git ls-files *",
)


def opencode_permission_semantics(profile: WorkerAuthorityProfile) -> dict[str, object]:
    permissions = [{"action": "*", "resource": "*", "effect": "deny"}]
    if profile.workspace_read:
        permissions.extend(
            (
                {"action": "read", "resource": "*", "effect": "allow"},
                {"action": "read", "resource": "*.env", "effect": "deny"},
                {"action": "read", "resource": "*.env.*", "effect": "deny"},
                {"action": "read", "resource": "*.env.example", "effect": "allow"},
                {"action": "glob", "resource": "*", "effect": "allow"},
                {"action": "grep", "resource": "*", "effect": "allow"},
            )
        )
        if profile.workspace_write:
            permissions.append({"action": "edit", "resource": "*", "effect": "allow"})
        permissions.extend(
            {"action": "bash", "resource": resource, "effect": "allow"}
            for resource in OPENCODE_SAFE_BASH_RESOURCES
        )

    policies = [{"action": "provider.use", "resource": "*", "effect": "deny"}]
    if profile.surface == "workspace":
        policies.append({"action": "provider.use", "resource": "ollama", "effect": "allow"})
    return {
        "provider": "ollama",
        "transport": "authenticated-local-http",
        "server_hostname": "127.0.0.1",
        "project_config": "disabled",
        "permissions": tuple(permissions),
        "policies": tuple(policies),
    }


def sandbox_permission_semantics(profile: WorkerAuthorityProfile) -> dict[str, object]:
    if profile.surface == "computer":
        return {}
    if profile.surface == "shell":
        from app.shell_worker import shell_policy_semantics

        return {"shell_compute": shell_policy_semantics()}
    if profile.surface == "python":
        return {"python_compute": developer_sandbox.python_compute_sandbox_plan()}
    if profile.surface == "api":
        return {}
    return developer_sandbox.worker_sandbox_plans(profile.workspace_write)


def external_worker_sandbox_semantics(profile: WorkerAuthorityProfile) -> dict[str, object]:
    if profile.surface != "workspace":
        return developer_sandbox.external_worker_sandbox_plan(
            repo_present=False,
            workspace_write=False,
            service_network=False,
        )
    return developer_sandbox.external_worker_sandbox_plans(profile.workspace_write)


def realized_authority_policy(profile: WorkerAuthorityProfile) -> dict[str, object]:
    python_compute = None
    api_http = None
    shell_compute = None
    computer_read = None
    computer_navigate = None
    if profile.authority == _AGENT_COMPUTER_READ.authority:
        from app.computer_worker import computer_read_policy_semantics

        computer_read = computer_read_policy_semantics()
    if profile.authority == _AGENT_COMPUTER_NAVIGATE.authority:
        from app.computer_worker import computer_navigate_policy_semantics

        computer_navigate = computer_navigate_policy_semantics()
    if profile.surface == "shell":
        from app.shell_worker import shell_policy_semantics

        shell_compute = shell_policy_semantics()
    if profile.surface == "python":
        from app.python_worker import python_policy_semantics

        python_compute = python_policy_semantics()
    if profile.surface == "api":
        from app.api_worker import api_policy_semantics

        api_http = api_policy_semantics()
    return {
        "name": profile.name,
        "authority": profile.authority,
        "workspace_read": profile.workspace_read,
        "surface": profile.surface,
        "codex": codex_permission_semantics(profile),
        "codex_cli_overrides": codex_cli_overrides(profile),
        "claude_code": claude_code_permission_semantics(profile),
        "opencode": opencode_permission_semantics(profile),
        "sandbox": sandbox_permission_semantics(profile),
        "external_worker_sandbox": external_worker_sandbox_semantics(profile),
        "python_compute": python_compute,
        "api_http": api_http,
        "shell_compute": shell_compute,
        "computer_read": computer_read,
        "computer_navigate": computer_navigate,
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
