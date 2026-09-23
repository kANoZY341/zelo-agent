"""OpenCode adapter policy and HTTP protocol tests; no external model calls."""

import asyncio
import json

import pytest

from app.developer import DeveloperError
from app.developer_process import ProcessResult
from app.developer_sandbox import external_worker_sandbox_plan
from app.opencode_worker import (
    CAPABILITY_PROFILE,
    COMPACTION_BUFFER_TOKENS,
    COMPACTION_KEEP_TOKENS,
    MAX_STEPS,
    OpenCodeWorker,
    assert_project_config_absent,
    configuration_version,
    inline_config,
    parse_assistant_summary,
    parse_cli_version,
    parse_server_url,
    parse_text_output,
)
from app.worker_authority import (
    OPENCODE_SAFE_BASH_RESOURCES,
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
        if "--version" in argv:
            return ProcessResult(0, b"2.0.14\n", b"", 0.01)
        if argv[-2:] == ["serve", "--help"]:
            return ProcessResult(0, b"--hostname string\n--port number\n", b"", 0.01)
        raise AssertionError(argv)

    monkeypatch.setattr("app.opencode_worker.external_worker_sandbox", fake_sandbox)
    monkeypatch.setattr("app.opencode_worker.run_process", fake_process)
    return OpenCodeWorker(configured)


def test_opencode_identity_is_generic_and_configuration_bound(settings, monkeypatch):
    worker = configured_worker(settings, monkeypatch)
    identity = worker.identity()
    assert identity.implementation == "opencode"
    assert identity.cli_version == "2.0.14"
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
    calls = []

    def capture(repo, command, **kwargs):
        calls.append((repo, command, kwargs))
        return command

    async def fake_process(argv, **_kwargs):
        if "--version" in argv:
            return ProcessResult(0, b"v2.0.14\n", b"", 0.01)
        return ProcessResult(0, b"--hostname\n--port\n", b"", 0.01)

    monkeypatch.setattr("app.opencode_worker.external_worker_sandbox", capture)
    monkeypatch.setattr("app.opencode_worker.run_process", fake_process)
    identity = asyncio.run(worker.preflight())
    assert identity.implementation == "opencode"
    assert len(calls) == 2
    assert all(call[2]["service_network"] is False for call in calls)
    assert all(call[2]["writable"] is False for call in calls)


def test_opencode_server_argv_uses_authenticated_local_http(settings, monkeypatch, tmp_path):
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
        argv = bound.server_argv(
            tmp_path,
            authority_profile(("workspace-write",)),
        )

    assert argv == captured["command"]
    assert captured["repo"] == tmp_path
    assert captured["kwargs"]["writable"] is True
    assert captured["kwargs"]["service_network"] is True
    assert argv == [
        "/opt/zelo-worker/worker",
        "serve",
        "--hostname",
        "127.0.0.1",
        "--port",
        "0",
    ]


def test_opencode_inline_config_allows_only_trusted_inspection_shell(settings):
    profile = authority_profile(("workspace-write",))
    config = inline_config(settings, profile)
    semantics = opencode_permission_semantics(profile)
    assert config["permissions"] == semantics["permissions"]
    assert config["experimental"]["policies"] == semantics["policies"]
    assert config["providers"]["ollama"]["settings"]["baseURL"] == "http://127.0.0.1:11434/v1"
    assert config["compaction"] == {
        "auto": False,
        "keep": {"tokens": COMPACTION_KEEP_TOKENS},
        "buffer": COMPACTION_BUFFER_TOKENS,
    }

    permissions = list(semantics["permissions"])
    policies = list(semantics["policies"])
    permission_deny = {"action": "*", "resource": "*", "effect": "deny"}
    assert permission_deny in permissions

    for action in ("read", "glob", "grep", "edit"):
        permission_allow = {"action": action, "resource": "*", "effect": "allow"}
        assert permission_allow in permissions
        assert permissions.index(permission_deny) < permissions.index(permission_allow)

    for resource in OPENCODE_SAFE_BASH_RESOURCES:
        permission_allow = {"action": "bash", "resource": resource, "effect": "allow"}
        assert permission_allow in permissions
        assert permissions.index(permission_deny) < permissions.index(permission_allow)

    allowed = " ".join(OPENCODE_SAFE_BASH_RESOURCES)
    for forbidden in ("git push", "gh ", "curl", "wget", "ssh", "sudo", "pytest", "ruff", "python"):
        assert forbidden not in allowed

    assert {"action": "provider.use", "resource": "*", "effect": "deny"} in policies
    assert {"action": "provider.use", "resource": "ollama", "effect": "allow"} in policies
    assert semantics["transport"] == "authenticated-local-http"
    assert semantics["server_hostname"] == "127.0.0.1"
    assert semantics["project_config"] == "disabled"


def test_opencode_server_environment_disables_project_config(settings, monkeypatch):
    worker = configured_worker(settings, monkeypatch)
    profile = authority_profile(("workspace-write",))
    env = worker._server_environment(profile, "test-secret")
    payload = json.loads(env["OPENCODE_CONFIG_CONTENT"])

    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert env["OPENCODE_SERVER_USERNAME"] == "opencode"
    assert env["OPENCODE_SERVER_PASSWORD"] == "test-secret"
    assert payload["compaction"]["auto"] is False
    assert payload["agents"]["build"]["steps"] == MAX_STEPS


def test_opencode_server_url_is_strictly_local():
    assert (
        parse_server_url(b"server listening on http://127.0.0.1:4097\n") == "http://127.0.0.1:4097"
    )
    with pytest.raises(DeveloperError, match="server_start_failed"):
        parse_server_url(b"server listening on http://0.0.0.0:4097\n")
    with pytest.raises(DeveloperError, match="server_start_failed"):
        parse_server_url(b"garbage\n")


def test_opencode_assistant_summary_accepts_v2_and_legacy_shapes():
    assert (
        parse_assistant_summary(
            {
                "data": [
                    {
                        "type": "assistant",
                        "content": [{"type": "text", "id": "prt_1", "text": "done"}],
                    },
                    {"type": "user", "text": "task"},
                ]
            }
        )
        == "done"
    )
    assert (
        parse_assistant_summary(
            {
                "data": [
                    {
                        "info": {"role": "assistant"},
                        "parts": [{"type": "text", "text": "legacy"}],
                    }
                ]
            }
        )
        == "legacy"
    )


def test_opencode_assistant_summary_rejects_missing_or_error_output():
    with pytest.raises(DeveloperError, match="output_invalid"):
        parse_assistant_summary({"data": [{"type": "user", "text": "task"}]})
    with pytest.raises(DeveloperError, match="model_error"):
        parse_assistant_summary(
            {
                "data": [
                    {
                        "type": "assistant",
                        "error": {"type": "unknown", "message": "failed"},
                        "content": [],
                    }
                ]
            }
        )


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
    permissions = list(semantics["permissions"])
    policies = list(semantics["policies"])
    assert {"action": "*", "resource": "*", "effect": "deny"} in permissions
    assert {"action": "edit", "resource": "*", "effect": "allow"} not in permissions
    assert all(policy["action"] == "provider.use" for policy in policies)


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


def test_opencode_auxiliary_execution_is_disabled_and_steps_are_bounded(settings):
    payload = inline_config(settings, authority_profile(("workspace-write",)))
    assert payload["formatter"] is False
    assert payload["snapshots"] is False
    assert payload["mcp"] == {"servers": {}}
    assert payload["plugins"] == []
    assert payload["skills"] == []
    assert payload["commands"] == {}
    assert payload["agents"]["build"]["steps"] == MAX_STEPS


def test_inline_config_is_json_serializable(settings):
    payload = inline_config(settings, authority_profile(("workspace-write",)))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert "ollama/qwen3:4b-instruct" in encoded
