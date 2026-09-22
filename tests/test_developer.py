"""Approval, typed protocol and lifecycle tests; no real model service."""

import asyncio
import copy
import json
import threading
import time
from uuid import uuid4

import pytest

from app.codex_worker import (
    CLI_VERSION,
    SUPPORTED_CLI_VERSIONS,
    CodexCliWorker,
    command_config,
    file_digest,
    parse_jsonl,
)
from app.developer import DeveloperError
from app.developer_process import ProcessResult, environment
from app.developer_sandbox import CODEX, ENGINE, git_argv, sandbox, sandbox_policy_version
from app.models import Approval, Task
from app.policy import DomainError, State
from app.runner import Runner
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from app.worker_authority import (
    WorkerAuthorityProfile,
    authority_policy_version,
    authority_profile,
)
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


def test_codex_identity_uses_generic_artifacts_and_auth(fake_codex):
    identity = asyncio.run(fake_codex.preflight())
    executable = fake_codex.settings.developer_codex_executable.resolve()
    host = executable.with_name("codex-code-mode-host").resolve()

    assert identity.artifact("executable").path == str(executable)
    assert identity.artifact("executable").sha256 == file_digest(executable)
    assert identity.artifact("code_mode_host").path == str(host)
    assert identity.artifact("code_mode_host").sha256 == file_digest(host)
    assert identity.credential_location("auth").path == str(
        fake_codex.settings.developer_codex_auth_file.absolute()
    )

    persisted = identity.model_dump(mode="json")
    assert "executable" not in persisted
    assert "code_mode_host" not in persisted
    assert "auth_location" not in persisted


def request(runtime):
    return runtime.request("Make the requested change", "zelo_core", 60)


def stream(*events):
    return b"".join(json.dumps(event, ensure_ascii=False).encode() + b"\n" for event in events)


def _mount_mode(argv: list[str], dest: str) -> str | None:
    for index in range(2, len(argv)):
        if argv[index] == dest and argv[index - 2] in {"--bind", "--ro-bind"}:
            return argv[index - 2]
    return None


def _has_arg(argv: list[str], flag: str) -> bool:
    return flag in argv


def _chdir_target(argv: list[str]) -> str | None:
    for index in range(len(argv) - 1):
        if argv[index] == "--chdir":
            return argv[index + 1]
    return None


def _patch_sandbox_plan(monkeypatch, mutate):
    from app import developer_sandbox

    original = developer_sandbox.sandbox_plan

    def wrapped(**kwargs):
        plan = copy.deepcopy(original(**kwargs))
        mutate(plan, kwargs)
        return plan

    monkeypatch.setattr(developer_sandbox, "sandbox_plan", wrapped)


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


def test_codex_command_config_derives_filesystem_and_network_from_authority():
    read_only = WorkerAuthorityProfile(
        name="test-read-only",
        authority=("test-read-only",),
        workspace_write=False,
        network=False,
    )
    config = command_config(read_only)
    joined = " ".join(config)
    assert '"/workspace"="read"' in joined
    assert "permissions.zelo.network.enabled=false" in config


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


