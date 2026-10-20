"""Dual owner-approved delegated reads through persisted Task and gateway gates."""

import asyncio

import pytest
from pydantic import ValidationError

from app.agent_delegations import AgentDelegationService
from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, AgentRegistry
from app.agent_instances import AgentInstanceService
from app.agent_space_resources import AgentSpaceResourceService
from app.campaign_control import CampaignControlService
from app.computer_contracts import ComputerExecutionSpec
from app.computer_worker import ComputerRuntime
from app.memory import MemoryService
from app.models import AgentComputerPermission, utcnow
from app.policy import DomainError, State
from app.runner import Runner
from app.schemas import AgentDelegationCreate, AgentInstanceCreate, AgentTaskCreate, ComputerPayload
from app.space_file_gateway import SpaceFileReadGateway
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_computer_worker
from tests.test_agent_delegations import create_project_goal_campaign, reserve
from tests.test_computer_worker import FakeReadBackend, computer_agent_spec


def test_parent_approval_is_explicit_versioned_task_input():
    assert "parent_space_activation_id" in ComputerPayload.model_fields
    assert "parent_space_activation_id" in ComputerExecutionSpec.model_fields
    assert "parent_permission_version" in ComputerExecutionSpec.model_fields


def _delegated_env(database, settings, client):
    project, _goal, campaign = create_project_goal_campaign(client, "Dual approved Space read")
    child_spec = computer_agent_spec().model_copy(update={"space_permissions": ("workroom",)})
    parent_spec = child_spec.model_copy(
        update={
            "agent_id": "space_read_manager",
            "memory_namespace": "agent/space_read_manager",
            "allow_subagents": True,
            "max_subagents": 1,
        }
    )
    factory = AgentFactory(AgentRegistry([parent_spec, child_spec]))
    agents = AgentInstanceService(database, factory, MemoryService(database))
    parent = agents.create(
        AgentInstanceCreate(agent_id=parent_spec.agent_id, project_id=project["id"])
    )
    child = agents.create(
        AgentInstanceCreate(agent_id=child_spec.agent_id, project_id=project["id"])
    )
    backend = FakeReadBackend()
    computer = ComputerRuntime(database, backend)
    supervisor = Supervisor(WorkerRegistry(((descriptor_for_computer_worker(), computer),)))
    tasks = TaskService(database, settings, agent_factory=factory, computer_supervisor=supervisor)
    submits = AgentExecutionService(database, factory, tasks)
    with database.transaction() as session:
        for instance in (parent, child):
            permission = session.get(AgentComputerPermission, instance.id)
            permission.enabled = permission.files = permission.browser = True
            permission.updated_at = utcnow()
    resources = AgentSpaceResourceService(database, factory, settings.runtime_dir)
    resource = resources.provision(
        instance_id=parent.id,
        space_id="workroom",
        resource_id="documents",
        operations=("list", "read"),
        files=(("notes.txt", "dual-approved evidence"),),
    )
    approvals = []
    for instance in (parent, child):
        intent = resources.intents.create(
            instance_id=instance.id,
            space_id="workroom",
            resource_kind="files",
            resource_id="documents",
            operations=("list", "read"),
            ttl_seconds=600,
        )
        approvals.append(
            resources.activate(
                instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
            )
        )
    controls = CampaignControlService(database)
    budget = reserve(controls, campaign["id"], parent.id, key="dual-space-budget", units=3)
    delegations = AgentDelegationService(database, factory, controls)
    delegation = delegations.create(
        parent.id,
        AgentDelegationCreate(
            child_instance_id=child.id,
            campaign_id=campaign["id"],
            budget_reservation_id=budget.id,
            idempotency_key="dual-space-delegation",
            objective="Read the explicitly shared document",
            allowed_task_kinds=["computer_run"],
            required_capabilities=["computer_read"],
            max_depth=1,
            budget_resource="agent_steps",
            budget_units=3,
        ),
    )
    runner = Runner(
        tasks,
        computer_supervisor=supervisor,
        space_file_gateway=SpaceFileReadGateway(
            database, factory, settings.runtime_dir, computer, task_service=tasks
        ),
    )
    return dict(
        runner=runner,
        parent=parent,
        child=child,
        factory=factory,
        agents=agents,
        backend=backend,
        computer=computer,
        tasks=tasks,
        submits=submits,
        resources=resources,
        resource=resource,
        parent_approval=approvals[0],
        child_approval=approvals[1],
        controls=controls,
        delegations=delegations,
        delegation=delegation,
        budget=budget,
        campaign=campaign,
    )


def _request(env, action="files_read", **overrides):
    payload = {
        "action": action,
        "path": "notes.txt" if action == "files_read" else "",
        "space_activation_id": env["child_approval"].id,
        "parent_space_activation_id": env["parent_approval"].id,
    }
    payload.update(overrides)
    return AgentTaskCreate.model_validate(
        {
            "delegation_id": env["delegation"].id,
            "task": {
                "kind": "computer_run",
                "campaign_id": env["campaign"]["id"],
                "payload": payload,
            },
        }
    )


@pytest.mark.parametrize("action", ["files_read", "files_list"])
def test_independently_approved_parent_and_child_execute_scoped_task(
    database, settings, client, action
):
    env = _delegated_env(database, settings, client)
    try:
        body = _request(env, action)
        task, created = env["submits"].submit(env["child"].id, body, None)
    except (ValidationError, DomainError) as error:
        pytest.fail(f"Dual-approved delegated Space execution is missing: {error}")
    assert created and task.status == State.PENDING
    claimed = env["tasks"].claim()
    assert claimed is not None and claimed.task_id == task.id
    assert claimed.computer_request.schema_version == "zelo-computer-execution-v3"
    assert claimed.computer_request.parent_space_activation_id == env["parent_approval"].id
    asyncio.run(env["runner"]._run(claimed))
    done = env["tasks"].get(task.id)
    assert done.status == State.SUCCEEDED, done.error
    if action == "files_read":
        assert done.result["files_read"]["text"] == "dual-approved evidence"
    else:
        assert [row["path"] for row in done.result["files_list"]["entries"]] == ["notes.txt"]
    assert all(call[0] == "control" for call in env["backend"].calls)
    assert {call[1] for call in env["backend"].calls} == {env["parent"].id, env["child"].id}


def test_delegated_claim_direct_gateway_returns_content_and_correlated_audit(
    database, settings, client
):
    from sqlalchemy import select

    from app.models import AuditEvent

    env = _delegated_env(database, settings, client)
    task, _ = env["submits"].submit(env["child"].id, _request(env), None)
    claimed = env["tasks"].claim()
    result = asyncio.run(
        env["runner"].space_file_gateway.read(
            execution=claimed,
            activation_id=env["child_approval"].id,
            relative_path="notes.txt",
        )
    )
    assert result.text == "dual-approved evidence"
    with database.read() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.task_id == task.id, AuditEvent.event == "space_file_read_completed"
            )
        )
        assert event.details["delegation_id"] == env["delegation"].id
        assert event.details["parent_activation_id"] == env["parent_approval"].id
        assert "dual-approved evidence" not in str(event.details)
