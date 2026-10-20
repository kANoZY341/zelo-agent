"""A child and parent require independent Space approvals under delegation."""

import asyncio

import pytest
from pydantic import ValidationError

from app.agent_delegations import AgentDelegationService
from app.campaign_control import CampaignControlService
from app.campaigns import CampaignService
from app.models import AgentComputerPermission, utcnow
from app.policy import DomainError, State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentDelegationCreate,
    AgentDelegationRevoke,
    AgentInstanceCreate,
    AgentTaskCreate,
    CampaignBudgetReserve,
    CampaignBudgetSet,
    CampaignCreate,
    ComputerRunInput,
    GoalCreate,
    GoalSpec,
)
from tests.test_computer_worker import computer_agent_spec
from tests.test_space_file_gateway import _fixture
from tests.test_space_file_task_routing import _list_activation


def _delegated_fixture(database, settings):
    parent_spec = computer_agent_spec().model_copy(
        update={
            "agent_id": "space_parent",
            "role": "manager",
            "memory_namespace": "agent/space_parent",
            "allow_subagents": True,
            "max_subagents": 1,
            "space_permissions": ("workroom",),
        }
    )
    env = _fixture(database, settings, extra_specs=(parent_spec,))
    parent = env["agents"].create(
        AgentInstanceCreate(agent_id="space_parent", project_id=env["project"].id)
    )
    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, parent.id)
        assert permission is not None
        permission.enabled = permission.files = True
        permission.updated_at = utcnow()
    parent_intent = env["intents"].create(
        instance_id=parent.id,
        space_id="workroom",
        resource_kind="files",
        resource_id="documents",
        operations=("list", "read"),
        ttl_seconds=600,
    )
    env["parent_activation"] = env["resources"].activate(
        instance_id=parent.id,
        intent_id=parent_intent.id,
        resource_record_id=env["resource"].id,
    )
    goal = ProjectService(database).create_goal(
        env["project"].id,
        GoalCreate(title="Review child evidence", spec=GoalSpec(objective="Review a bounded file")),
    )
    campaign = CampaignService(database).create(goal.id, CampaignCreate(title="Child read"))
    controls = CampaignControlService(database)
    controls.set_budget(campaign.id, "agent_steps", CampaignBudgetSet(limit_units=10))
    reservation = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=parent.id,
            idempotency_key="child-read-budget",
            units=1,
            ttl_seconds=600,
        ),
    )
    delegations = AgentDelegationService(database, env["factory"], controls)
    delegation = delegations.create(
        parent.id,
        AgentDelegationCreate(
            child_instance_id=env["instance"].id,
            campaign_id=campaign.id,
            budget_reservation_id=reservation.id,
            idempotency_key="child-read-delegation",
            objective="Read the approved file",
            allowed_task_kinds=["computer_run"],
            required_capabilities=["computer_read"],
            max_depth=1,
            budget_resource="agent_steps",
            budget_units=1,
        ),
    )
    return env, campaign, delegation, delegations


def _submit(
    env,
    campaign,
    delegation,
    activation_id,
    *,
    action="files_read",
    path="notes.txt",
    parent_activation_id=None,
):
    parent_activation_id = (
        env["parent_activation"].id if parent_activation_id is None else parent_activation_id
    )
    return env["agent_submit"].submit(
        env["instance"].id,
        AgentTaskCreate(
            delegation_id=delegation.id,
            task=ComputerRunInput(
                kind="computer_run",
                campaign_id=campaign.id,
                payload={
                    "action": action,
                    "path": path,
                    "space_activation_id": activation_id,
                    "parent_space_activation_id": parent_activation_id,
                },
            ),
        ),
        None,
    )[0]


def test_dual_owned_grants_allow_delegated_read(database, settings):
    env, campaign, delegation, _ = _delegated_fixture(database, settings)
    (env["root"] / "notes.txt").write_text("child-approved")
    task = _submit(env, campaign, delegation, env["activation"].id)
    claimed = env["tasks"].claim()
    assert claimed is not None and claimed.task_id == task.id
    runner = Runner(
        env["tasks"],
        computer_supervisor=env["supervisor"],
        space_file_gateway=env["gateway"],
    )
    asyncio.run(runner._run(claimed))
    assert env["tasks"].get(task.id).status == State.SUCCEEDED
    assert env["tasks"].get(task.id).result["files_read"]["text"] == "child-approved"


