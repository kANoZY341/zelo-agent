"""Computer operations must not start after authority changes during dispatch.

These use real migrated Core databases/TaskService with fake computer transport;
no browser navigation, external network, host file access or LLM is performed.
"""

import asyncio

import pytest

from app.api_contracts import ApiEndpointRegistry
from app.computer_worker import ComputerRuntimeError
from app.models import AgentComputerPermission, utcnow
from app.schemas import AgentTaskCreate, ComputerRunInput
from tests.test_computer_navigation import navigation_task, setup_navigation
from tests.test_computer_worker import grant, setup

READ_ACTIONS = ("files_read", "files_list", "browser_read", "browser_snapshot")


def _case(database, settings, action):
    if action == "browser_navigate":
        backend, _, runtime, _, tasks, execution, instance = setup_navigation(database, settings)
        payload = navigation_task()
        path, endpoint = "/docs", "public_site"
    else:
        backend, runtime, _, tasks, execution, instance = setup(database, settings)
        grant(database, instance.id, files=True, browser=True)
        path, endpoint = ("notes.txt" if action == "files_read" else ""), None
        payload = AgentTaskCreate(
            task=ComputerRunInput(kind="computer_run", payload={"action": action, "path": path})
        )
    execution.submit(instance.id, payload, "computer-dispatch:revalidation")
    claimed = tasks.claim()
    assert claimed is not None and claimed.computer_request is not None
    assert runtime.matches(claimed.computer_request)
    return backend, runtime, instance, claimed, path, endpoint


def _revoke(database, instance_id):
    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance_id)
        assert row is not None
        row.enabled = False
        row.updated_at = utcnow()


@pytest.mark.parametrize("action", (*READ_ACTIONS, "browser_navigate"))
@pytest.mark.parametrize("boundary", ("control_await", "started_event"))
@pytest.mark.parametrize("drift", ("permission_revocation", "backend_identity"))
def test_authority_drift_at_dispatch_never_calls_resource(
    database, settings, monkeypatch, action, boundary, drift
):
    backend, runtime, instance, claimed, path, endpoint = _case(database, settings, action)
    real_control = backend.control
    invalidations = []

    def invalidate():
        invalidations.append(drift)
        if drift == "permission_revocation":
            _revoke(database, instance.id)
        else:
            backend.identity = "b" * 64

    async def control_then_invalidate(instance_id):
        control = await real_control(instance_id)
        await asyncio.sleep(0)  # Deterministic suspension; no timing assumption.
        if boundary == "control_await":
            invalidate()
        return control

    def emit(event, _details):
        if boundary == "started_event" and event.endswith("_started"):
            invalidate()

    monkeypatch.setattr(backend, "control", control_then_invalidate)
    with pytest.raises(ComputerRuntimeError, match="computer_execution_spec_stale"):
        asyncio.run(
            runtime.run(
                spec=claimed.computer_request,
                action=action,
                path=path,
                endpoint_id=endpoint,
                timeout_seconds=claimed.timeout_seconds,
                emit=emit,
            )
        )
    assert invalidations
    assert not any(call[0] == action for call in backend.calls), (
        "A stale request reached the computer resource before being rejected"
    )


@pytest.mark.parametrize("boundary", ("control_await", "started_event"))
def test_endpoint_drift_at_dispatch_never_navigates(database, settings, monkeypatch, boundary):
    action = "browser_navigate"
    backend, runtime, _, claimed, path, endpoint = _case(database, settings, action)
    real_control = backend.control

    def invalidate():
        runtime.endpoints = ApiEndpointRegistry.from_entries(("public_site=https://example.org",))

    async def changed_control(instance_id):
        control = await real_control(instance_id)
        if boundary == "control_await":
            invalidate()
        return control

    def emit(event, _details):
        if boundary == "started_event" and event.endswith("_started"):
            invalidate()

    monkeypatch.setattr(backend, "control", changed_control)
    with pytest.raises(ComputerRuntimeError, match="computer_execution_spec_stale"):
        asyncio.run(
            runtime.run(
                spec=claimed.computer_request,
                action=action,
                path=path,
                endpoint_id=endpoint,
                timeout_seconds=claimed.timeout_seconds,
                emit=emit,
            )
        )
    assert not any(call[0] == action for call in backend.calls)


@pytest.mark.parametrize("action", (*READ_ACTIONS, "browser_navigate"))
def test_unchanged_authority_still_dispatches_once(database, settings, action):
    backend, runtime, _, claimed, path, endpoint = _case(database, settings, action)
    events = []
    result = asyncio.run(
        runtime.run(
            spec=claimed.computer_request,
            action=action,
            path=path,
            endpoint_id=endpoint,
            timeout_seconds=claimed.timeout_seconds,
            emit=lambda event, _details: events.append(event),
        )
    )
    assert result["action"] == action
    assert sum(call[0] == action for call in backend.calls) == 1
    assert any(event.endswith("_completed") for event in events)


def test_runner_records_revocation_failure_without_resource_access(database, settings, monkeypatch):
    from app.policy import State
    from app.runner import Runner

    backend, _, supervisor, tasks, execution, instance = setup(database, settings)
    grant(database, instance.id, files=True)
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run", payload={"action": "files_read", "path": "notes.txt"}
            )
        ),
        "computer-dispatch:runner-revocation",
    )
    claimed = tasks.claim()
    assert claimed is not None
    real_control = backend.control

    async def control_then_revoke(instance_id):
        control = await real_control(instance_id)
        _revoke(database, instance_id)
        return control

    monkeypatch.setattr(backend, "control", control_then_revoke)
    asyncio.run(Runner(tasks, computer_supervisor=supervisor)._run(claimed))
    completed = tasks.get(task.id)
    assert completed.status == State.FAILED
    assert completed.error == "computer_execution_spec_stale"
    assert completed.result is None
    assert not any(call[0] == "files_read" for call in backend.calls)
    assert "computer_read_completed" not in [event.event for event in tasks.events(task.id, 100, 0)]
