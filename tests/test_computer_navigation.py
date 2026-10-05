"""Named-endpoint Agent Computer navigation execution tests."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent_computers import ComputerControlState
from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentComputerSpec, AgentFactory, AgentRegistry, AgentSpec
from app.agent_instances import AgentInstanceService
from app.api_contracts import ApiEndpointRegistry
from app.computer_worker import ComputerRuntime
from app.memory import MemoryService
from app.models import AgentComputerPermission, utcnow
from app.policy import State
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
    descriptor_for_computer_navigate_worker,
    descriptor_for_computer_worker,
)


class FakeNavigationBackend:
    configured = True

    def __init__(self) -> None:
        self.identity = "a" * 64
        self.settings = SimpleNamespace(
            computer_internal_network_cidr="172.30.250.0/24",
            computer_egress_proxy_host="172.30.250.1",
        )
        self.control_state = ComputerControlState(
            holder="bot",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=False,
        )
        self.calls: list[tuple[str, str, str]] = []
        self.navigation_response: dict[str, object] = {
            "url": "https://example.com/docs",
            "title": "Example Docs",
            "text": "PRIVATE-NAVIGATION-CONTENT",
            "truncated": False,
            "elapsedMs": 12,
            "challenge": None,
        }

    async def control(self, instance_id: str):
        self.calls.append(("control", instance_id, ""))
        return self.control_state

    async def navigate_action(self, instance_id: str, url: str):
        self.calls.append(("browser_navigate", instance_id, url))
        return self.navigation_response


def navigation_agent_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="computer_navigator",
        role="computer_navigator",
        objective="Navigate one persistent browser only to trusted endpoints.",
        capabilities=("computer_navigate", "computer_read"),
        allowed_task_kinds=("computer_run",),
        procedures=("computer_navigation", "computer_observation"),
        skill_ids=("computer_navigation", "computer_observation"),
        memory_namespace="agent/computer_navigator",
        computer=AgentComputerSpec(
            browser=True,
            files=True,
            human_takeover=True,
            browser_endpoint_ids=("public_site",),
        ),
        lifecycle="active",
    )


def setup_navigation(database, settings):
    backend = FakeNavigationBackend()
    endpoints = ApiEndpointRegistry.from_entries(("public_site=https://example.com",))
    runtime = ComputerRuntime(database, backend, endpoints)
    registry = WorkerRegistry(
        (
            (descriptor_for_computer_worker(), runtime),
            (descriptor_for_computer_navigate_worker(), runtime),
        )
    )
    supervisor = Supervisor(registry)
    factory = AgentFactory(AgentRegistry((navigation_agent_spec(),)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database,
        settings,
        agent_factory=factory,
        computer_supervisor=supervisor,
    )
    execution = AgentExecutionService(database, factory, tasks)
    project = ProjectService(database).create(
        ProjectCreate(
            name="Computer navigation",
            summary="Named-endpoint navigation tests.",
        )
    )
    instance = instances.create(
        AgentInstanceCreate(
            agent_id="computer_navigator",
            project_id=project.id,
        )
    )
    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance.id)
        assert row is not None
        row.enabled = True
        row.browser = True
        row.updated_at = utcnow()
    return backend, endpoints, runtime, supervisor, tasks, execution, instance


def navigation_task(path: str = "/docs") -> AgentTaskCreate:
    return AgentTaskCreate(
        task=ComputerRunInput(
            kind="computer_run",
            payload={
                "action": "browser_navigate",
                "endpoint_id": "public_site",
                "path": path,
            },
        )
    )


def test_navigation_input_never_accepts_arbitrary_url_or_read_endpoint():
    with pytest.raises(ValidationError, match="computer_navigation_endpoint_required"):
        ComputerRunInput(
            kind="computer_run",
            payload={"action": "browser_navigate", "path": "/"},
        )

    for path in (
        "https://evil.example/",
        "/docs?token=value",
        "/docs#fragment",
        "//evil.example/",
        "/../admin",
    ):
        with pytest.raises(ValidationError):
            ComputerRunInput(
                kind="computer_run",
                payload={
                    "action": "browser_navigate",
                    "endpoint_id": "public_site",
                    "path": path,
                },
            )

    with pytest.raises(ValidationError, match="computer_read_endpoint_forbidden"):
        ComputerRunInput(
            kind="computer_run",
            payload={
                "action": "browser_read",
                "endpoint_id": "public_site",
            },
        )


def test_submission_binds_navigation_worker_endpoint_and_egress_policy(database, settings):
    (
        backend,
        endpoints,
        _runtime,
        _supervisor,
        _tasks,
        execution,
        instance,
    ) = setup_navigation(database, settings)

    task, created = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:bind",
    )

    assert created is True
    assert task.status == State.PENDING
    assert task.computer_spec is not None
    assert task.computer_spec.action == "browser_navigate"
    assert task.computer_spec.agent_instance_id == instance.id
    assert task.computer_spec.backend_identity == backend.identity
    assert task.computer_spec.endpoint == endpoints.bind("public_site")
    assert task.computer_spec.egress_policy_version is not None
    assert task.computer_spec.worker_selection.descriptor.worker_id == ("computer_navigate_core")
    assert task.computer_spec.worker_selection.requested_capabilities == ("computer_navigate",)


def test_navigation_permission_revocation_cancels_before_attempt(database, settings):
    _backend, _endpoints, _runtime, _supervisor, tasks, execution, instance = setup_navigation(
        database, settings
    )
    task, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:revoke",
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


def test_permission_revocation_after_navigation_blocks_the_next_task(database, settings):
    backend, _endpoints, _runtime, supervisor, tasks, execution, instance = setup_navigation(
        database, settings
    )
    first, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:before-revoke",
    )
    first_claimed = tasks.claim()
    assert first_claimed is not None
    asyncio.run(Runner(tasks, computer_supervisor=supervisor)._run(first_claimed))
    assert tasks.get(first.id).status == State.SUCCEEDED

    second, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:after-revoke",
    )
    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance.id)
        assert row is not None
        row.browser = False
        row.updated_at = utcnow()

    assert tasks.claim() is None
    cancelled = tasks.get(second.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "computer_execution_spec_invalid"
    assert cancelled.attempts == []
    assert sum(call[0] == "browser_navigate" for call in backend.calls) == 1


def test_endpoint_registry_drift_cancels_before_attempt(database, settings):
    _backend, _endpoints, runtime, _supervisor, tasks, execution, instance = setup_navigation(
        database, settings
    )
    task, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:endpoint-drift",
    )

    runtime.endpoints = ApiEndpointRegistry.from_entries(("public_site=https://example.org",))

    assert tasks.claim() is None
    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "computer_execution_spec_invalid"
    assert cancelled.attempts == []


def test_runner_executes_named_navigation_only(database, settings):
    backend, _endpoints, _runtime, supervisor, tasks, execution, instance = setup_navigation(
        database, settings
    )
    task, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:run",
    )
    claimed = tasks.claim()
    assert claimed is not None
    assert claimed.computer_request is not None

    runner = Runner(tasks, computer_supervisor=supervisor)
    asyncio.run(runner._run(claimed))

    completed = tasks.get(task.id)
    assert completed.status == State.SUCCEEDED
    assert completed.result is not None
    result = completed.result["browser_navigate"]
    assert result["endpoint_id"] == "public_site"
    assert result["url"] == "https://example.com/docs"
    assert result["title"] == "Example Docs"
    assert result["text"] == "PRIVATE-NAVIGATION-CONTENT"
    assert ("browser_navigate", instance.id, "https://example.com/docs") in backend.calls

    names = [event.event for event in tasks.events(task.id, 100, 0)]
    assert "computer_navigation_started" in names
    assert "computer_navigation_completed" in names
    assert "computer_completed" in names


def test_runner_rejects_cross_origin_navigation_result(database, settings):
    backend, _endpoints, _runtime, supervisor, tasks, execution, instance = setup_navigation(
        database, settings
    )
    backend.navigation_response = {
        "url": "https://evil.example/docs",
        "title": "Redirected",
        "text": "should be rejected",
        "truncated": False,
        "elapsedMs": 12,
        "challenge": None,
    }
    task, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:cross-origin",
    )
    claimed = tasks.claim()
    assert claimed is not None

    runner = Runner(tasks, computer_supervisor=supervisor)
    asyncio.run(runner._run(claimed))

    failed = tasks.get(task.id)
    assert failed.status == State.FAILED
    assert failed.error == "computer_navigation_final_origin_denied"


def test_human_takeover_blocks_navigation_before_backend_action(database, settings):
    backend, _endpoints, _runtime, supervisor, tasks, execution, instance = setup_navigation(
        database, settings
    )
    task, _ = execution.submit(
        instance.id,
        navigation_task(),
        "computer-nav:human",
    )
    claimed = tasks.claim()
    assert claimed is not None

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
    assert not any(call[0] == "browser_navigate" for call in backend.calls)


def test_navigation_projection_excludes_page_content():
    result = {
        "action": "browser_navigate",
        "agent_instance_id": "00000000-0000-4000-8000-000000000001",
        "browser_navigate": {
            "endpoint_id": "public_site",
            "url": "https://example.com/docs",
            "title": "Example Docs",
            "text": "PRIVATE-NAVIGATION-CONTENT",
            "truncated": False,
            "elapsed_ms": 12,
            "challenge_present": False,
        },
        "completed_at": "2026-10-05T00:00:00Z",
    }

    projection, truncated = _project_task_result("computer_run", result, 4096)
    assert truncated is True
    assert projection["browser_navigate"]["endpoint_id"] == "public_site"
    assert projection["browser_navigate"]["text_chars"] == 26
    assert "text" not in projection["browser_navigate"]
    assert "PRIVATE-NAVIGATION-CONTENT" not in str(projection)
