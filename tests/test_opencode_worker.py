"""OpenCode adapter policy and protocol tests; no external model calls."""

import asyncio
import json

import pytest

from app.developer import DeveloperError
from app.developer_process import ProcessResult
from app.developer_sandbox import external_worker_sandbox_plan
from app.opencode_worker import (
    CAPABILITY_PROFILE,
    OpenCodeWorker,
    assert_project_config_absent,
    configuration_version,
    inline_config,
    parse_cli_version,
    parse_text_output,
)
from app.worker_authority import (
    OPENCODE_SAFE_SHELL_RESOURCES,
    authority_profile,
    opencode_permission_semantics,
)


def configured_worker(settings, monkeypatch):
    binary = settings.runtime_dir / "fake-opencode"
    binary.write_bytes(b"fake opencode executable")
    binary.chmod(0o700)
    configured = settings.model_copy(
        update={
            "developer_opencode_executable": binary,
            "developer_opencode_model": "ollama/qwen3:4b-instruct",
        }
    )

    def fake_sandbox(_repo, command, **_kwargs):
        return command

    async def fake_process(argv, **_kwargs):
        assert "--version" in argv
        return ProcessResult(0, b"2.0.7\n", b"", 0.01)

    monkeypatch.setattr("app.opencode_worker.external_worker_sandbox", fake_sandbox)
    monkeypatch.setattr("app.opencode_worker.run_process", fake_process)
    return OpenCodeWorker(configured)


def test_opencode_identity_is_generic_and_configuration_bound(settings, monkeypatch):
    worker = configured_worker(settings, monkeypatch)
    identity = worker.identity()
    assert identity.implementation == "opencode"
    assert identity.cli_version == "2.0.7"
    assert identity.capability_profile == CAPABILITY_PROFILE
    assert [item.name for item in identity.artifacts] == ["executable"]
    assert identity.credential_locations == ()

    changed_model = worker.settings.model_copy(
        update={"developer_opencode_model": "ollama/qwen3-coder:8b"}
    )
    changed_url = worker.settings.model_copy(update={"ollama_url": "http://127.0.0.1:11435"})
    assert configuration_version(changed_model) != identity.config_version
    assert configuration_version(changed_url) != identity.config_version


@pytest.mark.parametrize("raw", [b"1.9.9\n", b"3.0.0\n", b"not-a-version\n"])
def test_opencode_unsupported_versions_fail_closed(raw):
    with pytest.raises(DeveloperError, match="version"):
        parse_cli_version(raw)


def test_opencode_preflight_never_needs_service_network(settings, monkeypatch):
    worker = configured_worker(settings, monkeypatch)
    seen = {}

    def capture(repo, command, **kwargs):
        seen.update(kwargs)
        return command

    async def fake_process(argv, **_kwargs):
        return ProcessResult(0, b"v2.0.7\n", b"", 0.01)

    monkeypatch.setattr("app.opencode_worker.external_worker_sandbox", capture)
    monkeypatch.setattr("app.opencode_worker.run_process", fake_process)
    identity = asyncio.run(worker.preflight())
    assert identity.implementation == "opencode"
    assert seen["service_network"] is False
    assert seen["writable"] is False


def test_opencode_argv_uses_private_standalone_pure_session(settings, monkeypatch, tmp_path):
    worker = configured_worker(settings, monkeypatch)
    approved = worker.identity()
    captured = {}

    def capture(repo, command, **kwargs):
        captured["repo"] = repo
        captured["command"] = command
        captured["kwargs"] = kwargs
        return command

    monkeypatch.setattr("app.opencode_worker.external_worker_sandbox", capture)
    with worker.bind(approved) as bound:
        argv = bound.argv(
            tmp_path,
            authority_profile(("workspace-write",)),
            "test prompt",
        )

    assert argv == captured["command"]
    assert captured["repo"] == tmp_path
    assert captured["kwargs"]["writable"] is True
    assert captured["kwargs"]["service_network"] is True
    assert argv[:3] == ["/opt/zelo-worker/worker", "--pure", "run"]
    assert "--standalone" in argv
    assert "--model" in argv
    assert "--agent" in argv
    assert "--format" in argv
    assert argv[-1] == "test prompt"


