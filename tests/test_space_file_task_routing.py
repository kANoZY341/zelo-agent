"""Space IDs are part of the immutable Computer Task and exact dispatch route."""

import asyncio

import pytest
from pydantic import ValidationError

from app.agent_execution_contracts import AgentExecutionBinding, DelegationExecutionBinding
from app.agent_instances import AgentInstanceService
from app.computer_contracts import ComputerExecutionSpec
from app.computer_worker import ComputerRuntimeError
from app.memory import MemoryService
from app.models import AgentComputerPermission, Task, utcnow
from app.policy import DomainError, State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentTaskCreate,
    ComputerRunInput,
    ProjectCreate,
)
from tests.test_space_file_gateway import _claim, _fixture


def _space_task(env, *, activation_id=None, path="notes.txt"):
    return env["agent_submit"].submit(
        env["instance"].id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={
                    "action": "files_read",
                    "path": path,
                    "space_activation_id": activation_id or env["activation"].id,
                },
            )
        ),
        None,
    )[0]


def test_agent_submits_claims_and_finishes_real_mediated_read(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("authorized source text")
    claimed = _claim(env)
    assert claimed.payload["space_activation_id"] == env["activation"].id
    assert claimed.computer_request.space_activation_id == env["activation"].id
    runner = Runner(
        env["tasks"],
        computer_supervisor=env["supervisor"],
        space_file_gateway=env["gateway"],
    )
    asyncio.run(runner._run(claimed))
    result = env["tasks"].get(claimed.task_id)
    assert result.status == State.SUCCEEDED
    assert result.result["files_read"]["text"] == "authorized source text"
    assert result.result["files_read"]["truncated"] is False
    assert result.result["files_read"]["path"] == "notes.txt"
    assert all(call[0] == "control" for call in env["backend"].calls)
    event_names = [event.event for event in env["tasks"].events(claimed.task_id, 100, 0)]
    assert "space_file_read_completed" in event_names
    assert "computer_completed" in event_names


def test_gateway_unavailable_never_falls_back_to_openbot(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("must remain local")
    claimed = _claim(env)
    runner = Runner(env["tasks"], computer_supervisor=env["supervisor"])
    asyncio.run(runner._run(claimed))
    result = env["tasks"].get(claimed.task_id)
    assert result.status == State.FAILED
    assert result.error == "computer_space_gateway_unavailable"
    assert not env["backend"].calls


def test_direct_generic_backend_refuses_space_bound_execution(database, settings):
    env = _fixture(database, settings)
    claimed = _claim(env)
    with pytest.raises(ComputerRuntimeError, match="computer_space_requires_mediated_gateway"):
        asyncio.run(
            env["computer"].run(
                spec=claimed.computer_request,
                action="files_read",
                path="notes.txt",
                timeout_seconds=claimed.timeout_seconds,
                emit=lambda *_: None,
            )
        )
    assert not env["backend"].calls


def test_revoked_confirmation_cancels_at_claim_before_attempt(database, settings):
    env = _fixture(database, settings)
    task = _space_task(env)
    env["resources"].revoke(instance_id=env["instance"].id, activation_id=env["activation"].id)
    assert env["tasks"].claim() is None
    cancelled = env["tasks"].get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "computer_execution_spec_invalid"
    assert not cancelled.attempts
    assert not env["backend"].calls


def test_revoked_confirmation_refuses_new_submission(database, settings):
    env = _fixture(database, settings)
    env["intents"].revoke(instance_id=env["instance"].id, intent_id=env["intent"].id)
    with pytest.raises(DomainError, match="computer_space_activation_invalid"):
        _space_task(env)


def test_retry_after_revocation_requires_new_confirmation(database, settings):
    env = _fixture(database, settings)
    claimed = _claim(env)
    env["tasks"].finish(claimed, State.FAILED, error="fixture_failure")
    env["resources"].revoke(instance_id=env["instance"].id, activation_id=env["activation"].id)
    with pytest.raises(DomainError, match="computer_space_activation_invalid"):
        env["tasks"].retry(claimed.task_id)
    result = env["tasks"].get(claimed.task_id)
    assert result.status == State.FAILED
    assert result.error == "fixture_failure"
    assert not env["backend"].calls


def test_cross_agent_confirmation_cannot_be_attached(database, settings):
    env = _fixture(database, settings)
    factory = env["factory"]
    agents = AgentInstanceService(database, factory, MemoryService(database))
    project = ProjectService(database).create(
        ProjectCreate(name="Other project", summary="Isolation test")
    )
    foreign = agents.create(
        AgentInstanceCreate(agent_id=env["instance"].agent_id, project_id=project.id)
    )
    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, foreign.id)
        permission.enabled = True
        permission.files = True
        permission.updated_at = utcnow()
    with pytest.raises(DomainError, match="computer_space_activation_invalid"):
        env["agent_submit"].submit(
            foreign.id,
            AgentTaskCreate(
                task=ComputerRunInput(
                    kind="computer_run",
                    payload={
                        "action": "files_read",
                        "path": "notes.txt",
                        "space_activation_id": env["activation"].id,
                    },
                )
            ),
            None,
        )


def test_space_id_only_supported_on_file_reads():
    for action in ("browser_read", "files_list", "browser_snapshot"):
        with pytest.raises(ValidationError, match="computer_space_activation_files_read_only"):
            ComputerRunInput(
                kind="computer_run",
                payload={
                    "action": action,
                    "path": "",
                    "space_activation_id": "00000000-0000-4000-8000-000000000001",
                },
            )


def test_hung_human_control_lookup_expires_before_file_read(database, settings, monkeypatch):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("private")
    env["agent_submit"].submit(
        env["instance"].id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                timeout_seconds=1.0,
                payload={
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": env["activation"].id,
                },
            )
        ),
        None,
    )
    claimed = env["tasks"].claim()
    assert claimed is not None

    async def stalled_control(_instance_id):
        await asyncio.sleep(1.1)
        raise AssertionError("Timed-out control lookup should never complete")

    monkeypatch.setattr(env["backend"], "control", stalled_control)
    runner = Runner(
        env["tasks"],
        computer_supervisor=env["supervisor"],
        space_file_gateway=env["gateway"],
    )
    asyncio.run(runner._run(claimed))
    result = env["tasks"].get(claimed.task_id)
    assert result.status == State.FAILED
    assert result.error == "computer_space_read_denied"
    assert not env["backend"].calls