def test_authority_fingerprint_changes_when_auth_bind_widens(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    profile = authority_profile(("workspace-write",))
    auth = runtime.settings.workspace_root / "auth.json"
    auth.write_text("fixture-auth")
    baseline_argv = sandbox(
        runtime.source_repository,
        [CODEX],
        writable=profile.workspace_write,
        codex_fd=11,
        code_mode_host_fd=12,
        auth=auth,
        service_network=True,
    )
    baseline_version = approved.worker_selection.authority_policy_version

    def mutate(plan, _kwargs):
        for operation in plan["operations"]:
            if operation.get("dest") == ENGINE + "/auth.json":
                operation["op"] = "--bind"

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(
        runtime.source_repository,
        [CODEX],
        writable=profile.workspace_write,
        codex_fd=11,
        code_mode_host_fd=12,
        auth=auth,
        service_network=True,
    )
    assert _mount_mode(baseline_argv, ENGINE + "/auth.json") == "--ro-bind"
    assert _mount_mode(changed_argv, ENGINE + "/auth.json") == "--bind"
    assert authority_policy_version() != baseline_version
    assert not runtime.supervisor.matches(approved.worker_selection)


def test_sandbox_version_changes_when_validation_venv_bind_widens(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    venv = runtime.settings.workspace_root / "trusted-venv"
    venv.mkdir()
    baseline_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    baseline_version = approved.sandbox_version

    def mutate(plan, _kwargs):
        for operation in plan["operations"]:
            if operation.get("source") == "$VENV":
                operation["op"] = "--bind"

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    assert _mount_mode(baseline_argv, "/opt/zelo-venv") == "--ro-bind"
    assert _mount_mode(changed_argv, "/opt/zelo-venv") == "--bind"
    assert sandbox_policy_version() != baseline_version
    assert runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


def test_sandbox_version_changes_when_git_inspection_workspace_widens(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    baseline_argv = sandbox(
        runtime.source_repository,
        git_argv("status"),
        writable=False,
        git_inspection=True,
    )
    baseline_version = approved.sandbox_version

    def mutate(plan, kwargs):
        if kwargs["git_inspection"]:
            for operation in plan["operations"]:
                if operation.get("dest") == "/workspace" and operation.get("source") == "$REPO":
                    operation["op"] = "--bind"

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(
        runtime.source_repository,
        git_argv("status"),
        writable=False,
        git_inspection=True,
    )
    assert _mount_mode(baseline_argv, "/workspace") == "--ro-bind"
    assert _mount_mode(changed_argv, "/workspace") == "--bind"
    assert sandbox_policy_version() != baseline_version
    assert runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


def test_sandbox_version_changes_when_extra_host_bind_is_added(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    baseline_argv = sandbox(
        runtime.source_repository,
        git_argv("status"),
        writable=False,
        git_inspection=True,
    )
    baseline_version = approved.sandbox_version

    def mutate(plan, kwargs):
        if kwargs["git_inspection"]:
            operations = list(plan["operations"])
            operations.append({"op": "--ro-bind", "source": "/", "dest": "/hostroot"})
            plan["operations"] = tuple(operations)

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(
        runtime.source_repository,
        git_argv("status"),
        writable=False,
        git_inspection=True,
    )
    assert "/hostroot" not in baseline_argv
    assert "/hostroot" in changed_argv
    assert sandbox_policy_version() != baseline_version
    assert runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


def test_sandbox_version_changes_when_isolated_network_is_dropped(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    venv = runtime.settings.workspace_root / "trusted-venv-network"
    venv.mkdir()
    baseline_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    baseline_version = approved.sandbox_version
    baseline_authority = approved.worker_selection.authority_policy_version

    def mutate(plan, kwargs):
        if not kwargs["service_network"]:
            plan["namespaces"]["network"] = False

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    assert "--unshare-net" in baseline_argv
    assert "--unshare-net" not in changed_argv
    assert sandbox_policy_version() != baseline_version
    assert authority_policy_version() != baseline_authority
    assert not runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


def test_policy_changes_when_unshare_pid_is_dropped(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    venv = runtime.settings.workspace_root / "trusted-venv-pid"
    venv.mkdir()
    baseline_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    baseline_version = approved.sandbox_version
    baseline_authority = approved.worker_selection.authority_policy_version

    def mutate(plan, _kwargs):
        plan["namespaces"]["pid"] = False

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    assert _has_arg(baseline_argv, "--unshare-pid")
    assert not _has_arg(changed_argv, "--unshare-pid")
    assert sandbox_policy_version() != baseline_version
    assert authority_policy_version() != baseline_authority
    assert not runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


def test_policy_changes_when_die_with_parent_is_dropped(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    venv = runtime.settings.workspace_root / "trusted-venv-lifecycle"
    venv.mkdir()
    baseline_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    baseline_version = approved.sandbox_version
    baseline_authority = approved.worker_selection.authority_policy_version

    def mutate(plan, _kwargs):
        plan["lifecycle"]["die_with_parent"] = False

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    assert _has_arg(baseline_argv, "--die-with-parent")
    assert not _has_arg(changed_argv, "--die-with-parent")
    assert sandbox_policy_version() != baseline_version
    assert authority_policy_version() != baseline_authority
    assert not runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


def test_policy_changes_when_sandbox_cwd_changes(developer_case, monkeypatch):
    runtime = developer_case
    approved = request(runtime)
    venv = runtime.settings.workspace_root / "trusted-venv-cwd"
    venv.mkdir()
    baseline_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    baseline_version = approved.sandbox_version
    baseline_authority = approved.worker_selection.authority_policy_version

    def mutate(plan, _kwargs):
        plan["cwd"] = "/tmp/alternate-workspace"

    _patch_sandbox_plan(monkeypatch, mutate)
    changed_argv = sandbox(runtime.source_repository, ["/usr/bin/python3"], venv=venv)
    assert _chdir_target(baseline_argv) == "/workspace"
    assert _chdir_target(changed_argv) == "/tmp/alternate-workspace"
    assert sandbox_policy_version() != baseline_version
    assert authority_policy_version() != baseline_authority
    assert not runtime.supervisor.matches(approved.worker_selection)
    assert not runtime.compatible(approved, 60)


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
        "worker.schema_version",
        "worker.config_version",
        "worker.implementation",
        "worker.cli_version",
        "worker.capability_profile",
        "worker.artifacts",
        "worker.credential_locations",
        "worker_selection.authority_profile",
        "worker_selection.authority_policy_version",
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
        if field == "worker.artifacts":
            changed = copy.deepcopy(value)
            changed[0]["sha256"] = "f" * 64
            target[keys[-1]] = changed
        elif field == "worker.credential_locations":
            changed = copy.deepcopy(value)
            changed[0]["path"] += "-changed"
            target[keys[-1]] = changed
        else:
            target[keys[-1]] = value + 1 if isinstance(value, (int, float)) else value + "changed"
        if field == "base_sha":
            target[keys[-1]] = "f" * 40
        if field == "worker_selection.authority_policy_version":
            target[keys[-1]] = "0" * 64
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
    profile = authority_profile(("workspace-write",))
    worker.settings.developer_codex_auth_file = auth
    with worker.bind() as bound:
        argv = bound.argv(repo, profile)
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
    assert 'inherit="none"' in str(command_config(profile))


def test_codex_entry_points_require_explicit_profile(developer_case, fake_codex):
    repo = developer_case.settings.workspace_root
    auth = repo / "fake-auth.json"
    auth.write_text("sentinel")
    with pytest.raises(TypeError):
        command_config()
    with fake_codex.bind() as bound:
        with pytest.raises(TypeError):
            bound.argv(repo)
        with pytest.raises(TypeError):
            asyncio.run(bound.probe(repo, auth))
        with pytest.raises(TypeError):
            asyncio.run(bound.local_command_probe(repo, auth))


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


def test_runtime_offloads_worker_binding_from_event_loop(developer_case, monkeypatch):
    entered = threading.Event()
    ticker_progress = threading.Event()
    request_spec = request(developer_case)

    async def snapshot(_request):
        return {"snapshot": "ok"}

    async def stop(*args):
        raise DeveloperError("fixture_stop")

    monkeypatch.setattr(developer_case, "primary_snapshot", snapshot)
    monkeypatch.setattr(developer_case, "prepare", stop)

    class SlowBind:
        def __enter__(self):
            entered.set()
            time.sleep(0.1)
            assert ticker_progress.wait(0.2)
            return developer_case.worker

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(developer_case.worker, "bind", lambda approved: SlowBind())

    async def exercise():
        async def ticker():
            await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(0)
            ticker_progress.set()

        run_task = asyncio.create_task(
            developer_case.run(str(uuid4()), str(uuid4()), request_spec, lambda *_: None)
        )
        await asyncio.gather(run_task, ticker())
        return await run_task

    result = asyncio.run(exercise())
    assert result["error"] == "fixture_stop"


def test_runtime_cleans_up_bind_cancelled_during_threaded_enter(developer_case, monkeypatch):
    entered = threading.Event()
    release_enter = threading.Event()
    exited = []
    request_spec = request(developer_case)

    class SlowBind:
        def __enter__(self):
            entered.set()
            assert release_enter.wait(1)
            return developer_case.worker

        def __exit__(self, exc_type, exc, tb):
            exited.append((exc_type, exc, tb))
            return False

    monkeypatch.setattr(developer_case.worker, "bind", lambda approved: SlowBind())

    async def exercise():
        run_task = asyncio.create_task(
            developer_case.run(str(uuid4()), str(uuid4()), request_spec, lambda *_: None)
        )
        await asyncio.to_thread(entered.wait, 1)
        run_task.cancel()
        release_enter.set()
        return await run_task

    result = asyncio.run(exercise())
    assert result["error"] == "developer_cancelled"
    assert result["cancelled"] is True
    assert len(exited) == 1


def test_runtime_releases_slot_before_uncancellable_unbind(developer_case, monkeypatch):
    request_spec = request(developer_case)
    worker_id = request_spec.worker_selection.descriptor.worker_id
    run_started = threading.Event()
    exit_started = threading.Event()
    release_exit = threading.Event()
    exited = []
    events = []

    async def block(request, repo):
        run_started.set()
        await asyncio.sleep(30)

    developer_case.worker.action = block
    monkeypatch.setattr(
        developer_case,
        "primary_snapshot",
        lambda _request: asyncio.sleep(0, result="ok"),
    )

    class SlowExit:
        def __enter__(self):
            return developer_case.worker

        def __exit__(self, exc_type, exc, tb):
            exited.append((exc_type, exc, tb))
            exit_started.set()
            assert release_exit.wait(1)
            return False

    monkeypatch.setattr(developer_case.worker, "bind", lambda approved: SlowExit())

    async def exercise():
        run_task = asyncio.create_task(
            developer_case.run(
                str(uuid4()),
                str(uuid4()),
                request_spec,
                lambda event, details: events.append(event),
            )
        )
        await asyncio.to_thread(run_started.wait, 1)
        run_task.cancel()
        await asyncio.to_thread(exit_started.wait, 1)
        lock = developer_case.worker_lock(worker_id, 1)
        lock.close()
        run_task.cancel()
        release_exit.set()
        return await run_task

    result = asyncio.run(exercise())
    assert result["cancelled"] is True
    assert events.count("worker_capacity_claimed") == 1
    assert events.count("worker_capacity_released") == 1
    assert len(exited) == 1


def test_release_emit_failure_still_unbinds_and_closes_artifacts(
    developer_case, database, monkeypatch
):
    runtime = developer_case
    service = TaskService(database, runtime.settings, developer_runtime=runtime)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None and execution.developer_request is not None
    exits = []
    artifacts_closed = []
    worker_id = execution.developer_request.worker_selection.descriptor.worker_id

    class TrackingBind:
        def __enter__(self):
            return runtime.worker

        def __exit__(self, exc_type, exc, tb):
            exits.append((exc_type, exc, tb))
            return False

    class TrackingExitStack:
        def __init__(self):
            self._closed = False

        def close(self):
            assert not self._closed
            self._closed = True
            artifacts_closed.append("closed")

    original_model_event = service.model_event

    def fail_release_event(task_id, event, attempt_id, **details):
        if event == "worker_capacity_released":
            raise RuntimeError("audit backend down")
        return original_model_event(task_id, event, attempt_id, **details)

    monkeypatch.setattr(runtime.worker, "bind", lambda approved: TrackingBind())
    monkeypatch.setattr("app.developer.ExitStack", TrackingExitStack)
    monkeypatch.setattr(service, "model_event", fail_release_event)

    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))

    current = service.get(task.id)
    assert current.status == State.FAILED
    assert current.error == "worker_error"
    assert current.attempts[0].outcome == State.FAILED
    assert len(exits) == 1
    assert artifacts_closed == ["closed"]
    lock = runtime.worker_lock(worker_id, 1)
    lock.close()


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
                authority_profile(("workspace-write",)),
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
