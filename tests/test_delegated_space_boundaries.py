"""Additional operation, version, rollback and live-control boundaries."""

import asyncio

import pytest
from pydantic import ValidationError

from app.agent_execution_contracts import AgentExecutionBinding
from app.computer_contracts import ComputerExecutionSpec
from app.models import AgentComputerPermission, Task, utcnow
from app.policy import DomainError, State
from app.schemas import AgentTaskCreate
from app.space_file_gateway import SpaceFileReadGateway
from tests.test_delegated_space_files import _delegated_env, _request


def test_parent_list_grant_is_not_a_parent_read_grant(database, settings, client):
    env = _delegated_env(database, settings, client)
    intent = env["resources"].intents.create(
        instance_id=env["parent"].id,
        space_id="workroom",
        resource_kind="files",
        resource_id="documents",
        operations=("list",),
        ttl_seconds=600,
    )
    parent = env["resources"].activate(
        instance_id=env["parent"].id, intent_id=intent.id, resource_record_id=env["resource"].id
    )
    with pytest.raises(DomainError):
        env["submits"].submit(
            env["child"].id, _request(env, parent_space_activation_id=parent.id), None
        )
    task, _ = env["submits"].submit(
        env["child"].id, _request(env, "files_list", parent_space_activation_id=parent.id), None
    )
    asyncio.run(env["runner"]._run(env["tasks"].claim()))
    assert env["tasks"].get(task.id).status == State.SUCCEEDED


def test_legacy_versions_and_nondelegated_tasks_cannot_carry_parent_grants(
    database, settings, client
):
    env = _delegated_env(database, settings, client)
    body = _request(env)
    task, _ = env["submits"].submit(env["child"].id, body, None)
    with database.read() as session:
        spec = session.get(Task, task.id).computer_spec
    for version in ("zelo-computer-execution-v1", "zelo-computer-execution-v2"):
        with pytest.raises(ValidationError):
            ComputerExecutionSpec.model_validate({**spec, "schema_version": version})
    for field in (
        "parent_space_activation_id",
        "parent_agent_instance_id",
        "parent_permission_version",
    ):
        with pytest.raises(ValidationError):
            ComputerExecutionSpec.model_validate({**spec, field: None})
    standalone = AgentTaskCreate(task=body.task)
    with pytest.raises(DomainError):
        env["submits"].submit(env["child"].id, standalone, None)
    assert env["tasks"].list_tasks(limit=100, offset=0).total == 1


def test_reenabled_parent_permission_does_not_resurrect_old_claim_but_explicit_retry_works(
    database, settings, client
):
    env = _delegated_env(database, settings, client)
    task, _ = env["submits"].submit(env["child"].id, _request(env), None)
    with database.read() as session:
        previous_version = session.get(Task, task.id).computer_spec["parent_permission_version"]
    for enabled in (False, True):
        with database.transaction() as session:
            row = session.get(AgentComputerPermission, env["parent"].id)
            row.files = enabled
            row.updated_at = utcnow()
    assert env["tasks"].claim() is None
    assert env["tasks"].get(task.id).status == State.CANCELLED
    retried = env["tasks"].retry(task.id)
    assert retried.action_version == 2 and retried.status == State.PENDING
    claimed = env["tasks"].claim()
    assert claimed.computer_request.parent_permission_version != previous_version
    asyncio.run(env["runner"]._run(claimed))
    done = env["tasks"].get(task.id)
    assert done.status == State.SUCCEEDED
    assert done.result["files_read"]["text"] == "dual-approved evidence"


def test_unqualified_nested_binding_refuses_before_resource_lookup(
    database, settings, client, monkeypatch
):
    env = _delegated_env(database, settings, client)
    task, _ = env["submits"].submit(env["child"].id, _request(env), None)
    with database.read() as session:
        row = session.get(Task, task.id)
        agent = AgentExecutionBinding.model_validate(row.agent_execution_spec)
        computer = ComputerExecutionSpec.model_validate(row.computer_spec)
    nested = agent.model_copy(
        update={"delegation": agent.delegation.model_copy(update={"depth": 2, "max_depth": 2})}
    )

    def no_resource(*args, **kwargs):
        pytest.fail("Unqualified nesting reached a resource lookup")

    monkeypatch.setattr(SpaceFileReadGateway, "_verified_resource", no_resource)
    with database.transaction() as session:
        with pytest.raises(DomainError, match="computer_space_activation_invalid"):
            env["tasks"]._check_space_approval(session, nested, computer, env["computer"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("holder", "human"),
        ("requested", True),
        ("transitioning", True),
        ("resume_snapshot_required", True),
    ],
)
def test_parent_backend_control_is_checked_not_just_child(
    database, settings, client, monkeypatch, field, value
):
    env = _delegated_env(database, settings, client)
    task, _ = env["submits"].submit(env["child"].id, _request(env), None)
    claimed = env["tasks"].claim()
    original = env["backend"].control

    async def parent_blocked(instance_id):
        state = await original(instance_id)
        return state.model_copy(update={field: value}) if instance_id == env["parent"].id else state

    monkeypatch.setattr(env["backend"], "control", parent_blocked)
    asyncio.run(env["runner"]._run(claimed))
    done = env["tasks"].get(task.id)
    assert done.status == State.FAILED and done.result is None
    assert done.error == "computer_space_read_denied"
    assert {call[1] for call in env["backend"].calls} == {env["parent"].id, env["child"].id}
