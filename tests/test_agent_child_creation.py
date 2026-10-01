"""v0.14 hierarchy-aware atomic Agent Factory child creation tests."""

import pytest
from sqlalchemy import func, select

from app.agent_child_creation import AgentChildCreationService
from app.agent_delegations import AgentDelegationService
from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec
from app.agent_instances import AgentInstanceService
from app.campaign_control import CampaignControlService
from app.campaigns import CampaignService
from app.memory import MemoryService
from app.models import AgentInstance, OrganizationAssignment
from app.organization import OrganizationService
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import (
    AgentChildCreate,
    AgentDelegationRevoke,
    AgentInstanceCreate,
    CampaignBudgetReserve,
    CampaignBudgetSet,
    CampaignCreate,
    GoalCreate,
    GoalSpec,
    OrganizationAssignmentCreate,
    OrganizationUnitCreate,
    ProjectCreate,
)
from tests.conftest import AUTH


def spec(
    agent_id: str,
    level: str,
    *,
    allow_children: bool = False,
    max_children: int = 0,
) -> AgentSpec:
    capabilities = ("research",) if level == "specialist" else ("coordination",)
    return AgentSpec(
        agent_id=agent_id,
        role=agent_id,
        organizational_level=level,
        objective=f"Serve as bounded {level}.",
        capabilities=capabilities,
        allowed_task_kinds=("research_run",),
        allowed_tools=(),
        permissions=(("organization_hire",) if allow_children else ()),
        procedures=("organization",),
        memory_namespace=f"agent/{agent_id}",
        lifecycle="active",
        max_active_instances=8,
        allow_subagents=allow_children,
        max_subagents=max_children,
    )


def services(database):
    specs = (
        spec("vp_media", "vp", allow_children=True, max_children=4),
        spec("youtube_director", "director", allow_children=True, max_children=4),
        spec("youtube_manager", "manager", allow_children=True, max_children=4),
        spec("research_specialist", "specialist"),
        spec("wrong_director", "director"),
    )
    factory = AgentFactory(AgentRegistry(specs))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    organization = OrganizationService(database, factory)
    controls = CampaignControlService(database)
    children = AgentChildCreationService(database, factory)
    return factory, instances, organization, controls, children


def project_campaign(database):
    projects = ProjectService(database)
    project = projects.create(
        ProjectCreate(name="Media Factory", summary="Hierarchy child creation.")
    )
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Build media team",
            spec=GoalSpec(
                objective="Create a bounded hierarchy.",
                success_criteria=["trusted child agents"],
                constraints=["no hierarchy jumps"],
            ),
        ),
    )
    campaign = CampaignService(database).create(
        goal.id,
        CampaignCreate(title="Build media hierarchy"),
    )
    return project, campaign


def units(organization: OrganizationService):
    division = organization.create_unit(OrganizationUnitCreate(name="Media", unit_type="division"))
    department = organization.create_unit(
        OrganizationUnitCreate(
            name="YouTube",
            unit_type="department",
            parent_unit_id=division.id,
        )
    )
    team = organization.create_unit(
        OrganizationUnitCreate(
            name="YouTube Production",
            unit_type="team",
            parent_unit_id=department.id,
        )
    )
    return division, department, team


def reserve(
    controls: CampaignControlService,
    campaign_id: str,
    owner_id: str,
    key: str,
    *,
    units: int = 2,
):
    try:
        controls.set_budget(
            campaign_id,
            "agent_steps",
            CampaignBudgetSet(limit_units=100),
        )
    except DomainError:
        pass
    return controls.reserve_budget(
        campaign_id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=owner_id,
            idempotency_key=key,
            units=units,
            ttl_seconds=3600,
        ),
    )


def request(
    *,
    child_agent_id: str,
    unit_id: str,
    campaign_id: str,
    reservation_id: str,
    key: str,
    max_depth: int = 4,
) -> AgentChildCreate:
    return AgentChildCreate(
        child_agent_id=child_agent_id,
        unit_id=unit_id,
        campaign_id=campaign_id,
        budget_reservation_id=reservation_id,
        idempotency_key=key,
        objective="Perform bounded delegated research.",
        allowed_task_kinds=["research_run"],
        required_capabilities=(
            ["research"] if child_agent_id == "research_specialist" else ["coordination"]
        ),
        max_depth=max_depth,
        budget_resource="agent_steps",
        budget_units=2,
    )


def build_manager_parent(database):
    _factory, instances, organization, controls, children = services(database)
    project, campaign = project_campaign(database)
    division, department, team = units(organization)

    vp = instances.create(AgentInstanceCreate(agent_id="vp_media", project_id=project.id))
    vp_assignment = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=vp.id,
            unit_id=division.id,
            level="vp",
        )
    )
    director = instances.create(
        AgentInstanceCreate(agent_id="youtube_director", project_id=project.id)
    )
    director_assignment = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=director.id,
            unit_id=department.id,
            level="director",
            reports_to_assignment_id=vp_assignment.id,
        )
    )
    manager = instances.create(
        AgentInstanceCreate(agent_id="youtube_manager", project_id=project.id)
    )
    organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=manager.id,
            unit_id=team.id,
            level="manager",
            reports_to_assignment_id=director_assignment.id,
        )
    )
    return instances, organization, controls, children, project, campaign, team, manager


