"""Read-only Agent Computer Worker execution and revocation tests."""

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_computers import ComputerControlState
from app.agent_execution import AgentExecutionService
from app.agent_factory import (
    AgentComputerSpec,
    AgentFactory,
    AgentRegistry,
    AgentSpec,
)
from app.agent_instances import AgentInstanceService
from app.computer_worker import ComputerRuntime, ComputerRuntimeError
from app.memory import MemoryService
from app.models import AgentComputerPermission, utcnow
from app.policy import DomainError, State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentTaskCreate,
    ComputerRunInput,
    ProjectCreate,
)
from app.tasks import TaskService, _project_task_result
from app.worker_registry import (
    Supervisor,
    WorkerRegistry,
    descriptor_for_computer_worker,
)


class FakeReadBackend:
    configured = True

    def __init__(self) -> None:
        self.identity = "a" * 64
        self.control_state = ComputerControlState(
            holder="bot",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=False,
        )
        self.calls: list[tuple[str, str, str]] = []
        self.responses: dict[str, object] = {
            "browser_snapshot": {
                "snapshotId": 1,
                "url": "about:blank",
                "title": "",
                "elements": [],
                "truncated": False,
                "challenge": None,
            },
            "browser_read": {
                "url": "about:blank",
                "title": "",
                "text": "",
                "truncated": False,
                "challenge": None,
            },
            "files_list": {
                "path": ".",
                "entries": [{"path": "notes.txt", "kind": "file", "bytes": 5}],
                "truncated": False,
            },
            "files_read": {
                "path": "notes.txt",
                "text": "hello",
                "truncated": False,
                "bytes": 5,
            },
        }

    async def control(self, instance_id: str):
        self.calls.append(("control", instance_id, ""))
        return self.control_state

    async def read_action(self, instance_id: str, action: str, *, path: str = ""):
        self.calls.append((action, instance_id, path))
        return self.responses[action]


def computer_agent_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="computer_agent",
        role="computer_agent",
        objective="Read bounded evidence from one persistent computer.",
        capabilities=("computer_read",),
        allowed_task_kinds=("computer_run",),
        procedures=("computer_observation",),
        skill_ids=("computer_observation",),
        memory_namespace="agent/computer_agent",
        computer=AgentComputerSpec(
            browser=True,
            files=True,
            shell=False,
            human_takeover=True,
        ),
        lifecycle="active",
    )


def setup(database, settings):
    backend = FakeReadBackend()
    runtime = ComputerRuntime(database, backend)
    registry = WorkerRegistry(((descriptor_for_computer_worker(), runtime),))
    supervisor = Supervisor(registry)
    factory = AgentFactory(AgentRegistry((computer_agent_spec(),)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database,
        settings,
        agent_factory=factory,
        computer_supervisor=supervisor,
    )
    execution = AgentExecutionService(database, factory, tasks)
    project = ProjectService(database).create(
        ProjectCreate(name="Computer worker", summary="Read-only computer worker tests.")
    )
    instance = instances.create(
        AgentInstanceCreate(agent_id="computer_agent", project_id=project.id)
    )
    return backend, runtime, supervisor, tasks, execution, instance


def grant(database, instance_id: str, *, browser=False, files=False):
    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance_id)
        assert row is not None
        row.enabled = True
        row.browser = browser
        row.files = files
        row.updated_at = utcnow()
        session.flush()


def test_computer_task_input_rejects_escape_and_wrong_action_path():
    with pytest.raises(ValidationError, match="computer_path_invalid"):
        ComputerRunInput(
            kind="computer_run",
            payload={"action": "files_read", "path": "../secret"},
        )
    with pytest.raises(ValidationError, match="computer_file_path_required"):
        ComputerRunInput(
            kind="computer_run",
            payload={"action": "files_read", "path": ""},
        )
    with pytest.raises(ValidationError, match="computer_browser_path_forbidden"):
        ComputerRunInput(
            kind="computer_run",
            payload={"action": "browser_read", "path": "notes.txt"},
        )


def test_generic_task_submission_cannot_use_computer_worker(database, settings):
    _backend, _runtime, _supervisor, tasks, _execution, _instance = setup(database, settings)
    with pytest.raises(DomainError, match="requires Agent execution"):
        tasks.submit(
            ComputerRunInput(
                kind="computer_run",
                payload={"action": "browser_read"},
            ),
            None,
        )


def test_agent_submission_does_not_consume_domain_worker_requirement(database, settings):
    backend, _runtime, _supervisor, tasks, execution, instance = setup(database, settings)
    assert computer_agent_spec().worker_requirement is None
    grant(database, instance.id, browser=True)

    task, created = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "browser_read"},
            )
        ),
        "computer-read:bind",
    )

    assert created is True
    assert task.status == State.PENDING
    assert task.computer_spec is not None
    assert task.computer_spec.agent_instance_id == instance.id
    assert task.computer_spec.action == "browser_read"
    assert task.computer_spec.backend_identity == backend.identity
    assert task.computer_spec.worker_selection.requested_role == "computer"
    assert task.computer_spec.worker_selection.requested_capabilities == ("computer_read",)

    names = [event.event for event in tasks.events(task.id, 100, 0)]
    assert "agent_execution_bound" in names
    assert "computer_permission_bound" in names


