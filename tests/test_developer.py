"""Approval, typed protocol and lifecycle tests; no real model service."""

import asyncio
import copy
import json
from uuid import uuid4

import pytest

from app.codex_worker import (
    CLI_VERSION,
    SUPPORTED_CLI_VERSIONS,
    CodexCliWorker,
    command_config,
    parse_jsonl,
)
from app.developer import DeveloperError
from app.developer_process import ProcessResult, environment
from app.models import Approval, Task
from app.policy import DomainError, State
from app.runner import Runner
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from tests.developer_helpers import fixture_commit


@pytest.fixture
def fake_codex(settings):
    binary = settings.runtime_dir / "fake-codex"
    binary.write_text(
        "#!/usr/bin/python3\nimport sys\n"
        "if '--version' in sys.argv:\n    print('codex-cli 0.154.0')\n"
        "else:\n    print('--ignore-user-config --ignore-rules --ephemeral --json "
        "--strict-config workspace-write --ask-for-approval --permission-profile')\n"
    )
    binary.chmod(0o700)
    host = binary.with_name("codex-code-mode-host")
    host.write_text("#!/bin/sh\nexit 0\n")
    host.chmod(0o700)
    settings.developer_codex_executable = binary
    auth = settings.runtime_dir / "fake-auth.json"
    auth.write_text("harmless-sentinel")
    settings.developer_codex_auth_file = auth
    return CodexCliWorker(settings)


def request(runtime):
    return runtime.request("Make the requested change", "zelo_core", 60)


def stream(*events):
    return b"".join(json.dumps(event, ensure_ascii=False).encode() + b"\n" for event in events)


FINAL = {
    "type": "item.completed",
    "item": {"id": "final", "type": "agent_message", "text": "Done é 🌍"},
}
DONE = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}}
LIFECYCLE = (
    {"type": "thread.started", "thread_id": "thread_fixture"},
    {"type": "turn.started"},
    {
        "type": "item.started",
        "item": {"id": "reasoning", "type": "reasoning", "text": "PRIVATE_REASONING"},
    },
    {
        "type": "item.updated",
        "item": {"id": "reasoning", "type": "reasoning", "text": "PRIVATE_REASONING"},
    },
    {
        "type": "item.completed",
        "item": {"id": "reasoning", "type": "reasoning", "text": "PRIVATE_REASONING"},
    },
    {
        "type": "item.started",
        "item": {"id": "command", "type": "command_execution", "command": "private command"},
    },
    {
        "type": "item.updated",
        "item": {
            "id": "command",
            "type": "command_execution",
            "aggregated_output": "PRIVATE_COMMAND_OUTPUT",
        },
    },
    {
        "type": "item.completed",
        "item": {"id": "file", "type": "file_change", "changes": ["private file data"]},
    },
    {
        "type": "item.completed",
        "item": {"id": "mcp", "type": "mcp_tool_call", "arguments": "PRIVATE_MCP_ARGUMENTS"},
    },
    {
        "type": "item.completed",
        "item": {"id": "web", "type": "web_search", "query": "PRIVATE_SEARCH_QUERY"},
    },
    {
        "type": "item.completed",
        "item": {"id": "todo", "type": "todo_list", "text": "PRIVATE_PLAN"},
    },
    FINAL,
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 4,
            "reasoning_output_tokens": 3,
        },
    },
)


def test_jsonl_final_unicode_and_usage(developer_case):
    summary, count, usage, nonfatal_errors = parse_jsonl(
        stream(FINAL, DONE), request(developer_case).limits
    )
    assert summary == "Done é 🌍" and count == 2 and usage["input_tokens"] == 10
    assert nonfatal_errors == 0


def test_jsonl_accepts_documented_01551_lifecycle_and_discards_item_bodies(developer_case):
    summary, count, usage, nonfatal_errors = parse_jsonl(
        stream(*LIFECYCLE), request(developer_case).limits
    )
    assert summary == "Done é 🌍"
    assert count == len(LIFECYCLE)
    assert usage == {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4}
    assert nonfatal_errors == 0
    assert "PRIVATE" not in summary


@pytest.mark.parametrize(
    "item_type",
    [
        "reasoning",
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "web_search",
        "todo_list",
    ],
)
def test_jsonl_discards_supported_non_message_item_types(developer_case, item_type):
    data = stream(
        {"type": "thread.started", "thread_id": "thread_fixture"},
        {"type": "turn.started"},
        {"type": "item.updated", "item": {"type": item_type, "text": "PRIVATE"}},
        FINAL,
        DONE,
    )
    summary, _, _, _ = parse_jsonl(data, request(developer_case).limits)
    assert summary == "Done é 🌍"


