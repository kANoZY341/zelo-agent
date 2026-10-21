"""Claimed Agent-bound Shell and Browser tasks honor lifecycle revocation."""

import asyncio

import pytest

from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec, WorkerRequirement
from app.agent_instances import AgentInstanceService
from app.memory import MemoryService
from app.policy import State
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    BrowserRunInput,
    ShellRunInput,
)
from app.tasks import TaskService
from tests import test_browser_core_binding as browser_binding
from tests import test_shell_core_binding as shell_binding
from tests.test_agent_execution import project
from tests.test_shell_core_binding import fake_owner, supervisor


@pytest.fixture(name="runtime")
def shell_runtime_fixture(tmp_path, monkeypatch):
    return shell_binding.runtime.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture(name="browser_setup")
def browser_setup_fixture(tmp_path):
    return browser_binding.setup.__wrapped__(tmp_path)


browser_runtime = browser_binding.runtime
browser_supervisor = browser_binding.supervisor


def shell_agent_services(database, settings, runtime):
    spec = AgentSpec(
        agent_id="shell_child",
        role="shell_child",
        objective="Run bounded Shell.",
        capabilities=("shell_execution",),
        allowed_task_kinds=("shell_run",),
        memory_namespace="agent/shell_child",
        lifecycle="active",
        worker_requirement=WorkerRequirement(role="shell", capabilities=("shell_execution",)),
    )
    factory = AgentFactory(AgentRegistry((spec,)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database, settings, agent_factory=factory, shell_supervisor=supervisor(runtime)
    )
    return instances, tasks, AgentExecutionService(database, factory, tasks)


@pytest.mark.parametrize("resume", [False, True])
def test_paused_shell_agent_cannot_publish_claimed_result(database, settings, runtime, resume):
    instances, tasks, execution = shell_agent_services(database, settings, runtime)
    owned_project = project(database)
    instance = instances.create(
        AgentInstanceCreate(agent_id="shell_child", project_id=owned_project.id)
    )
    task, created = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ShellRunInput(
                kind="shell_run", payload={"script": "printf hello"}, timeout_seconds=2
            )
        ),
        f"shell-agent-pause:{resume}",
    )
    assert created
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    if resume:
        instances.transition(instance.id, AgentInstanceTransition(action="resume"))
    tasks.finish(
        claimed,
        State.SUCCEEDED,
        result={
            "stdout": "hello",
            "stderr": "",
            "exit_code": 0,
            "duration_seconds": 0.01,
            "policy_version": claimed.shell_request.policy_version,
        },
    )
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.error == "agent_execution_invalidated"
    assert completed.result is None
    assert completed.attempts[0].result is None


def browser_agent_services(database, settings, setup):
    spec = AgentSpec(
        agent_id="browser_child",
        role="browser_child",
        objective="Read bounded documents.",
        capabilities=("browser_document",),
        allowed_task_kinds=("browser_run",),
        memory_namespace="agent/browser_child",
        lifecycle="active",
        worker_requirement=WorkerRequirement(role="browser", capabilities=("browser_document",)),
    )
    factory = AgentFactory(AgentRegistry((spec,)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database,
        settings,
        agent_factory=factory,
        browser_supervisor=browser_supervisor(browser_runtime(setup)),
    )
    return instances, tasks, AgentExecutionService(database, factory, tasks)


@pytest.mark.parametrize("resume", [False, True])
def test_paused_browser_agent_cannot_publish_claimed_result(
    database, settings, browser_setup, resume
):
    instances, tasks, execution = browser_agent_services(database, settings, browser_setup)
    owned_project = project(database)
    instance = instances.create(
        AgentInstanceCreate(agent_id="browser_child", project_id=owned_project.id)
    )
    task, created = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=BrowserRunInput(
                kind="browser_run",
                payload={"endpoint_id": "fixture", "path": "/doc"},
                timeout_seconds=2,
            )
        ),
        f"browser-agent-pause:{resume}",
    )
    assert created
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    if resume:
        instances.transition(instance.id, AgentInstanceTransition(action="resume"))
    tasks.finish(
        claimed,
        State.SUCCEEDED,
        result={"endpoint_id": "fixture", "nodes": 3, "truncated": False, "text": "sensitive"},
    )
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.error == "agent_execution_invalidated"
    assert completed.result is None
    assert completed.attempts[0].result is None


