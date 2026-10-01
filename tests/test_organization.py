"""v0.14 organizational hierarchy contract tests."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec
from app.agent_instances import AgentInstanceService
from app.memory import MemoryService
from app.models import OrganizationAssignment, utcnow
from app.organization import (
    MANAGING_DIRECTOR_PRINCIPAL,
    ZELO_CEO_PRINCIPAL,
    OrganizationService,
)
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    OrganizationAssignmentCreate,
    OrganizationAssignmentEnd,
    OrganizationUnitCreate,
    ProjectCreate,
)
from tests.conftest import AUTH


def org_spec(agent_id: str, level: str) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        role=agent_id,
        organizational_level=level,
        objective=f"Serve as bounded {level}.",
        capabilities=(f"{level}_coordination",),
        allowed_task_kinds=(),
        allowed_tools=(),
        permissions=(),
        procedures=("organization",),
        memory_namespace=f"agent/{agent_id}",
        lifecycle="active",
    )


def services(database):
    specs = (
        org_spec("vp_engineering", "vp"),
        org_spec("software_director", "director"),
        org_spec("backend_manager", "manager"),
        org_spec("coding_specialist", "specialist"),
        org_spec("other_specialist", "specialist"),
    )
    factory = AgentFactory(AgentRegistry(specs))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    organization = OrganizationService(database, factory)
    project = ProjectService(database).create(
        ProjectCreate(name="Organization", summary="Hierarchy test project.")
    )
    return instances, organization, project


def build_units(organization: OrganizationService):
    division = organization.create_unit(
        OrganizationUnitCreate(name="Engineering", unit_type="division")
    )
    department = organization.create_unit(
        OrganizationUnitCreate(
            name="Software Engineering",
            unit_type="department",
            parent_unit_id=division.id,
        )
    )
    team = organization.create_unit(
        OrganizationUnitCreate(
            name="Backend Engineering",
            unit_type="team",
            parent_unit_id=department.id,
        )
    )
    return division, department, team


def build_instances(instances: AgentInstanceService, project_id: str):
    return {
        agent_id: instances.create(AgentInstanceCreate(agent_id=agent_id, project_id=project_id))
        for agent_id in (
            "vp_engineering",
            "software_director",
            "backend_manager",
            "coding_specialist",
            "other_specialist",
        )
    }


def test_full_company_reporting_chain_and_reserved_principals(database):
    instances, organization, project = services(database)
    division, department, team = build_units(organization)
    agents = build_instances(instances, project.id)

    vp = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["vp_engineering"].id,
            unit_id=division.id,
            level="vp",
        )
    )
    director = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["software_director"].id,
            unit_id=department.id,
            level="director",
            reports_to_assignment_id=vp.id,
        )
    )
    manager = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["backend_manager"].id,
            unit_id=team.id,
            level="manager",
            reports_to_assignment_id=director.id,
        )
    )
    specialist = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["coding_specialist"].id,
            unit_id=team.id,
            level="specialist",
            reports_to_assignment_id=manager.id,
        )
    )

    assert vp.reports_to_principal == ZELO_CEO_PRINCIPAL
    assert director.reports_to_assignment_id == vp.id
    assert manager.reports_to_assignment_id == director.id
    assert specialist.reports_to_assignment_id == manager.id

    snapshot = organization.snapshot()
    assert snapshot.managing_director.id == MANAGING_DIRECTOR_PRINCIPAL
    assert snapshot.managing_director.kind == "human"
    assert snapshot.managing_director.reports_to is None
    assert snapshot.ceo.id == ZELO_CEO_PRINCIPAL
    assert snapshot.ceo.kind == "system"
    assert snapshot.ceo.reports_to == MANAGING_DIRECTOR_PRINCIPAL
    assert [unit.unit_type for unit in snapshot.units] == [
        "division",
        "department",
        "team",
    ]
    assert [assignment.level for assignment in snapshot.assignments] == [
        "vp",
        "director",
        "manager",
        "specialist",
    ]


def test_assignment_level_must_match_trusted_agent_spec(database):
    instances, organization, project = services(database)
    division, _department, _team = build_units(organization)
    agents = build_instances(instances, project.id)

    with pytest.raises(DomainError, match="does not match AgentSpec"):
        organization.create_assignment(
            OrganizationAssignmentCreate(
                agent_instance_id=agents["coding_specialist"].id,
                unit_id=division.id,
                level="vp",
            )
        )


def test_unit_and_reporting_shape_fail_closed(database):
    instances, organization, project = services(database)
    division, department, team = build_units(organization)
    agents = build_instances(instances, project.id)

    with pytest.raises(DomainError, match="Division cannot"):
        organization.create_unit(
            OrganizationUnitCreate(
                name="Invalid Division",
                unit_type="division",
                parent_unit_id=division.id,
            )
        )
    with pytest.raises(DomainError, match="Department must belong"):
        organization.create_unit(
            OrganizationUnitCreate(
                name="Invalid Department",
                unit_type="department",
                parent_unit_id=team.id,
            )
        )

    vp = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["vp_engineering"].id,
            unit_id=division.id,
            level="vp",
        )
    )
    with pytest.raises(DomainError, match="requires a reporting parent"):
        organization.create_assignment(
            OrganizationAssignmentCreate(
                agent_instance_id=agents["software_director"].id,
                unit_id=department.id,
                level="director",
                reports_to_assignment_id=None,
            )
        )

    director = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["software_director"].id,
            unit_id=department.id,
            level="director",
            reports_to_assignment_id=vp.id,
        )
    )
    with pytest.raises(DomainError, match="specialist must report to manager"):
        organization.create_assignment(
            OrganizationAssignmentCreate(
                agent_instance_id=agents["coding_specialist"].id,
                unit_id=team.id,
                level="specialist",
                reports_to_assignment_id=director.id,
            )
        )


def test_active_assignment_blocks_agent_lifecycle_until_assignment_ends(database):
    instances, organization, project = services(database)
    division, _department, _team = build_units(organization)
    agents = build_instances(instances, project.id)
    vp = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["vp_engineering"].id,
            unit_id=division.id,
            level="vp",
        )
    )

    with pytest.raises(DomainError, match="assignment must end"):
        instances.transition(
            agents["vp_engineering"].id,
            AgentInstanceTransition(action="pause"),
        )

    ended = organization.end_assignment(
        vp.id,
        OrganizationAssignmentEnd(reason="Leadership rotation."),
    )
    assert ended.status == "ended"

    paused = instances.transition(
        agents["vp_engineering"].id,
        AgentInstanceTransition(action="pause"),
    )
    assert paused.status == "paused"


def test_parent_assignment_cannot_end_before_active_reports(database):
    instances, organization, project = services(database)
    division, department, _team = build_units(organization)
    agents = build_instances(instances, project.id)

    vp = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["vp_engineering"].id,
            unit_id=division.id,
            level="vp",
        )
    )
    director = organization.create_assignment(
        OrganizationAssignmentCreate(
            agent_instance_id=agents["software_director"].id,
            unit_id=department.id,
            level="director",
            reports_to_assignment_id=vp.id,
        )
    )

    with pytest.raises(DomainError, match="active reports"):
        organization.end_assignment(
            vp.id,
            OrganizationAssignmentEnd(reason="Cannot end yet."),
        )

    organization.end_assignment(
        director.id,
        OrganizationAssignmentEnd(reason="Director assignment completed."),
    )
    ended = organization.end_assignment(
        vp.id,
        OrganizationAssignmentEnd(reason="VP assignment completed."),
    )
    assert ended.status == "ended"


def test_database_rejects_direct_specialist_to_vp_promotion(database):
    instances, organization, project = services(database)
    division, _department, _team = build_units(organization)
    agents = build_instances(instances, project.id)

    with pytest.raises(IntegrityError, match="relation is invalid"):
        with database.transaction() as session:
            session.add(
                OrganizationAssignment(
                    agent_instance_id=agents["coding_specialist"].id,
                    unit_id=division.id,
                    level="vp",
                    reports_to_assignment_id=None,
                    status="active",
                    created_at=utcnow(),
                )
            )
            session.flush()


def test_default_application_exposes_reserved_organization_snapshot(client):
    response = client.get("/organization/snapshot", headers=AUTH)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["managing_director"] == {
        "id": MANAGING_DIRECTOR_PRINCIPAL,
        "role": "managing_director",
        "kind": "human",
        "reports_to": None,
    }
    assert payload["ceo"] == {
        "id": ZELO_CEO_PRINCIPAL,
        "role": "ceo",
        "kind": "system",
        "reports_to": MANAGING_DIRECTOR_PRINCIPAL,
    }


def test_organization_routes_require_operator_auth(client):
    bad_auth = {"Authorization": "Bearer invalid-operator-token"}
    assert client.get("/organization/snapshot", headers=bad_auth).status_code == 401
    assert client.get("/organization/units", headers=bad_auth).status_code == 401
    assert client.get("/organization/assignments", headers=bad_auth).status_code == 401