def test_permission_revocation_after_submit_cancels_before_attempt(database, settings):
    _backend, _runtime, _supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, browser=True)
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "browser_snapshot"},
            )
        ),
        "computer-read:revoke",
    )

    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance.id)
        assert row is not None
        row.browser = False
        row.updated_at = utcnow()

    assert tasks.claim() is None
    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "computer_execution_spec_invalid"
    assert cancelled.attempts == []


def test_backend_identity_change_after_submit_cancels_before_attempt(database, settings):
    backend, _runtime, _supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, files=True)
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "files_list"},
            )
        ),
        "computer-read:backend-drift",
    )
    backend.identity = "b" * 64

    assert tasks.claim() is None
    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "computer_execution_spec_invalid"


def test_runtime_blocks_human_control_without_reading(database, settings):
    backend, runtime, supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, files=True)
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "files_read", "path": "notes.txt"},
            )
        ),
        "computer-read:human",
    )
    claimed = tasks.claim()
    assert claimed is not None
    assert claimed.computer_request is not None

    backend.control_state = ComputerControlState(
        holder="human",
        requested=False,
        transitioning=False,
        resumeSnapshotRequired=False,
    )
    runner = Runner(tasks, computer_supervisor=supervisor)
    asyncio.run(runner._run(claimed))

    failed = tasks.get(task.id)
    assert failed.status == State.FAILED
    assert failed.error == "computer_human_control_active"
    assert not any(call[0] == "files_read" for call in backend.calls)
    assert runtime.matches(claimed.computer_request) is True


def test_runner_executes_read_and_marks_local_truncation(database, settings):
    backend, _runtime, supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, files=True)
    backend.responses["files_read"] = {
        "path": "notes.txt",
        "text": "x" * 70_000,
        "truncated": False,
        "bytes": 70_000,
    }
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "files_read", "path": "notes.txt"},
            )
        ),
        "computer-read:run",
    )
    claimed = tasks.claim()
    assert claimed is not None

    runner = Runner(tasks, computer_supervisor=supervisor)
    asyncio.run(runner._run(claimed))

    completed = tasks.get(task.id)
    assert completed.status == State.SUCCEEDED
    assert completed.result is not None
    result = completed.result["files_read"]
    assert len(result["text"]) == 64_000
    assert result["truncated"] is True
    assert result["bytes"] == 70_000

    names = [event.event for event in tasks.events(task.id, 100, 0)]
    assert "computer_permission_revalidated" in names
    assert "computer_read_started" in names
    assert "computer_read_completed" in names
    assert "computer_completed" in names


def test_runtime_rejects_unbounded_upstream_identity_fields(database, settings):
    backend, runtime, _supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, files=True)
    backend.responses["files_list"] = {
        "path": ".",
        "entries": [{"path": "x" * 1025, "kind": "file", "bytes": 1}],
        "truncated": False,
    }
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "files_list"},
            )
        ),
        "computer-read:bad-upstream",
    )
    claimed = tasks.claim()
    assert claimed is not None
    assert claimed.computer_request is not None

    with pytest.raises(ComputerRuntimeError, match="computer_response_invalid"):
        asyncio.run(
            runtime.run(
                spec=claimed.computer_request,
                action="files_list",
                path="",
                timeout_seconds=claimed.timeout_seconds,
                emit=lambda *_args: None,
            )
        )


def test_database_requires_agent_identity_for_computer_task(database):
    with pytest.raises(IntegrityError, match="Computer execution requires Agent identity"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id, kind, payload, timeout_seconds, status, action_version,
                        action_hash, request_hash, cancel_requested, created_at, updated_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000777',
                        'computer_run',
                        '{"action":"files_list","path":""}',
                        10, 'pending', 1, :action_hash, :request_hash, 0,
                        '2026-10-05 00:00:00', '2026-10-05 00:00:00'
                    )
                    """
                ),
                {"action_hash": "a" * 64, "request_hash": "b" * 64},
            )


def test_database_keeps_computer_execution_spec_immutable(database, settings):
    _backend, _runtime, _supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, files=True)
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={"action": "files_list"},
            )
        ),
        "computer-read:immutable",
    )
    assert task.computer_spec is not None

    with pytest.raises(IntegrityError, match="Computer execution identity is immutable"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE tasks SET computer_spec='{}' WHERE id=:task_id"),
                {"task_id": task.id},
            )


def test_computer_result_projection_excludes_private_content():
    result = {
        "action": "files_read",
        "agent_instance_id": "00000000-0000-4000-8000-000000000001",
        "files_read": {
            "path": "private/notes.txt",
            "text": "PRIVATE-COMPUTER-CONTENT",
            "truncated": False,
            "bytes": 24,
        },
        "completed_at": "2026-10-05T00:00:00Z",
    }

    projection, truncated = _project_task_result("computer_run", result, 4096)
    assert truncated is True
    assert projection == {
        "action": "files_read",
        "agent_instance_id": "00000000-0000-4000-8000-000000000001",
        "files_read": {
            "path": "private/notes.txt",
            "bytes": 24,
            "text_chars": 24,
            "truncated": False,
        },
    }
    assert "PRIVATE-COMPUTER-CONTENT" not in str(projection)
