"""v0.14 generic Agent Factory execution identity tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_delegations import AgentDelegationService
from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec, default_agent_registry
from app.agent_instances import AgentInstanceService
from app.campaign_control import CampaignControlService
from app.campaigns import CampaignService
from app.memory import MemoryService
from app.models import AgentInstance, utcnow
from app.policy import DomainError, State
from app.projects import ProjectService
from app.schemas import (
    AgentDelegationCreate,
    AgentDelegationRevoke,
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    CampaignBudgetReserve,
    CampaignBudgetSet,
    CampaignCreate,
    DeveloperRunInput,
    GoalCreate,
    GoalSpec,
    ProjectCreate,
    ResearchRunInput,
)
from app.tasks import TaskService
from tests.test_independent_review import review_runtime


class StubGateway:
    config_version = "agent-execution-test-v1"

    @staticmethod
    def select(profile: str) -> tuple[str, str]:
        assert profile == "general"
        return "local_general", "test-general-model"


def project(database, name: str = "Agent execution"):
    return ProjectService(database).create(
        ProjectCreate(name=name, summary="Bounded Agent Factory execution.")
    )


def campaign(database, project_id: str):
    projects = ProjectService(database)
    goal = projects.create_goal(
        project_id,
        GoalCreate(
            title="Execute specialist work",
            spec=GoalSpec(
                objective="Bind specialist work to exact persisted agent identity.",
                success_criteria=["identity survives inspection"],
                constraints=["Core retains task authority"],
            ),
        ),
    )
    return CampaignService(database).create(
        goal.id,
        CampaignCreate(title="Agent execution campaign"),
    )


def direct_services(database, settings, *, developer_runtime=None):
    factory = AgentFactory(default_agent_registry())
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database,
        settings,
        gateway=StubGateway(),
        developer_runtime=developer_runtime,
        agent_factory=factory,
    )
    execution = AgentExecutionService(database, factory, tasks)
    return factory, instances, tasks, execution


def test_research_agent_task_binds_exact_identity_and_replays(database, settings):
    _factory, instances, tasks, execution = direct_services(database, settings)
    owned_project = project(database)
    instance = instances.create(
        AgentInstanceCreate(agent_id="research", project_id=owned_project.id)
    )
    body = AgentTaskCreate(
        task=ResearchRunInput(
            kind="research_run",
            payload={"query": "Inspect the current project state."},
        )
    )

    created, was_created = execution.submit(instance.id, body, "agent-research:one")
    replayed, was_replayed = execution.submit(instance.id, body, "agent-research:one")

    assert was_created is True
    assert was_replayed is False
    assert replayed.id == created.id
    assert created.status == State.PENDING
    assert created.agent_execution_spec is not None
    assert created.agent_execution_spec.agent_instance_id == instance.id
    assert created.agent_execution_spec.project_id == owned_project.id
    assert created.agent_execution_spec.spec.agent_id == "research"
    assert created.agent_execution_spec.task_kind == "research_run"
    assert created.agent_execution_spec.delegation is None

    events = tasks.events(created.id, 100, 0)
    bound = [event for event in events if event.event == "agent_execution_bound"]
    assert len(bound) == 1
    assert bound[0].details["agent_instance_id"] == instance.id


def test_pre_hierarchy_specialist_instance_remains_execution_compatible(
    database,
    settings,
):
    factory, _instances, tasks, execution = direct_services(database, settings)
    owned_project = project(database, "Legacy specialist")
    spec = factory.registry.get_agent("research")
    assert spec is not None
    legacy_spec = spec.model_dump(mode="json")
    legacy_spec.pop("organizational_level")

    now = utcnow()
    with database.transaction() as session:
        instance = AgentInstance(
            agent_id=spec.agent_id,
            agent_version=spec.version,
            registry_version=factory.registry.version,
            project_id=owned_project.id,
            spec=legacy_spec,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(instance)
        session.flush()
        instance_id = instance.id

    task, created = execution.submit(
        instance_id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Verify legacy Specialist compatibility."},
            )
        ),
        "agent-research:legacy-specialist",
    )

    assert created is True
    assert task.agent_execution_spec is not None
    assert task.agent_execution_spec.spec.organizational_level == "specialist"

    claimed = tasks.claim()
    assert claimed is not None
    assert claimed.task_id == task.id
    assert claimed.agent_execution is not None
    assert claimed.agent_execution.spec.organizational_level == "specialist"


def test_agent_idempotency_key_cannot_replay_across_instances(database, settings):
    _factory, instances, _tasks, execution = direct_services(database, settings)
    first_project = project(database, "First")
    second_project = project(database, "Second")
    first = instances.create(AgentInstanceCreate(agent_id="research", project_id=first_project.id))
    second = instances.create(
        AgentInstanceCreate(agent_id="research", project_id=second_project.id)
    )
    body = AgentTaskCreate(
        task=ResearchRunInput(kind="research_run", payload={"query": "Same request."})
    )

    execution.submit(first.id, body, "agent-research:shared")
    with pytest.raises(DomainError, match="Idempotency key"):
        execution.submit(second.id, body, "agent-research:shared")


def test_paused_research_agent_task_is_cancelled_at_claim(database, settings):
    _factory, instances, tasks, execution = direct_services(database, settings)
    owned_project = project(database)
    instance = instances.create(
        AgentInstanceCreate(agent_id="research", project_id=owned_project.id)
    )
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "This must not run after pause."},
            )
        ),
        "agent-research:pause",
    )

    instances.transition(instance.id, AgentInstanceTransition(action="pause"))

    assert tasks.claim() is None
    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "agent_execution_invalid"


def test_coding_agent_approval_revalidates_identity(
    database,
    developer_case,
):
    runtime, _producer, _reviewer = review_runtime(developer_case)
    settings = runtime.settings
    _factory, instances, tasks, execution = direct_services(
        database,
        settings,
        developer_runtime=runtime,
    )
    owned_project = project(database)
    instance = instances.create(AgentInstanceCreate(agent_id="coding", project_id=owned_project.id))

    task, created = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=DeveloperRunInput(
                kind="developer_run",
                payload={"instruction": "Make one bounded reviewed change."},
            )
        ),
        "agent-coding:approval",
    )

    assert created is True
    assert task.status == State.AWAITING_APPROVAL
    assert task.agent_execution_spec is not None
    assert task.agent_execution_spec.spec.agent_id == "coding"
    assert task.developer_spec is not None
    assert tuple(task.developer_spec.worker_selection.requested_capabilities) == (
        "zelo_core_development",
    )
    assert task.developer_spec.reviewer is not None

    instances.transition(instance.id, AgentInstanceTransition(action="pause"))
    with pytest.raises(DomainError):
        tasks.decide(task.approvals[0].id, "approve")

    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "approval_invalidated"


def manager_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="manager",
        role="manager",
        objective="Delegate bounded research work.",
        capabilities=("delegation",),
        allowed_task_kinds=("research_run",),
        allowed_tools=(),
        permissions=("delegate_research",),
        procedures=("delegation",),
        memory_namespace="agent/manager",
        lifecycle="active",
        allow_subagents=True,
        max_subagents=2,
    )


def child_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="research_child",
        role="research_child",
        objective="Perform bounded delegated research.",
        capabilities=("research",),
        allowed_task_kinds=("research_run",),
        allowed_tools=("list_workspace_files", "read_workspace_file", "repo_status"),
        permissions=("workspace_read",),
        procedures=("evidence_retrieval",),
        memory_namespace="agent/research_child",
        lifecycle="active",
    )


def delegated_services(database, settings):
    factory = AgentFactory(AgentRegistry((manager_spec(), child_spec())))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    controls = CampaignControlService(database)
    delegations = AgentDelegationService(database, factory, controls)
    tasks = TaskService(
        database,
        settings,
        gateway=StubGateway(),
        agent_factory=factory,
    )
    execution = AgentExecutionService(database, factory, tasks)
    return instances, controls, delegations, tasks, execution


def make_delegation(database, settings):
    instances, controls, delegations, tasks, execution = delegated_services(
        database,
        settings,
    )
    owned_project = project(database, "Delegated")
    run_campaign = campaign(database, owned_project.id)
    parent = instances.create(AgentInstanceCreate(agent_id="manager", project_id=owned_project.id))
    child = instances.create(
        AgentInstanceCreate(agent_id="research_child", project_id=owned_project.id)
    )
    controls.set_budget(
        run_campaign.id,
        "agent_steps",
        CampaignBudgetSet(limit_units=10),
    )
    reservation = controls.reserve_budget(
        run_campaign.id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=parent.id,
            idempotency_key="agent-exec-budget",
            units=2,
            ttl_seconds=3600,
        ),
    )
    delegation = delegations.create(
        parent.id,
        AgentDelegationCreate(
            child_instance_id=child.id,
            campaign_id=run_campaign.id,
            budget_reservation_id=reservation.id,
            idempotency_key="agent-exec-delegation",
            objective="Investigate one bounded question.",
            allowed_task_kinds=["research_run"],
            required_capabilities=["research"],
            max_depth=2,
            budget_resource="agent_steps",
            budget_units=2,
        ),
    )
    return (
        instances,
        controls,
        delegations,
        tasks,
        execution,
        run_campaign,
        parent,
        child,
        delegation,
    )


def test_delegated_task_carries_exact_contract_and_revocation_blocks_claim(
    database,
    settings,
):
    (
        _instances,
        _controls,
        delegations,
        tasks,
        execution,
        run_campaign,
        _parent,
        child,
        delegation,
    ) = make_delegation(database, settings)

    task, created = execution.submit(
        child.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Investigate delegated evidence."},
                campaign_id=run_campaign.id,
            ),
            delegation_id=delegation.id,
        ),
        "agent-child:delegated",
    )

    assert created is True
    assert task.agent_execution_spec is not None
    bound = task.agent_execution_spec.delegation
    assert bound is not None
    assert bound.delegation_id == delegation.id
    assert bound.request_hash == delegation.request_hash
    assert bound.child_instance_id == child.id
    assert bound.campaign_id == run_campaign.id

    delegations.revoke(
        delegation.id,
        AgentDelegationRevoke(reason="Delegation no longer authorized."),
    )

    assert tasks.claim() is None
    cancelled = tasks.get(task.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "agent_execution_invalid"


def test_execution_binding_is_database_immutable_and_campaign_scoped(
    database,
    settings,
):
    (
        _instances,
        _controls,
        _delegations,
        _tasks,
        execution,
        run_campaign,
        _parent,
        child,
        delegation,
    ) = make_delegation(database, settings)
    other_project = project(database, "Wrong project")
    wrong_campaign = campaign(database, other_project.id)

    with pytest.raises(DomainError, match="delegation Campaign"):
        execution.submit(
            child.id,
            AgentTaskCreate(
                task=ResearchRunInput(
                    kind="research_run",
                    payload={"query": "Wrong campaign."},
                    campaign_id=wrong_campaign.id,
                ),
                delegation_id=delegation.id,
            ),
            "agent-child:wrong-campaign",
        )

    task, _ = execution.submit(
        child.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Immutable identity."},
                campaign_id=run_campaign.id,
            ),
            delegation_id=delegation.id,
        ),
        "agent-child:immutable",
    )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE tasks SET agent_execution_spec = NULL WHERE id = :task_id"),
                {"task_id": task.id},
            )