def test_revoked_delegation_refuses_child_space_use(database, settings):
    env, campaign, delegation, delegations = _delegated_fixture(database, settings)
    (env["root"] / "notes.txt").write_text("never disclosed")
    task = _submit(env, campaign, delegation, env["activation"].id)
    claimed = env["tasks"].claim()
    assert claimed is not None and claimed.task_id == task.id
    delegations.revoke(delegation.id, AgentDelegationRevoke(reason="Owner revoked"))
    with pytest.raises(DomainError):
        asyncio.run(
            env["gateway"].read(
                execution=claimed,
                activation_id=env["activation"].id,
                relative_path="notes.txt",
            )
        )
    assert all(call[0] != "files_read" for call in env["backend"].calls)


def test_parent_grant_cannot_replace_child_grant(database, settings):
    env, campaign, delegation, _ = _delegated_fixture(database, settings)
    with pytest.raises((DomainError, ValidationError)):
        _submit(env, campaign, delegation, env["parent_activation"].id)
    assert env["tasks"].list_tasks(limit=100, offset=0).total == 0


def test_child_only_grant_is_refused_before_task_persistence(database, settings):
    env, campaign, delegation, _ = _delegated_fixture(database, settings)
    with pytest.raises(DomainError, match="computer_space_activation_invalid"):
        env["agent_submit"].submit(
            env["instance"].id,
            AgentTaskCreate(
                delegation_id=delegation.id,
                task=ComputerRunInput(
                    kind="computer_run",
                    campaign_id=campaign.id,
                    payload={
                        "action": "files_read",
                        "path": "notes.txt",
                        "space_activation_id": env["activation"].id,
                    },
                ),
            ),
            None,
        )
    assert env["tasks"].list_tasks(limit=100, offset=0).total == 0


def test_delegated_read_fails_closed_without_core_validator(database, settings):
    from app.space_file_gateway import SpaceFileReadGateway

    env, campaign, delegation, _ = _delegated_fixture(database, settings)
    (env["root"] / "notes.txt").write_text("never disclosed")
    task = _submit(env, campaign, delegation, env["activation"].id)
    claimed = env["tasks"].claim()
    assert claimed is not None and claimed.task_id == task.id
    unbound_gateway = SpaceFileReadGateway(
        database, env["factory"], settings.runtime_dir, env["computer"]
    )
    with pytest.raises(DomainError, match="delegated authority unavailable"):
        asyncio.run(
            unbound_gateway.read(
                execution=claimed,
                activation_id=env["activation"].id,
                relative_path="notes.txt",
            )
        )
    assert all(call[0] != "files_read" for call in env["backend"].calls)


def test_revocation_during_control_lookup_prevents_delegated_read(database, settings, monkeypatch):
    env, campaign, delegation, delegations = _delegated_fixture(database, settings)
    (env["root"] / "notes.txt").write_text("never disclosed")
    task = _submit(env, campaign, delegation, env["activation"].id)
    claimed = env["tasks"].claim()
    assert claimed is not None and claimed.task_id == task.id
    original = env["backend"].control

    async def revoke_then_report(instance_id):
        delegations.revoke(delegation.id, AgentDelegationRevoke(reason="Owner revoked"))
        return await original(instance_id)

    monkeypatch.setattr(env["backend"], "control", revoke_then_report)
    runner = Runner(
        env["tasks"],
        computer_supervisor=env["supervisor"],
        space_file_gateway=env["gateway"],
    )
    asyncio.run(runner._run(claimed))
    outcome = env["tasks"].get(task.id)
    assert outcome.status == State.FAILED
    assert outcome.error == "computer_space_read_denied"
    assert all(call[0] == "control" for call in env["backend"].calls)
    assert "space_file_read_completed" not in {
        event.event for event in env["tasks"].events(task.id, 100, 0)
    }


def test_child_owned_list_grant_allows_only_listing(database, settings):
    env, campaign, delegation, _ = _delegated_fixture(database, settings)
    (env["root"] / "notes.txt").write_text("contents must stay private")
    activation = _list_activation(env)
    with pytest.raises(DomainError, match="computer_space_activation_invalid"):
        _submit(env, campaign, delegation, env["activation"].id, action="files_list", path="")
    task = _submit(env, campaign, delegation, activation.id, action="files_list", path="")
    claimed = env["tasks"].claim()
    assert claimed is not None and claimed.task_id == task.id
    runner = Runner(
        env["tasks"],
        computer_supervisor=env["supervisor"],
        space_file_gateway=env["gateway"],
    )
    asyncio.run(runner._run(claimed))
    result = env["tasks"].get(task.id)
    assert result.status == State.SUCCEEDED
    assert [entry["path"] for entry in result.result["files_list"]["entries"]] == ["notes.txt"]
    assert "contents must stay private" not in str(result.result)