def test_jsonl_01540_retains_plan_compatibility(developer_case):
    summary, _, _, _ = parse_jsonl(
        stream(
            {"type": "item.completed", "item": {"id": "plan", "type": "plan"}},
            FINAL,
            DONE,
        ),
        request(developer_case).limits,
        "codex-cli 0.154.0",
    )
    assert summary == "Done é 🌍"


def test_jsonl_error_item_is_nonfatal_and_private(developer_case):
    secret = "token=DO_NOT_PERSIST"
    summary, count, usage, nonfatal_errors = parse_jsonl(
        stream(
            {"type": "thread.started", "thread_id": "thread_fixture"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "notice", "type": "error", "message": secret},
            },
            {"type": "item.started", "item": {"id": "work", "type": "reasoning"}},
            FINAL,
            DONE,
        ),
        request(developer_case).limits,
    )
    assert (summary, count, usage, nonfatal_errors) == ("Done é 🌍", 6, DONE["usage"], 1)
    assert secret not in repr((summary, count, usage, nonfatal_errors))


@pytest.mark.parametrize(
    "events",
    [
        (
            {"type": "item.completed", "item": {"id": "notice", "type": "error", "message": "x"}},
            DONE,
        ),
        (
            {"type": "item.completed", "item": {"id": "notice", "type": "error", "message": "x"}},
            FINAL,
        ),
        (
            {"type": "item.started", "item": {"id": "notice", "type": "error", "message": "x"}},
            FINAL,
            DONE,
        ),
        (
            {"type": "item.updated", "item": {"id": "notice", "type": "error", "message": "x"}},
            FINAL,
            DONE,
        ),
        (
            {"type": "item.completed", "item": {"id": "notice", "type": "error"}},
            FINAL,
            DONE,
        ),
    ],
)
def test_jsonl_error_item_requires_its_completed_shape(developer_case, events):
    with pytest.raises(DeveloperError):
        parse_jsonl(stream(*events), request(developer_case).limits)


@pytest.mark.parametrize("failure", [{"type": "turn.failed"}, {"type": "error"}])
def test_jsonl_terminal_failures_fail_closed(developer_case, failure):
    with pytest.raises(DeveloperError, match="codex_turn_failed"):
        parse_jsonl(
            stream({"type": "thread.started"}, {"type": "turn.started"}, failure),
            request(developer_case).limits,
        )


def test_jsonl_unknown_event_has_safe_structural_diagnostic(developer_case):
    with pytest.raises(DeveloperError, match="codex_event_unknown") as raised:
        parse_jsonl(
            stream(
                {
                    "type": "future.event",
                    "item": {"type": "unrecognized_item", "text": "PRIVATE_EVENT_BODY"},
                }
            ),
            request(developer_case).limits,
        )
    assert raised.value.diagnostic == "event_type=future.event item_type=unrecognized_item"
    assert "PRIVATE_EVENT_BODY" not in raised.value.diagnostic


def test_jsonl_unknown_event_diagnostic_redacts_unsafe_type(developer_case):
    with pytest.raises(DeveloperError, match="codex_event_unknown") as raised:
        parse_jsonl(stream({"type": "secret=must-not-persist"}), request(developer_case).limits)
    assert raised.value.diagnostic == "event_type=invalid"


def test_jsonl_requires_the_final_completed_item_to_be_an_agent_message(developer_case):
    with pytest.raises(DeveloperError, match="codex_final_missing"):
        parse_jsonl(
            stream(
                {"type": "thread.started", "thread_id": "thread_fixture"},
                {"type": "turn.started"},
                FINAL,
                {"type": "item.completed", "item": {"id": "todo", "type": "todo_list"}},
                DONE,
            ),
            request(developer_case).limits,
        )


