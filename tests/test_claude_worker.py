"""Claude Code adapter policy and protocol tests; no external Claude service calls."""

import asyncio
import json

import pytest

from app.claude_worker import (
    CAPABILITY_PROFILE,
    ClaudeCodeWorker,
    configuration_version,
    parse_cli_version,
    parse_json_output,
)
from app.developer import DeveloperError
from app.developer_contracts import Limits
from app.developer_process import ProcessResult
from app.worker_authority import authority_profile, claude_code_permission_semantics


def limits() -> Limits:
    return Limits(
        overall_seconds=30,
        worker_seconds=10,
        validation_seconds=10,
        git_seconds=5,
        stdout_bytes=65536,
        stderr_bytes=4096,
        line_bytes=4096,
        events=32,
        diff_bytes=4096,
        changed_files=20,
    )


def configured_worker(settings, monkeypatch):
    binary = settings.runtime_dir / "fake-claude"
    binary.write_bytes(b"fake claude executable")
    binary.chmod(0o700)
    token = settings.runtime_dir / "claude-token"
    token.write_text("test-only-high-entropy-token-value")
    token.chmod(0o600)
    configured = settings.model_copy(
        update={
            "developer_claude_executable": binary,
            "developer_claude_oauth_token_file": token,
            "developer_claude_model": "claude-sonnet-5",
        }
    )

    def fake_sandbox(_repo, command, **_kwargs):
        return command

    async def fake_process(argv, **_kwargs):
        assert "--version" in argv
        return ProcessResult(0, b"2.1.275 (Claude Code)\n", b"", 0.01)

    monkeypatch.setattr("app.claude_worker.external_worker_sandbox", fake_sandbox)
    monkeypatch.setattr("app.claude_worker.run_process", fake_process)
    return ClaudeCodeWorker(configured)


def test_claude_identity_is_generic_and_configuration_bound(settings, monkeypatch):
    worker = configured_worker(settings, monkeypatch)
    identity = worker.identity()
    assert identity.implementation == "claude_code"
    assert identity.cli_version == "2.1.275"
    assert identity.capability_profile == CAPABILITY_PROFILE
    assert [item.name for item in identity.artifacts] == ["executable"]
    assert [item.name for item in identity.credential_locations] == ["oauth_token"]
    changed = worker.settings.model_copy(update={"developer_claude_model": "claude-opus-5"})
    assert configuration_version(changed) != identity.config_version


@pytest.mark.parametrize("raw", [b"2.1.258\n", b"2.2.0\n", b"not-a-version\n"])
def test_claude_unsupported_versions_fail_closed(raw):
    with pytest.raises(DeveloperError, match="version"):
        parse_cli_version(raw)


def test_claude_argv_hardens_unattended_execution(settings, monkeypatch, tmp_path):
    worker = configured_worker(settings, monkeypatch)
    approved = worker.identity()
    captured = {}

    def capture(repo, command, **kwargs):
        captured["repo"] = repo
        captured["command"] = command
        captured["kwargs"] = kwargs
        return command

    monkeypatch.setattr("app.claude_worker.external_worker_sandbox", capture)
    with worker.bind(approved) as bound:
        argv = bound.argv(tmp_path, authority_profile(("workspace-write",)))

    command = captured["command"]
    assert argv == command
    for flag in (
        "--bare",
        "--restricted",
        "--no-session-persistence",
        "--permission-mode",
        "--permission-prompts",
        "--tools",
        "--allowedTools",
        "--disallowedTools",
        "--settings",
        "--model",
        "--json-schema",
    ):
        assert flag in command
    settings_payload = json.loads(command[command.index("--settings") + 1])
    assert settings_payload["sandbox"] == {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "network": {"allowedDomains": [], "strictAllowlist": True},
    }
    assert settings_payload["permissions"]["blockReadsOutsideWorkingDirectories"] is True
    assert captured["kwargs"]["writable"] is True
    assert captured["kwargs"]["service_network"] is True
    assert str(worker.settings.developer_claude_oauth_token_file) not in " ".join(command)


def test_reviewer_tool_policy_removes_mutating_file_tools():
    semantics = claude_code_permission_semantics(authority_profile(("workspace-read",)))
    assert "Edit" not in semantics["tools"]
    assert "Write" not in semantics["tools"]
    assert semantics["settings"]["sandbox"]["network"]["strictAllowlist"] is True
    assert semantics["settings"]["sandbox"]["network"]["allowedDomains"] == []


def test_claude_json_output_is_bounded_and_discards_unstructured_result():
    payload = {
        "result": "untrusted verbose body is ignored",
        "structured_output": {"summary": "implemented safely"},
        "num_turns": 3,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "permission_denials": [],
    }
    summary, turns, usage = parse_json_output(json.dumps(payload).encode(), limits())
    assert summary == "implemented safely"
    assert turns == 3
    assert usage == {"input_tokens": 10, "output_tokens": 5}


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"structured_output": {"summary": ""}}, "summary"),
        (
            {
                "structured_output": {"summary": "x"},
                "permission_denials": [{"tool": "Bash"}],
            },
            "permission_denied",
        ),
        ({"is_error": True, "structured_output": {"summary": "x"}}, "turn_failed"),
    ],
)
def test_claude_json_output_fails_closed(payload, error):
    with pytest.raises(DeveloperError, match=error):
        parse_json_output(json.dumps(payload).encode(), limits())


def test_claude_preflight_never_needs_service_network(settings, monkeypatch):
    worker = configured_worker(settings, monkeypatch)
    seen = {}

    def capture(repo, command, **kwargs):
        seen.update(kwargs)
        return command

    async def fake_process(argv, **_kwargs):
        return ProcessResult(0, b"2.1.275\n", b"", 0.01)

    monkeypatch.setattr("app.claude_worker.external_worker_sandbox", capture)
    monkeypatch.setattr("app.claude_worker.run_process", fake_process)
    identity = asyncio.run(worker.preflight())
    assert identity.implementation == "claude_code"
    assert seen["service_network"] is False
    assert seen["writable"] is False
