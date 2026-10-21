"""Agent-bound Python compute retains Core authority across claim and use."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec, WorkerRequirement
from app.agent_instances import AgentInstanceService
from app.memory import MemoryService
from app.policy import State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    ProjectCreate,
    PythonRunInput,
)
from app.tasks import TaskService
from tests.test_python_worker import python_supervisor


def python_agent_services(database, settings):
    spec = AgentSpec(
        agent_id="python_child",
        role="python_child",
        objective="Execute bounded Python compute.",
        capabilities=("python_execution",),
        allowed_task_kinds=("python_run",),
        memory_namespace="agent/python_child",
        lifecycle="active",
        worker_requirement=WorkerRequirement(role="python", capabilities=("python_execution",)),
    )
    factory = AgentFactory(AgentRegistry((spec,)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database, settings, agent_factory=factory, python_supervisor=python_supervisor()
    )
    return instances, tasks, AgentExecutionService(database, factory, tasks)


def claimed_python_agent(database, settings):
    instances, tasks, execution = python_agent_services(database, settings)
    project = ProjectService(database).create(
        ProjectCreate(name="Python Agent lifecycle", summary="Core controls Python execution.")
    )
    instance = instances.create(AgentInstanceCreate(agent_id="python_child", project_id=project.id))
    task, created = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=PythonRunInput(kind="python_run", payload={"code": "print('secret')"})
        ),
        None,
    )
    assert created
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    return instances, tasks, instance, task, claimed


def test_paused_python_agent_task_is_cancelled_at_claim(database, settings):
    instances, tasks, execution = python_agent_services(database, settings)
    project = ProjectService(database).create(
        ProjectCreate(name="Python Agent pending", summary="Pause before claim.")
    )
    instance = instances.create(AgentInstanceCreate(agent_id="python_child", project_id=project.id))
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=PythonRunInput(kind="python_run", payload={"code": "print('secret')"})
        ),
        None,
    )
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    assert tasks.claim() is None
    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "agent_execution_invalid"


def test_python_agent_use_guard_rejects_modified_claim(database, settings):
    _instances, tasks, _instance, _task, claimed = claimed_python_agent(database, settings)
    assert tasks.worker_request_allowed(claimed)
    assert not tasks.worker_request_allowed(replace(claimed, payload={"code": "print('changed')"}))
    assert not tasks.worker_request_allowed(replace(claimed, python_selection=None))
    assert not tasks.worker_request_allowed(replace(claimed, worker_agent_lifecycle=None))


@pytest.mark.parametrize("resume", [False, True])
def test_python_agent_pause_after_claim_withholds_result(database, settings, resume):
    instances, tasks, instance, task, claimed = claimed_python_agent(database, settings)
    assert tasks.worker_request_allowed(claimed)
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    if resume:
        instances.transition(instance.id, AgentInstanceTransition(action="resume"))
    assert not tasks.worker_request_allowed(claimed)
    tasks.finish(
        claimed,
        State.SUCCEEDED,
        result={
            "stdout": "secret\\n",
            "stderr": "",
            "exit_code": 0,
            "duration_seconds": 0.01,
            "completed_at": "2026-10-21T00:00:00Z",
        },
    )
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.error == "agent_execution_invalidated"
    assert completed.result is None
    assert completed.attempts[0].result is None


def test_python_agent_pause_before_launch_never_calls_runtime(database, settings, monkeypatch):
    instances, tasks, instance, task, claimed = claimed_python_agent(database, settings)
    selected = tasks.python_supervisor.registry.get(claimed.python_selection.descriptor.worker_id)
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        raise AssertionError("revoked Python compute must not launch")

    monkeypatch.setattr(selected.worker, "run", fake_run)
    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    asyncio.run(Runner(tasks, python_supervisor=tasks.python_supervisor)._run(claimed))
    assert calls == []
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.result is None


def test_python_agent_pause_during_runtime_setup_never_starts_guest(
    database, settings, monkeypatch
):
    instances, tasks, instance, task, claimed = claimed_python_agent(database, settings)
    from app import python_worker

    monkeypatch.setattr(
        python_worker.developer_sandbox, "python_compute_sandbox", lambda command: command
    )
    calls = []

    async def forbidden_process(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("revoked Python compute must not start")

    monkeypatch.setattr(python_worker, "run_process", forbidden_process)
    model_event = tasks.model_event

    def pause_on_start(task_id, event, attempt_id, **details):
        if event == "python_execution_started":
            instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        model_event(task_id, event, attempt_id, **details)

    monkeypatch.setattr(tasks, "model_event", pause_on_start)
    asyncio.run(Runner(tasks, python_supervisor=tasks.python_supervisor)._run(claimed))
    assert calls == []
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.result is None


@pytest.mark.parametrize("resume", [False, True])
def test_python_agent_pause_after_guest_withholds_output(database, settings, monkeypatch, resume):
    instances, tasks, instance, task, claimed = claimed_python_agent(database, settings)
    from app import python_worker

    monkeypatch.setattr(
        python_worker.developer_sandbox, "python_compute_sandbox", lambda command: command
    )

    async def completed_process(*args, **kwargs):
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        if resume:
            instances.transition(instance.id, AgentInstanceTransition(action="resume"))
        return SimpleNamespace(stdout=b"secret\\n", stderr=b"", exit_code=0, duration=0.01)

    monkeypatch.setattr(python_worker, "run_process", completed_process)
    asyncio.run(Runner(tasks, python_supervisor=tasks.python_supervisor)._run(claimed))
    completed = tasks.get(task.id)
    assert completed.status == State.CANCELLED
    assert completed.error == "agent_execution_invalidated"
    assert completed.result is None
    assert completed.attempts[0].result is None