def test_manager_creates_specialist_atomically_and_replays(database):
    (
        _instances,
        organization,
        controls,
        children,
        _project,
        campaign,
        team,
        manager,
    ) = build_manager_parent(database)
    reservation = reserve(controls, campaign.id, manager.id, "budget:manager-child")
    body = request(
        child_agent_id="research_specialist",
        unit_id=team.id,
        campaign_id=campaign.id,
        reservation_id=reservation.id,
        key="child:manager-specialist",
    )

    created = children.create(manager.id, body)
    replayed = children.create(manager.id, body)

    assert replayed.id == created.id
    assert created.child_instance.spec.organizational_level == "specialist"
    assert created.child_assignment.level == "specialist"
    assert created.child_assignment.reports_to_assignment_id == created.parent_assignment_id
    assert created.child_assignment.unit_id == team.id
    assert created.delegation.parent_instance_id == manager.id
    assert created.delegation.child_instance_id == created.child_instance.id
    assert created.delegation.depth == 1
    assert created.delegation.status == "active"

    snapshot = organization.snapshot()
    assert any(item.agent_instance_id == created.child_instance.id for item in snapshot.assignments)


def test_child_creation_rejects_hierarchy_jump_without_side_effect(database):
    (
        _instances,
        _organization,
        controls,
        children,
        _project,
        campaign,
        team,
        manager,
    ) = build_manager_parent(database)
    reservation = reserve(controls, campaign.id, manager.id, "budget:jump")

    with database.read() as session:
        before_instances = session.scalar(select(func.count()).select_from(AgentInstance))
        before_assignments = session.scalar(
            select(func.count()).select_from(OrganizationAssignment)
        )

    with pytest.raises(DomainError, match="next organizational level"):
        children.create(
            manager.id,
            request(
                child_agent_id="wrong_director",
                unit_id=team.id,
                campaign_id=campaign.id,
                reservation_id=reservation.id,
                key="child:jump",
            ),
        )

    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentInstance)) == before_instances
        assert (
            session.scalar(select(func.count()).select_from(OrganizationAssignment))
            == before_assignments
        )


def test_child_creation_rejects_wrong_owner_budget_atomically(database):
    (
        instances,
        _organization,
        controls,
        children,
        project,
        campaign,
        team,
        manager,
    ) = build_manager_parent(database)
    other = instances.create(
        AgentInstanceCreate(agent_id="research_specialist", project_id=project.id)
    )
    reservation = reserve(controls, campaign.id, other.id, "budget:wrong-owner")

    with database.read() as session:
        before_instances = session.scalar(select(func.count()).select_from(AgentInstance))

    with pytest.raises(DomainError, match="budget proof"):
        children.create(
            manager.id,
            request(
                child_agent_id="research_specialist",
                unit_id=team.id,
                campaign_id=campaign.id,
                reservation_id=reservation.id,
                key="child:wrong-owner",
            ),
        )

    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentInstance)) == before_instances


def test_nested_vp_to_director_to_manager_inherits_depth(database):
    _factory, instances, organization, controls, children = services(database)
    project, campaign = project_campaign(database)
    division, department, team = units(organization)

    vp = instances.create(AgentInstanceCreate(agent_id="vp_media", project_id=project.id))
    organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=vp.id,
            unit_id=division.id,
            level="vp",
        )
    )

    vp_budget = reserve(controls, campaign.id, vp.id, "budget:vp")
    director_creation = children.create(
        vp.id,
        request(
            child_agent_id="youtube_director",
            unit_id=department.id,
            campaign_id=campaign.id,
            reservation_id=vp_budget.id,
            key="child:vp-director",
            max_depth=3,
        ),
    )
    assert director_creation.delegation.depth == 1

    director = director_creation.child_instance
    director_budget = reserve(controls, campaign.id, director.id, "budget:director")
    manager_creation = children.create(
        director.id,
        request(
            child_agent_id="youtube_manager",
            unit_id=team.id,
            campaign_id=campaign.id,
            reservation_id=director_budget.id,
            key="child:director-manager",
            max_depth=3,
        ),
    )

    assert manager_creation.child_assignment.level == "manager"
    assert manager_creation.delegation.depth == 2
    assert manager_creation.delegation.max_depth == 3
    assert (
        manager_creation.child_assignment.reports_to_assignment_id
        == director_creation.child_assignment.id
    )


def test_child_creation_route_requires_operator_auth(client):
    bad_auth = {"Authorization": "Bearer invalid-operator-token"}
    response = client.post(
        "/agents/00000000-0000-0000-0000-000000000000/children",
        headers=bad_auth,
        json={},
    )
    assert response.status_code == 401

    assert client.get("/organization/snapshot", headers=AUTH).status_code == 200


def test_permanent_reports_continue_to_consume_child_capacity_after_delegation_revoke(
    database,
):
    (
        _instances,
        _organization,
        controls,
        children,
        _project,
        campaign,
        team,
        manager,
    ) = build_manager_parent(database)
    delegations = AgentDelegationService(database, children.factory, controls)

    for index in range(4):
        reservation = reserve(
            controls,
            campaign.id,
            manager.id,
            f"budget:permanent:{index}",
        )
        created = children.create(
            manager.id,
            request(
                child_agent_id="research_specialist",
                unit_id=team.id,
                campaign_id=campaign.id,
                reservation_id=reservation.id,
                key=f"child:permanent:{index}",
            ),
        )
        delegations.revoke(
            created.delegation.id,
            AgentDelegationRevoke(reason="Initial delegated objective complete."),
        )

    fifth_reservation = reserve(
        controls,
        campaign.id,
        manager.id,
        "budget:permanent:overflow",
    )
    with pytest.raises(DomainError, match="child limit"):
        children.create(
            manager.id,
            request(
                child_agent_id="research_specialist",
                unit_id=team.id,
                campaign_id=campaign.id,
                reservation_id=fifth_reservation.id,
                key="child:permanent:overflow",
            ),
        )

    snapshot = _organization.snapshot()
    reports = [
        item
        for item in snapshot.assignments
        if item.status == "active"
        and item.reports_to_assignment_id is not None
        and item.level == "specialist"
    ]
    assert len(reports) == 4
