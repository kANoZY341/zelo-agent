"""Persisted Agent computer authority and owner-control tests."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_computer_service import AgentComputerService
from app.agent_computers import (
    ComputerBackendError,
    ComputerControlRequest,
    ComputerControlState,
    ComputerState,
)
from app.agent_factory import (
    AgentComputerSpec,
    AgentFactory,
    AgentRegistry,
    AgentSpec,
)
from app.agent_instances import AgentInstanceService
from app.memory import MemoryService
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import (
    AgentComputerPermissionPatch,
    AgentInstanceCreate,
    ProjectCreate,
)


def computer_agent_spec(*, version: int = 1) -> AgentSpec:
    return AgentSpec(
        agent_id="computer_agent",
        version=version,
        role="computer_agent",
        objective="Use one persistent bounded computer.",
        capabilities=("computer_use",),
        memory_namespace="agent/computer_agent",
        computer=AgentComputerSpec(
            browser=True,
            files=True,
            shell=False,
            human_takeover=True,
        ),
        lifecycle="active",
    )


class FakeBackend:
    def __init__(self, *, configured: bool = True) -> None:
        self.configured = configured
        self.states: dict[str, ComputerState] = {}
        self.control_state = ComputerControlState(
            holder="bot",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=False,
        )
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[str, str] = {}

    def _maybe_fail(self, action: str) -> None:
        if code := self.fail.get(action):
            raise ComputerBackendError(code)

    async def existing(self, instance_id: str):
        self._maybe_fail("existing")
        self.calls.append(("existing", instance_id))
        return self.states.get(instance_id)

    async def ensure(self, instance_id: str):
        self._maybe_fail("ensure")
        self.calls.append(("ensure", instance_id))
        state = ComputerState.model_validate(
            {
                "botId": instance_id,
                "container": f"zelo-computer-{instance_id}",
                "status": "running",
                "port": 49152,
            }
        )
        self.states[instance_id] = state
        return state

    async def stop(self, instance_id: str):
        self._maybe_fail("stop")
        self.calls.append(("stop", instance_id))
        state = self.states.get(instance_id)
        if state is not None:
            self.states[instance_id] = state.model_copy(update={"status": "stopped"})

    async def control(self, instance_id: str):
        self._maybe_fail("control")
        self.calls.append(("control", instance_id))
        return self.control_state

    async def take(self, instance_id: str):
        self._maybe_fail("take")
        self.calls.append(("take", instance_id))
        self.control_state = ComputerControlState(
            holder="human",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=False,
            request=ComputerControlRequest(id="handoff", status="taken"),
        )
        return self.control_state

    async def release(self, instance_id: str):
        self._maybe_fail("release")
        self.calls.append(("release", instance_id))
        self.control_state = ComputerControlState(
            holder="bot",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=True,
            request=ComputerControlRequest(id="handoff", status="completed"),
        )
        return self.control_state


def setup_services(database, *, backend=None, spec=None):
    spec = spec or computer_agent_spec()
    factory = AgentFactory(AgentRegistry((spec,)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    computers = AgentComputerService(database, factory, backend or FakeBackend())
    project = ProjectService(database).create(
        ProjectCreate(name="Computer project", summary="Computer authority test.")
    )
    instance = instances.create(AgentInstanceCreate(agent_id=spec.agent_id, project_id=project.id))
    return factory, instances, computers, instance


def test_agent_creation_persists_disabled_computer_permissions(database):
    _factory, _instances, computers, instance = setup_services(
        database,
        backend=FakeBackend(configured=False),
    )

    status = asyncio.run(computers.status(instance.id))
    assert status.state == "not_configured"
    assert status.configured is False
    assert status.permissions.model_dump(exclude={"updated_at"}) == {
        "enabled": False,
        "browser": False,
        "files": False,
        "shell": False,
    }
    assert status.spec.browser is True
    assert status.spec.files is True
    assert status.spec.shell is False


def test_permission_widening_is_bounded_by_trusted_agent_spec(database):
    _factory, _instances, computers, instance = setup_services(
        database,
        backend=FakeBackend(configured=False),
    )

    status = asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, browser=True),
        )
    )
    assert status.permissions.enabled is True
    assert status.permissions.browser is True

    with pytest.raises(DomainError, match="shell permission exceeds trusted spec"):
        asyncio.run(
            computers.patch_permissions(
                instance.id,
                AgentComputerPermissionPatch(shell=True),
            )
        )

    events = computers.events(instance.id, limit=100, offset=0)
    assert [(item.sequence, item.event, item.outcome) for item in events.items] == [
        (1, "permissions_changed", "succeeded"),
    ]
    assert events.items[0].details["permissions"]["shell"] is False


def test_stale_agent_can_revoke_but_cannot_widen(database):
    factory, _instances, computers, instance = setup_services(
        database,
        backend=FakeBackend(configured=False),
    )
    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, browser=True),
        )
    )

    changed = computer_agent_spec(version=2)
    computers.factory = AgentFactory(AgentRegistry((changed,)))

    revoked = asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(browser=False),
        )
    )
    assert revoked.permissions.browser is False

    with pytest.raises(DomainError, match="stale"):
        asyncio.run(
            computers.patch_permissions(
                instance.id,
                AgentComputerPermissionPatch(browser=True),
            )
        )

    computers.factory = factory


def test_start_stop_and_disable_revoke_backend_lifecycle(database):
    backend = FakeBackend()
    _factory, _instances, computers, instance = setup_services(
        database,
        backend=backend,
    )

    with pytest.raises(DomainError, match="disabled"):
        asyncio.run(computers.start(instance.id))

    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, files=True),
        )
    )
    running = asyncio.run(computers.start(instance.id))
    assert running.state == "running"
    assert ("ensure", instance.id) in backend.calls

    disabled = asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=False),
        )
    )
    assert disabled.permissions.enabled is False
    assert disabled.state == "stopped"
    assert ("stop", instance.id) in backend.calls

    events = computers.events(instance.id, limit=100, offset=0).items
    assert [item.event for item in events] == [
        "permissions_changed",
        "start",
        "permissions_changed",
        "revocation_stop",
    ]


def test_takeover_requires_browser_grant_and_handback_requires_fresh_snapshot(database):
    backend = FakeBackend()
    _factory, _instances, computers, instance = setup_services(
        database,
        backend=backend,
    )

    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, files=True),
        )
    )
    asyncio.run(computers.start(instance.id))
    with pytest.raises(DomainError, match="takeover is not permitted"):
        asyncio.run(computers.take(instance.id))

    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(browser=True),
        )
    )
    taken = asyncio.run(computers.take(instance.id))
    assert taken.control is not None
    assert taken.control.holder == "human"

    released = asyncio.run(computers.release(instance.id))
    assert released.control is not None
    assert released.control.holder == "bot"
    assert released.control.resume_snapshot_required is True

    events = computers.events(instance.id, limit=100, offset=0).items
    assert events[-2].event == "take_control"
    assert events[-1].event == "release_control"
    assert events[-1].details["resume_snapshot_required"] is True


def test_backend_failure_is_fail_closed_and_audited(database):
    backend = FakeBackend()
    backend.fail["ensure"] = "computer_supervisor_unavailable"
    _factory, _instances, computers, instance = setup_services(
        database,
        backend=backend,
    )
    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, browser=True),
        )
    )

    with pytest.raises(DomainError, match="computer_supervisor_unavailable") as caught:
        asyncio.run(computers.start(instance.id))
    assert caught.value.status_code == 503

    events = computers.events(instance.id, limit=100, offset=0).items
    assert events[-1].event == "start"
    assert events[-1].outcome == "failed"
    assert events[-1].details == {"error": "computer_supervisor_unavailable"}


def test_computer_events_are_contiguous_append_only_and_permissions_cannot_reassign(
    database,
):
    _factory, _instances, computers, instance = setup_services(
        database,
        backend=FakeBackend(configured=False),
    )
    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(files=True),
        )
    )

    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE agent_computer_events SET event='changed' "
                    "WHERE agent_instance_id=:instance_id"
                ),
                {"instance_id": instance.id},
            )

    with pytest.raises(IntegrityError, match="contiguous"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO agent_computer_events (
                        id, agent_instance_id, sequence, event, actor, outcome,
                        details, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000999',
                        :instance_id, 99, 'invalid', 'owner', 'succeeded',
                        '{}', '2026-10-05 00:00:00'
                    )
                    """
                ),
                {"instance_id": instance.id},
            )

    with pytest.raises(IntegrityError, match="ownership is immutable"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE agent_computer_permissions "
                    "SET agent_instance_id='00000000-0000-0000-0000-000000000999' "
                    "WHERE agent_instance_id=:instance_id"
                ),
                {"instance_id": instance.id},
            )