def test_space_confirmation_change_is_action_hash_bound(database, settings):
    env = _fixture(database, settings)
    first = _space_task(env)
    another_intent = env["intents"].create(
        instance_id=env["instance"].id,
        space_id="workroom",
        resource_kind="files",
        resource_id="documents",
        operations=("read",),
        ttl_seconds=600,
    )
    another = env["resources"].activate(
        instance_id=env["instance"].id,
        intent_id=another_intent.id,
        resource_record_id=env["resource"].id,
    )
    second = _space_task(env, activation_id=another.id)
    assert first.id != second.id
    with database.read() as session:
        first_hash = session.get(Task, first.id).action_hash
        second_hash = session.get(Task, second.id).action_hash
    assert first_hash != second_hash
    assert first.computer_spec.space_activation_id == env["activation"].id
    assert second.computer_spec.space_activation_id == another.id


def test_runtime_human_takeover_during_space_route_fails_closed(database, settings, monkeypatch):
    from app.agent_computers import ComputerControlState

    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("private")
    claimed = _claim(env)
    env["backend"].control_state = ComputerControlState(
        holder="human",
        requested=True,
        transitioning=False,
        resumeSnapshotRequired=True,
    )
    runner = Runner(
        env["tasks"],
        computer_supervisor=env["supervisor"],
        space_file_gateway=env["gateway"],
    )
    asyncio.run(runner._run(claimed))
    outcome = env["tasks"].get(claimed.task_id)
    assert outcome.status == State.FAILED
    assert outcome.error == "computer_space_read_denied"
    assert all(call[0] != "files_read" for call in env["backend"].calls)


def test_delegated_space_activation_denied_before_any_resource_lookup(
    database, settings, monkeypatch
):
    """The nondelegated Space file grant cannot be inherited by a child Agent.

    Use a real persisted valid owner-bound Computer Task and a schema-valid
    delegated execution snapshot, then exercise the shared admission gate
    called by submit/retry/claim. No additional historical Task or backend
    file read can arise from this denied authority.
    """
    from app.space_file_gateway import SpaceFileReadGateway

    env = _fixture(database, settings)
    legitimate_task = _space_task(env)
    with database.read() as session:
        persisted = session.get(Task, legitimate_task.id)
        assert persisted is not None
        binding = AgentExecutionBinding.model_validate(persisted.agent_execution_spec)
        computer = ComputerExecutionSpec.model_validate(persisted.computer_spec)
    assert binding.delegation is None
    forged_delegation = DelegationExecutionBinding(
        delegation_id="00000000-0000-4000-8000-000000000001",
        parent_instance_id="00000000-0000-4000-8000-000000000002",
        child_instance_id=env["instance"].id,
        campaign_id="00000000-0000-4000-8000-000000000003",
        budget_reservation_id="00000000-0000-4000-8000-000000000004",
        idempotency_key="cannot_inherit_space",
        request_hash="a" * 64,
        objective="Synthetic delegated file read must be denied",
        allowed_task_kinds=("computer_run",),
        required_capabilities=("computer_read",),
        depth=1,
        max_depth=1,
        budget_resource="agent_steps",
        budget_units=1,
    )
    delegated = AgentExecutionBinding.model_validate(
        binding.model_copy(update={"delegation": forged_delegation}).model_dump(mode="json")
    )

    def should_not_read_resource(*_args, **_kwargs):
        raise AssertionError("Delegate must be refused before Space resource access")

    monkeypatch.setattr(SpaceFileReadGateway, "_verified_resource", should_not_read_resource)
    with database.transaction() as session:
        with pytest.raises(DomainError, match="computer_space_activation_invalid"):
            env["tasks"]._check_space_approval(
                session,
                delegated,
                computer,
                env["computer"],
            )
    assert env["tasks"].get(legitimate_task.id).status == State.PENDING
    assert not env["backend"].calls