@pytest.mark.parametrize(
    "data",
    [
        b"not json\n",
        b"{}",
        b"\xff\n",
        b"[]\n",
        b"{}\n",
        b"\n",
        stream(FINAL),
        stream(DONE),
        stream(FINAL, DONE, FINAL),
        stream({"type": "turn.failed"}),
        stream({"type": "item.completed", "item": None}),
        stream({"type": "item.completed", "item": {"type": "reasoning", "text": "private"}}, DONE),
        stream({"type": "item.completed", "item": {"type": "agent_message", "text": 2}}, DONE),
        b'{"type":"turn.started","type":"turn.completed"}\n',
        b'{"type":"turn.started","value":NaN}\n',
        stream({"type": "item.completed", "item": {"type": "unregistered_tool"}}),
    ],
)
def test_jsonl_fails_closed(developer_case, data):
    with pytest.raises(DeveloperError):
        parse_jsonl(data, request(developer_case).limits)


def test_reasoning_is_never_summary(developer_case):
    data = stream(
        {"type": "item.completed", "item": {"type": "reasoning", "text": "PRIVATE"}}, FINAL, DONE
    )
    summary, _, _, _ = parse_jsonl(data, request(developer_case).limits)
    assert "PRIVATE" not in summary


@pytest.mark.parametrize("field,value", [("events", 1), ("line_bytes", 10), ("stdout_bytes", 20)])
def test_jsonl_limits(developer_case, field, value):
    limits = request(developer_case).limits.model_copy(update={field: value})
    with pytest.raises(DeveloperError):
        parse_jsonl(stream(FINAL, DONE), limits)


@pytest.mark.parametrize("field", ["repository", "executable", "command", "path", "flags"])
def test_api_cannot_select_execution_inputs(field):
    with pytest.raises(ValueError):
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x", field: "unsafe"})


def test_validation_profile_closed():
    with pytest.raises(ValueError):
        DeveloperRunInput(
            kind="developer_run", payload={"instruction": "x", "validation_profile": "shell"}
        )


