"""Persisted Agent computer authority and owner-control tests."""

import asyncio

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_computer_service import AgentComputerService
from app.agent_computers import (
    ComputerBackendError,
    ComputerControlRequest,
    ComputerControlState,
    ComputerState,
    OpenBotComputerBackend,
)
from app.agent_factory import (
    AgentComputerSpec,
    AgentFactory,
    AgentRegistry,
    AgentSpec,
)
from app.agent_instances import AgentInstanceService
from app.computer_control_fence import human_takeover_unreleased
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
    assert [event.event for event in events[-4:]] == [
        "take_control_requested",
        "take_control",
        "release_control_requested",
        "release_control",
    ]
    assert events[-1].details["resume_snapshot_required"] is True


def test_ambiguous_takeover_error_keeps_durable_fence_until_confirmed_release(database):
    backend = FakeBackend()
    _factory, _instances, computers, instance = setup_services(database, backend=backend)
    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, browser=True, files=True),
        )
    )
    asyncio.run(computers.start(instance.id))
    backend.fail["take"] = "computer_http_503"

    with pytest.raises(DomainError, match="computer_http_503"):
        asyncio.run(computers.take(instance.id))
    with database.read() as session:
        assert human_takeover_unreleased(session, instance.id)
    before_calls = list(backend.calls)
    with pytest.raises(DomainError, match="pending owner handback"):
        asyncio.run(computers.take(instance.id))
    assert backend.calls == before_calls  # Never repeat an ambiguous remote takeover.

    backend.fail.pop("take")
    asyncio.run(computers.release(instance.id))
    with database.read() as session:
        assert not human_takeover_unreleased(session, instance.id)


def test_owner_cancel_waiting_takeover_with_pinned_openbot_adapter(database):
    """Actual backend HTTP contract repairs a waiting request without blind retry."""
    state = {
        "holder": "bot",
        "requested": False,
        "transitioning": False,
        "resumeSnapshotRequired": False,
        "request": None,
    }
    calls = []
    instance_id = None

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        instance_state = {
            "botId": instance_id,
            "container": f"zelo-computer-{instance_id}",
            "status": "running",
            "port": 49152,
        }
        if path == "/computers":
            return httpx.Response(200, json={"computers": [instance_state]})
        if path == f"/computers/{instance_id}/ensure":
            return httpx.Response(200, json=instance_state)
        if path == "/control" and request.method == "GET":
            return httpx.Response(200, json=state)
        if path == "/control/request":
            state.update(
                requested=True,
                transitioning=True,
                request={"id": "handoff", "status": "waiting"},
            )
            return httpx.Response(200, json=state)
        if path == "/control/take":
            return httpx.Response(503, json={"error": "take result uncertain"})
        if path == "/control/cancel":
            state.update(
                requested=False,
                transitioning=False,
                request={"id": "handoff", "status": "cancelled"},
            )
            return httpx.Response(200, json=state)
        raise AssertionError(f"Unexpected adapter path: {path}")

    from tests.test_agent_computers import settings as computer_settings

    backend = OpenBotComputerBackend(computer_settings(), transport=httpx.MockTransport(handler))
    _factory, _instances, computers, instance = setup_services(database, backend=backend)
    instance_id = instance.id
    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, browser=True, files=True),
        )
    )
    asyncio.run(computers.start(instance.id))
    with pytest.raises(DomainError, match="computer_http_503"):
        asyncio.run(computers.take(instance.id))
    with database.read() as session:
        assert human_takeover_unreleased(session, instance.id)

    with pytest.raises(DomainError, match="pending owner handback"):
        asyncio.run(computers.take(instance.id))
    assert calls.count(("POST", "/control/take")) == 1
    recovered = asyncio.run(computers.release(instance.id))
    assert recovered.control is not None
    assert recovered.control.holder == "bot"
    assert recovered.control.requested is False
    assert ("POST", "/control/cancel") in calls
    with database.read() as session:
        assert not human_takeover_unreleased(session, instance.id)


def test_release_cannot_race_in_flight_takeover_request(database, monkeypatch):
    backend = FakeBackend()
    _factory, _instances, computers, instance = setup_services(database, backend=backend)
    asyncio.run(
        computers.patch_permissions(
            instance.id, AgentComputerPermissionPatch(enabled=True, browser=True)
        )
    )
    asyncio.run(computers.start(instance.id))
    entered = asyncio.Event()
    allow_take = asyncio.Event()
    original_take = backend.take

    async def delayed_take(agent_id):
        entered.set()
        await allow_take.wait()
        return await original_take(agent_id)

    monkeypatch.setattr(backend, "take", delayed_take)

    async def exercise():
        take_task = asyncio.create_task(computers.take(instance.id))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            with pytest.raises(DomainError, match="takeover.*outcome"):
                await computers.release(instance.id)
            assert ("release", instance.id) not in backend.calls
        finally:
            allow_take.set()
            await take_task

    asyncio.run(exercise())
    with database.read() as session:
        assert human_takeover_unreleased(session, instance.id)


def test_overlapping_handbacks_cannot_clear_future_takeover(database, monkeypatch):
    """A second delayed release must never complete after a newer takeover."""
    backend = FakeBackend()
    _factory, _instances, computers, instance = setup_services(database, backend=backend)
    asyncio.run(
        computers.patch_permissions(
            instance.id, AgentComputerPermissionPatch(enabled=True, browser=True)
        )
    )
    asyncio.run(computers.start(instance.id))
    asyncio.run(computers.take(instance.id))
    entered = asyncio.Event()
    allow_release = asyncio.Event()
    original_release = backend.release

    async def delayed_release(agent_id):
        entered.set()
        await allow_release.wait()
        return await original_release(agent_id)

    monkeypatch.setattr(backend, "release", delayed_release)

    async def exercise():
        first = asyncio.create_task(computers.release(instance.id))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            with pytest.raises(DomainError, match="handback outcome"):
                await computers.release(instance.id)
            assert sum(action == "release" for action, _ in backend.calls) == 0
            with pytest.raises(DomainError, match="pending owner handback"):
                await computers.take(instance.id)
        finally:
            allow_release.set()
            await first

    asyncio.run(exercise())
    assert sum(action == "release" for action, _ in backend.calls) == 1
    with database.read() as session:
        assert not human_takeover_unreleased(session, instance.id)


def test_unconfirmed_remote_handback_keeps_takeover_fenced(database, monkeypatch):
    backend = FakeBackend()
    _factory, _instances, computers, instance = setup_services(database, backend=backend)
    asyncio.run(
        computers.patch_permissions(
            instance.id,
            AgentComputerPermissionPatch(enabled=True, browser=True),
        )
    )
    asyncio.run(computers.start(instance.id))
    asyncio.run(computers.take(instance.id))

    async def bad_release(_instance_id):
        return ComputerControlState(
            holder="human",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=False,
        )

    monkeypatch.setattr(backend, "release", bad_release)
    with pytest.raises(DomainError, match="handback unconfirmed"):
        asyncio.run(computers.release(instance.id))
    with database.read() as session:
        assert human_takeover_unreleased(session, instance.id)


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