def test_opencode_inline_config_allows_only_trusted_inspection_shell(settings):
    profile = authority_profile(("workspace-write",))
    config = inline_config(settings, profile)
    semantics = opencode_permission_semantics(profile)
    assert config["permissions"] == semantics["permissions"]
    assert config["experimental"]["policies"] == semantics["policies"]
    assert config["providers"]["ollama"]["settings"]["baseURL"] == "http://127.0.0.1:11434/v1"

    permissions = list(semantics["permissions"])
    policies = list(semantics["policies"])
    permission_deny = {"action": "shell", "resource": "*", "effect": "deny"}
    policy_deny = {"action": "permission", "resource": "shell:*", "effect": "deny"}
    assert permission_deny in permissions
    assert policy_deny in policies

    for resource in OPENCODE_SAFE_SHELL_RESOURCES:
        permission_allow = {"action": "shell", "resource": resource, "effect": "allow"}
        policy_allow = {
            "action": "permission",
            "resource": f"shell:{resource}",
            "effect": "allow",
        }
        assert permission_allow in permissions
        assert policy_allow in policies
        assert permissions.index(permission_deny) < permissions.index(permission_allow)
        assert policies.index(policy_deny) < policies.index(policy_allow)

    allowed = " ".join(OPENCODE_SAFE_SHELL_RESOURCES)
    for forbidden in ("git push", "gh ", "curl", "wget", "ssh", "sudo", "pytest", "ruff", "python"):
        assert forbidden not in allowed

    assert {"action": "permission", "resource": "webfetch:*", "effect": "deny"} in policies
    assert {"action": "permission", "resource": "websearch:*", "effect": "deny"} in policies
    assert {"action": "provider.use", "resource": "*", "effect": "deny"} in policies
    assert {"action": "provider.use", "resource": "ollama", "effect": "allow"} in policies


def test_external_worker_plan_replaces_repository_git_config():
    plan = external_worker_sandbox_plan(
        repo_present=True,
        workspace_write=True,
        service_network=True,
    )
    assert plan["git_metadata"]["git_inspection"] is False
    assert plan["git_metadata"]["trusted_config"] is True
    assert {
        "op": "--ro-bind",
        "source": "$DEVELOPER_GITCONFIG",
        "dest": "/workspace/.git/config",
    } in plan["operations"]


def test_opencode_reviewer_policy_is_read_only():
    semantics = opencode_permission_semantics(authority_profile(("workspace-read",)))
    assert {"action": "edit", "resource": "*", "effect": "deny"} in semantics["permissions"]
    assert {"action": "permission", "resource": "edit:*", "effect": "deny"} in semantics["policies"]


@pytest.mark.parametrize("name", ["opencode.json", "opencode.jsonc", ".opencode"])
def test_opencode_rejects_project_configuration(tmp_path, name):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / name
    if name == ".opencode":
        target.mkdir()
    else:
        target.write_text("{}")
    with pytest.raises(DeveloperError, match="project_configuration_forbidden"):
        assert_project_config_absent(repo)


def test_opencode_text_output_is_strict_and_bounded():
    assert parse_text_output(b"done\n") == "done"
    with pytest.raises(DeveloperError, match="output_invalid"):
        parse_text_output(b"")
    with pytest.raises(DeveloperError, match="output_invalid"):
        parse_text_output(b"x" * 4097)
    with pytest.raises(DeveloperError, match="output_invalid"):
        parse_text_output(b"\xff")


def test_inline_config_is_json_serializable(settings):
    payload = inline_config(settings, authority_profile(("workspace-write",)))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert "ollama/qwen3:4b-instruct" in encoded