@pytest.mark.parametrize(
    "variable",
    [
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "SSH_AUTH_SOCK",
        "DOCKER_HOST",
        "AWS_TOKEN",
        "AZURE_TOKEN",
        "GOOGLE_TOKEN",
        "DATABASE_URL",
        "ZELO_OPERATOR_TOKEN",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "VIRTUAL_ENV",
    ],
)
def test_environment_allowlist(monkeypatch, variable):
    monkeypatch.setenv(variable, "harmless-sentinel")
    assert variable not in environment()
    assert environment()["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


@pytest.mark.parametrize(
    "field",
    [
        "instruction",
        "source_repository",
        "base_sha",
        "workspace_root",
        "validation_identity",
        "sandbox_version",
        "validation_version",
        "worker.config_version",
        "worker.implementation",
        "worker.cli_version",
        "worker.capability_profile",
        "worker.executable",
        "worker.executable_sha256",
        "worker.auth_location",
        "limits.overall_seconds",
        "limits.worker_seconds",
        "limits.validation_seconds",
        "limits.git_seconds",
        "limits.stdout_bytes",
        "limits.stderr_bytes",
        "limits.line_bytes",
        "limits.events",
        "limits.diff_bytes",
        "limits.stat_bytes",
        "limits.changed_files",
    ],
)
def test_approval_binds_specification(developer_case, database, field):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    assert task.status == State.AWAITING_APPROVAL
    assert service.claim() is None
    service.decide(task.approvals[0].id, "approve")
    with database.transaction() as session:
        stored = session.get(Task, task.id)
        approval = session.get(Approval, task.approvals[0].id)
        assert service._valid_action(stored, approval)
        spec = copy.deepcopy(stored.developer_spec)
        keys = field.split(".")
        target = spec if len(keys) == 1 else spec[keys[0]]
        value = target[keys[-1]]
        target[keys[-1]] = value + 1 if isinstance(value, (int, float)) else value + "changed"
        if field == "base_sha":
            target[keys[-1]] = "f" * 40
        stored.developer_spec = spec
        assert not service._valid_action(stored, approval)
    assert service.claim() is None
    assert service.get(task.id).status == State.CANCELLED


def test_runner_poll_does_not_live_revalidate_pending_developer_approval(
    developer_case, database, monkeypatch
):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("live")
        raise AssertionError("runner polling must not perform live developer revalidation")

    monkeypatch.setattr(developer_case, "approval_compatible", forbidden)

    async def exercise():
        runner = Runner(service, developer_runtime=developer_case)
        runner.start()
        await asyncio.sleep(developer_case.settings.runner_poll_seconds * 3)
        assert runner.ready
        await runner.stop()

    asyncio.run(exercise())

    current = service.get(task.id)
    assert current.status == State.AWAITING_APPROVAL
    assert current.approvals[0].status == "pending"
    assert calls == []


def test_source_head_drift_invalidates_at_operator_approval(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    (developer_case.source_repository / "test_example.py").write_text(
        "def test_example():\n    assert 2 + 2 == 4\n"
    )
    fixture_commit(developer_case.source_repository)

    with pytest.raises(DomainError, match="no longer pending|conflicts"):
        service.decide(task.approvals[0].id, "approve")

    current = service.get(task.id)
    assert current.status == State.CANCELLED
    assert current.error == "approval_invalidated"


def test_source_head_drift_after_approval_fails_before_worker_execution(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")

    (developer_case.source_repository / "test_example.py").write_text(
        "def test_example():\n    assert 3 + 3 == 6\n"
    )
    fixture_commit(developer_case.source_repository)

    execution = service.claim()
    assert execution is not None
    asyncio.run(Runner(service, developer_runtime=developer_case)._run(execution))

    current = service.get(task.id)
    assert current.status == State.FAILED
    assert current.error == "source_repository_changed"
    assert developer_case.worker.calls == []


def test_live_limit_change_invalidates_approval(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    developer_case.settings.developer_validation_timeout_seconds = 1
    service.expire_approvals()
    assert service.get(task.id).status == State.CANCELLED


@pytest.mark.parametrize("level", ["developer", "execution", "repo"])
def test_symlink_escape_rejected(developer_case, level):
    runtime = developer_case
    execution = str(uuid4())
    root = runtime.settings.workspace_root
    target = root / "developer"
    if level != "developer":
        target.mkdir()
        target = target / execution
    if level == "repo":
        target.mkdir()
        target = target / "repo"
    target.symlink_to(runtime.source_repository, target_is_directory=True)
    with pytest.raises(DeveloperError, match="workspace_escape"):
        runtime.path(execution)


def test_replacement_revalidation(developer_case):
    execution = str(uuid4())
    repo = developer_case.path(execution)
    repo.parent.mkdir(parents=True)
    repo.symlink_to(developer_case.source_repository, target_is_directory=True)
    with pytest.raises(DeveloperError):
        developer_case.verify_checkout_path(repo)


@pytest.mark.parametrize("value", ["../source", "/tmp", "..", "not-a-uuid"])
def test_execution_identifier_closed(developer_case, value):
    with pytest.raises(DeveloperError):
        developer_case.path(value)


def test_codex_missing_executable(settings):
    settings.developer_codex_executable = settings.runtime_dir / "missing"
    with pytest.raises(DeveloperError, match="unavailable"):
        CodexCliWorker(settings).identity()


@pytest.mark.parametrize(
    "version,help_text", [("codex-cli 0.1", ""), (CLI_VERSION, "missing flags")]
)
def test_codex_preflight_rejects_capabilities(fake_codex, monkeypatch, version, help_text):
    async def fake(argv, **kw):
        return ProcessResult(0, (version if "--version" in argv else help_text).encode(), b"", 0)

    monkeypatch.setattr("app.codex_worker.run_process", fake)
    with pytest.raises(DeveloperError, match="unsupported"):
        fake_codex.identity()


def test_codex_invocation_is_explicit(developer_case, fake_codex):
    worker = fake_codex
    repo = developer_case.settings.workspace_root
    auth = repo / "fake-auth.json"
    auth.write_text("sentinel")
    worker.settings.developer_codex_auth_file = auth
    with worker.bind() as bound:
        argv = bound.argv(repo)
    for flag in (
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--strict-config",
    ):
        assert flag in argv
    assert 'approval_policy="never"' in argv
    assert "permissions.zelo.network.enabled=false" in argv
    assert "--unshare-net" not in argv  # Parent service connectivity; inner commands are denied.
    assert "danger-full-access" not in str(argv)
    assert "sandbox" not in argv[argv.index("exec") + 1 :]
    assert 'inherit="none"' in str(command_config())


def test_runtime_worker_change_fails_before_execution(developer_case):
    spec = request(developer_case)
    developer_case.worker.worker_identity = spec.worker.model_copy(
        update={"cli_version": "changed"}
    )
    result = asyncio.run(developer_case.run(str(uuid4()), str(uuid4()), spec, lambda *args: None))
    assert result["error"] == "worker_configuration_changed"
    assert developer_case.worker.calls == []


def test_runtime_persists_only_safe_protocol_diagnostic(developer_case):
    runtime = developer_case
    developer_request = request(runtime)

    async def fail_with_protocol_diagnostic(*args):
        raise DeveloperError(
            "codex_event_unknown",
            diagnostic="event_type=future.event item_type=unrecognized_item",
        )

    runtime.worker.action = fail_with_protocol_diagnostic
    result = asyncio.run(
        runtime.run(str(uuid4()), str(uuid4()), developer_request, lambda *args: None)
    )
    assert result["error"] == "codex_event_unknown"
    assert result["protocol_diagnostic"] == "event_type=future.event item_type=unrecognized_item"


def test_runtime_persists_only_nonfatal_error_item_count(developer_case):
    developer_case.worker.nonfatal_error_items = 1
    result = asyncio.run(
        developer_case.run(str(uuid4()), str(uuid4()), request(developer_case), lambda *args: None)
    )
    assert result["nonfatal_error_items"] == 1
    assert "message" not in result


def test_jsonl_current_exec_rejects_collab_tool_call(developer_case):
    with pytest.raises(DeveloperError, match="codex_event_unknown"):
        parse_jsonl(
            stream(
                {"type": "item.completed", "item": {"id": "collab", "type": "collab_tool_call"}}
            ),
            request(developer_case).limits,
        )


def test_execution_reports_its_persisted_attempt_identifier(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    assert execution.attempt_id == service.get(task.id).attempts[0].id


def test_audit_response_uses_event_field(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    event = service.events(task.id, 1, 0)[0]
    assert event.event == "task_submitted"
    assert not hasattr(event, "event_type")


def test_runner_terminal_audit_is_atomic(developer_case, database, monkeypatch):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    service.finish(execution, State.FAILED, error="test_failure")
    names = [e.event for e in service.events(task.id, 100, 0)]
    assert names[-1] == "developer_failed" and "developer_completed" not in names


def test_fake_executable_preflight_detects_version(fake_codex):
    identity = fake_codex.identity()
    assert identity.cli_version in SUPPORTED_CLI_VERSIONS
    assert identity.config_version != identity.cli_version
    assert len(identity.executable_sha256) == 64


@pytest.mark.parametrize("version", sorted(SUPPORTED_CLI_VERSIONS))
def test_supported_codex_versions_are_explicitly_accepted(fake_codex, monkeypatch, version):
    async def fake(argv, **kwargs):
        output = (
            version
            if "--version" in argv
            else (
                "--ignore-user-config --ignore-rules --ephemeral --json "
                "--strict-config workspace-write --ask-for-approval --permission-profile"
            )
        )
        return ProcessResult(0, output.encode(), b"", 0)

    monkeypatch.setattr("app.codex_worker.run_process", fake)
    assert fake_codex.identity().cli_version == version


def test_unsafe_codex_boundary_fails_closed(fake_codex, developer_case, monkeypatch):
    async def unsafe(*args, **kwargs):
        return ProcessResult(0, b'{"auth_denied":false}\n', b"", 0)

    monkeypatch.setattr("app.codex_worker.run_process", unsafe)
    with pytest.raises(DeveloperError, match="boundary_unverified"):
        asyncio.run(
            fake_codex.probe(
                developer_case.settings.workspace_root,
                fake_codex.settings.developer_codex_auth_file,
            )
        )


@pytest.mark.parametrize(
    "exit_code,output,error",
    [
        (7, b"", "codex_nonzero_exit"),
        (0, b"not-json\n", "codex_jsonl_invalid"),
        (
            0,
            stream(
                {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}}, DONE
            ),
            "codex_final_missing",
        ),
    ],
)
def test_codex_failures_preserve_exit_status(
    fake_codex, developer_case, monkeypatch, exit_code, output, error
):
    approved = request(developer_case).model_copy(update={"worker": fake_codex.identity()})

    async def safe_probe(*args):
        pass

    async def fake_process(*args, **kwargs):
        assert kwargs["stdin"].startswith(b"You are Zelo")
        assert kwargs["env"].get("ZELO_OPERATOR_TOKEN") is None
        return ProcessResult(exit_code, output, b"do not persist raw stderr", 0)

    monkeypatch.setattr("app.codex_worker.run_process", fake_process)
    with fake_codex.bind(approved.worker) as bound:
        monkeypatch.setattr(bound, "probe", safe_probe)
        with pytest.raises(DeveloperError, match=error) as raised:
            asyncio.run(bound.run(approved, developer_case.settings.workspace_root))
    assert raised.value.exit_code == exit_code