def test_paused_claimed_shell_agent_does_not_start_guest(database, settings, runtime, monkeypatch):
    made = fake_owner(monkeypatch)
    instances, tasks, execution = shell_agent_services(database, settings, runtime)
    instance = instances.create(
        AgentInstanceCreate(agent_id="shell_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ShellRunInput(
                kind="shell_run", payload={"script": "printf hello"}, timeout_seconds=2
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    assert not tasks.worker_request_allowed(claimed)
    asyncio.run(Runner(tasks, shell_supervisor=tasks.shell_supervisor)._run(claimed))
    assert not made
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.result is None


def test_paused_claimed_browser_agent_does_not_render(database, settings, browser_setup):
    instances, tasks, execution = browser_agent_services(database, settings, browser_setup)
    instance = instances.create(
        AgentInstanceCreate(agent_id="browser_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=BrowserRunInput(
                kind="browser_run",
                payload={"endpoint_id": "fixture", "path": "/doc"},
                timeout_seconds=2,
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    assert not tasks.worker_request_allowed(claimed)
    worker = tasks.browser_supervisor.registry.get(
        claimed.browser_request.worker_selection.descriptor.worker_id
    ).worker
    asyncio.run(Runner(tasks, browser_supervisor=tasks.browser_supervisor)._run(claimed))
    assert not worker.made
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.result is None


def test_shell_pause_during_validation_never_starts_guest(database, settings, runtime, monkeypatch):
    made = fake_owner(monkeypatch)
    instances, tasks, execution = shell_agent_services(database, settings, runtime)
    instance = instances.create(
        AgentInstanceCreate(agent_id="shell_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ShellRunInput(
                kind="shell_run", payload={"script": "printf hello"}, timeout_seconds=2
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    prepare = runtime.prepare_validation

    def pause_during_validation():
        validation = prepare()
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        return validation

    monkeypatch.setattr(runtime, "prepare_validation", pause_during_validation)
    asyncio.run(Runner(tasks, shell_supervisor=tasks.shell_supervisor)._run(claimed))
    assert not made
    assert tasks.get(task.id).status == State.CANCELLED


def test_browser_pause_during_validation_never_renders(
    database, settings, browser_setup, monkeypatch
):
    instances, tasks, execution = browser_agent_services(database, settings, browser_setup)
    instance = instances.create(
        AgentInstanceCreate(agent_id="browser_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=BrowserRunInput(
                kind="browser_run",
                payload={"endpoint_id": "fixture", "path": "/doc"},
                timeout_seconds=2,
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    selected = tasks.browser_supervisor.registry.get(
        claimed.browser_request.worker_selection.descriptor.worker_id
    )
    worker = selected.worker
    matches = worker.matches
    calls = 0

    def pause_during_runtime_validation(spec):
        nonlocal calls
        calls += 1
        valid = matches(spec)
        if calls == 2:
            instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        return valid

    monkeypatch.setattr(worker, "matches", pause_during_runtime_validation)
    asyncio.run(Runner(tasks, browser_supervisor=tasks.browser_supervisor)._run(claimed))
    assert calls >= 2
    assert not worker.made
    assert tasks.get(task.id).status == State.CANCELLED


def test_shell_pause_after_guest_starts_withholds_clean_result(
    database, settings, runtime, monkeypatch
):
    made = fake_owner(monkeypatch)
    instances, tasks, execution = shell_agent_services(database, settings, runtime)
    instance = instances.create(
        AgentInstanceCreate(agent_id="shell_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ShellRunInput(
                kind="shell_run", payload={"script": "printf hello"}, timeout_seconds=2
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    from app.shell_worker import ShellOwner

    probe = ShellOwner.run_probe

    async def pause_after_probe(owner, script, **kwargs):
        result = await probe(owner, script, **kwargs)
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        return result

    monkeypatch.setattr(ShellOwner, "run_probe", pause_after_probe)
    asyncio.run(Runner(tasks, shell_supervisor=tasks.shell_supervisor)._run(claimed))
    assert len(made) == 1 and made[0].cleanup_confirmed
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.error == "agent_execution_invalidated"
    assert completed.result is None


def test_browser_pause_after_render_withholds_clean_evidence(
    database, settings, browser_setup, monkeypatch
):
    instances, tasks, execution = browser_agent_services(database, settings, browser_setup)
    instance = instances.create(
        AgentInstanceCreate(agent_id="browser_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=BrowserRunInput(
                kind="browser_run",
                payload={"endpoint_id": "fixture", "path": "/doc"},
                timeout_seconds=2,
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    worker = tasks.browser_supervisor.registry.get(
        claimed.browser_request.worker_selection.descriptor.worker_id
    ).worker
    from qualification.renderer import DocumentRenderer

    render = DocumentRenderer.render

    async def pause_after_render(renderer, *args, **kwargs):
        evidence = await render(renderer, *args, **kwargs)
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        return evidence

    monkeypatch.setattr(DocumentRenderer, "render", pause_after_render)
    asyncio.run(Runner(tasks, browser_supervisor=tasks.browser_supervisor)._run(claimed))
    assert len(worker.made) == 1 and worker.made[0].owner.cleanup_confirmed
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.error == "agent_execution_invalidated"
    assert completed.result is None


def test_browser_pause_during_bundle_verification_never_starts_owner(
    database, settings, browser_setup, monkeypatch
):
    instances, tasks, execution = browser_agent_services(database, settings, browser_setup)
    instance = instances.create(
        AgentInstanceCreate(agent_id="browser_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=BrowserRunInput(
                kind="browser_run",
                payload={"endpoint_id": "fixture", "path": "/doc"},
                timeout_seconds=2,
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    worker = tasks.browser_supervisor.registry.get(
        claimed.browser_request.worker_selection.descriptor.worker_id
    ).worker
    original_factory = worker.renderer_factory
    paused = []

    def factory(*args):
        renderer = original_factory(*args)
        contract = renderer.contract

        class PausingContract:
            def __getattr__(self, name):
                return getattr(contract, name)

            def verify_bundle(self, bundle):
                if not paused:
                    paused.append(True)
                    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
                return contract.verify_bundle(bundle)

        renderer.contract = PausingContract()
        return renderer

    monkeypatch.setattr(worker, "renderer_factory", factory)
    asyncio.run(Runner(tasks, browser_supervisor=tasks.browser_supervisor)._run(claimed))
    assert paused and len(worker.made) == 1
    assert not worker.made[0].owner._used
    assert tasks.get(task.id).status == State.CANCELLED


def test_shell_pause_inside_owner_before_guest_never_launches(
    database, settings, runtime, monkeypatch
):
    made = fake_owner(monkeypatch)
    instances, tasks, execution = shell_agent_services(database, settings, runtime)
    instance = instances.create(
        AgentInstanceCreate(agent_id="shell_child", project_id=project(database).id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ShellRunInput(
                kind="shell_run", payload={"script": "printf hello"}, timeout_seconds=2
            )
        ),
        None,
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    from app.shell_worker import ShellOwner, ShellRuntimeError

    original_probe = ShellOwner.run_probe

    async def pause_before_guest(owner, script, *, authorize_action=None):
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        if authorize_action is not None and not authorize_action():
            raise ShellRuntimeError("shell_agent_execution_invalid")
        return await original_probe(owner, script)

    monkeypatch.setattr(ShellOwner, "run_probe", pause_before_guest)
    asyncio.run(Runner(tasks, shell_supervisor=tasks.shell_supervisor)._run(claimed))
    assert len(made) == 1 and not made[0].started
    assert tasks.get(task.id).status == State.CANCELLED
