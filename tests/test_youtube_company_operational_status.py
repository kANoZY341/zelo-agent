"""Real project-bound YouTube company status; visibility cannot confer authority."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_factory import default_agent_registry
from app.application import create_app
from app.models import (
    AgentDelegation,
    AgentInstance,
    OrganizationAssignment,
    Project,
    Task,
    utcnow,
)
from app.policy import DomainError, State
from app.projects import ProjectService
from app.schemas import (
    AgentTaskCreate,
    ProjectCreate,
    ResearchRunInput,
)
from app.youtube_company_operational_status import (
    YoutubeCompanyOperationalStatus,
    YoutubeCompanyOperationalStatusService,
)
from app.youtube_company_team import TEAM_ROLE_IDS, youtube_company_registry_digest
from tests.conftest import AUTH
from tests.test_youtube_company_team import (
    _approved,
    _company_director_ready_to_hire,
    _config,
    _real_company_graph,
)


def _view(database, project_id, *, registry=None, now=None):
    if registry is None:
        registry, _skills = _approved(database)
    return YoutubeCompanyOperationalStatusService(database).preview(
        project_id, registry=registry, now=now
    )


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in (AgentInstance, OrganizationAssignment, AgentDelegation, Task, Project)
        )


def test_empty_project_reports_no_company_and_has_no_side_effects(database):
    project = ProjectService(database).create(
        ProjectCreate(name="Empty company", summary="No organizational runtime yet")
    )
    before = _counts(database)
    status = _view(database, project.id, registry=default_agent_registry())
    assert status.schema_version == "zelo-youtube-company-operational-status-v1"
    assert status.project_id == project.id
    assert status.project_name == "Empty company"
    assert status.project_status == "active"
    assert status.actual_registry_version == default_agent_registry().version
    assert not status.company_registry_selected
    assert status.required_company_registry_version == youtube_company_registry_digest()
    assert status.unambiguous_active_role_instance_count == 0
    assert status.roles_with_consistent_chain_evidence == 0
    assert status.all_four_reporting_chains_consistent is False
    assert tuple(x.role_agent_id for x in status.roles) == TEAM_ROLE_IDS
    assert [x.observed_status for x in status.roles] == ["not_created"] * 4
    assert all(x.project_role_research_tasks_24h == 0 for x in status.roles)
    assert status.real_project_state_not_a_design_preview
    assert status.does_not_approve_bootstrap_or_new_teams
    assert status.does_not_grant_execution_hiring_or_spending
    assert status.campaign_reservations_do_not_prove_currency_cost
    assert _counts(database) == before

    assert (
        YoutubeCompanyOperationalStatus.model_validate_json(status.model_dump_json(), strict=True)
        == status
    )


def test_real_persisted_company_reports_full_ceo_vp_director_manager_specialist_chain(
    database, settings
):
    company = _real_company_graph(database, settings)
    before = _counts(database)
    report = _view(database, company["project"].id)
    assert report.company_registry_selected
    assert report.unambiguous_active_role_instance_count == 4
    assert report.roles_with_consistent_chain_evidence == 4
    assert report.all_four_reporting_chains_consistent
    assert [item.organizational_level for item in report.roles] == [
        "vp",
        "director",
        "manager",
        "specialist",
    ]
    assert [item.observed_status for item in report.roles] == [
        "root_assigned",
        "delegated_chain_intact",
        "delegated_chain_intact",
        "delegated_chain_intact",
    ]
    assert [item.reports_to_agent_id for item in report.roles] == [
        None,
        "youtube",
        "youtube_content",
        "youtube_research",
    ]
    assert report.roles[0].agent_instance_id == company["vp"].id
    assert report.roles[0].incoming_delegation_id is None
    assert report.roles[0].organization_unit_id == company["division"].id
    for index, entry in enumerate(company["created"], start=1):
        row = report.roles[index]
        assert row.agent_instance_id == entry.child_instance.id
        assert row.assignment_id == entry.child_assignment.id
        assert row.incoming_delegation_id == entry.delegation.id
        assert row.campaign_id == company["campaign"].id
        assert row.reservation_expires_at is not None
        assert row.reservation_expires_at > report.observed_at
        assert row.evidence_of_active_reporting_chain
        assert row.does_not_grant_execution_or_hiring_authority
    assert _counts(database) == before


def test_partial_company_distinguishes_director_and_not_yet_hired_roles(database, settings):
    company = _company_director_ready_to_hire(database, settings)
    status = _view(database, company["project"].id)
    assert [role.observed_status for role in status.roles] == [
        "root_assigned",
        "delegated_chain_intact",
        "not_created",
        "not_created",
    ]
    assert status.roles_with_consistent_chain_evidence == 2
    assert status.unambiguous_active_role_instance_count == 2
    assert not status.all_four_reporting_chains_consistent


def test_revoked_top_delegation_cascades_to_operational_status(database, settings):
    from app.agent_delegations import AgentDelegationService
    from app.schemas import AgentDelegationRevoke

    company = _real_company_graph(database, settings)
    AgentDelegationService(database, company["factory"], company["controls"]).revoke(
        company["created"][0].delegation.id,
        AgentDelegationRevoke(reason="Operator stopped VP funding"),
    )
    report = _view(database, company["project"].id)
    assert [x.observed_status for x in report.roles] == [
        "root_assigned",
        "delegation_missing_or_ambiguous",
        "parent_chain_incomplete",
        "parent_chain_incomplete",
    ]
    assert report.roles_with_consistent_chain_evidence == 1
    assert not report.all_four_reporting_chains_consistent
    assert report.unambiguous_active_role_instance_count == 4


def test_expired_reservation_and_project_pause_not_misreported_active(database, settings):
    company = _real_company_graph(database, settings)
    later = utcnow() + timedelta(hours=3)
    report = _view(database, company["project"].id, now=later)
    assert [x.observed_status for x in report.roles] == [
        "root_assigned",
        "campaign_or_reservation_invalid",
        "parent_chain_incomplete",
        "parent_chain_incomplete",
    ]
    assert not report.all_four_reporting_chains_consistent
    with database.transaction() as session:
        project = session.get(Project, company["project"].id)
        project.status = "paused"
    paused = _view(database, company["project"].id)
    assert all(x.observed_status == "project_inactive" for x in paused.roles)
    assert paused.unambiguous_active_role_instance_count == 4
    assert paused.roles_with_consistent_chain_evidence == 0


def test_selected_registry_vs_historical_company_identity(database, settings):
    company = _real_company_graph(database, settings)
    before = _counts(database)
    status = _view(database, company["project"].id, registry=default_agent_registry())
    assert not status.company_registry_selected
    assert not status.all_four_reporting_chains_consistent
    assert [x.observed_status for x in status.roles] == ["registry_disabled"] * 4
    assert status.unambiguous_active_role_instance_count == 4
    assert _counts(database) == before


def test_real_task_activity_is_aggregated_by_project_and_exact_role(database, settings):
    company = _real_company_graph(database, settings)
    vp = company["vp"]
    leaf = company["created"][-1]
    vp_task, _ = company["execution"].submit(
        vp.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                campaign_id=company["campaign"].id,
                payload={"query": "Operator-approved strategy research evidence"},
            )
        ),
        "company-operational-status-vp",
    )
    leaf_task, _ = company["execution"].submit(
        leaf.child_instance.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                campaign_id=company["campaign"].id,
                payload={"query": "Operator-approved scripts research evidence"},
            ),
            delegation_id=leaf.delegation.id,
        ),
        "company-operational-status-leaf",
    )
    assert vp_task.status == leaf_task.status == State.PENDING
    report = _view(database, company["project"].id)
    assert [x.project_role_research_tasks_24h for x in report.roles] == [1, 0, 0, 1]
    assert [x.project_role_active_tasks for x in report.roles] == [1, 0, 0, 1]
    assert report.all_four_reporting_chains_consistent


def test_owner_api_reads_real_project_without_mutating_state(database, settings):
    company = _real_company_graph(database, settings)
    before = _counts(database)
    endpoint = f"/projects/{company['project'].id}/youtube-company-status"
    with TestClient(create_app(_config(settings)), headers=AUTH) as client:
        denied = client.get(endpoint, headers={"Authorization": "Bearer invalid"})
        assert denied.status_code == 401
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["company_registry_selected"] is True
        assert data["all_four_reporting_chains_consistent"] is True
        assert len(data["roles"]) == 4
        assert data["does_not_grant_execution_hiring_or_spending"] is True
        assert data["does_not_approve_bootstrap_or_new_teams"] is True
        unknown = client.get(
            "/projects/00000000-0000-4000-8000-000000000000/youtube-company-status"
        )
        assert unknown.status_code == 404
    assert _counts(database) == before


def test_status_refuses_forged_authority_in_receipt(database, settings):
    company = _real_company_graph(database, settings)
    record = _view(database, company["project"].id).model_dump(mode="json")
    for field in (
        "does_not_grant_execution_hiring_or_spending",
        "does_not_approve_bootstrap_or_new_teams",
        "campaign_reservations_do_not_prove_currency_cost",
    ):
        altered = json.loads(json.dumps(record))
        altered[field] = False
        with pytest.raises(ValidationError):
            YoutubeCompanyOperationalStatus.model_validate_json(json.dumps(altered), strict=True)
    altered = json.loads(json.dumps(record))
    altered["roles"][0]["does_not_grant_execution_or_hiring_authority"] = False
    with pytest.raises(ValidationError):
        YoutubeCompanyOperationalStatus.model_validate_json(json.dumps(altered), strict=True)


def test_observation_rejects_invalid_clock_and_missing_project(database):
    with pytest.raises(DomainError, match="Project not found") as error:
        _view(database, "00000000-0000-4000-8000-000000000000")
    assert error.value.status_code == 404
    from datetime import datetime

    project = ProjectService(database).create(
        ProjectCreate(name="Invalid time", summary="Reject naive observation")
    )
    with pytest.raises(ValueError, match="clock_invalid"):
        _view(database, project.id, now=datetime(2026, 10, 12))


def test_status_payload_rejects_forged_agent_order_and_chain_evidence(database, settings):
    company = _real_company_graph(database, settings)
    record = _view(database, company["project"].id).model_dump(mode="json")

    wrong_role_order = json.loads(json.dumps(record))
    wrong_role_order["roles"][0]["role_agent_id"] = "youtube_content"
    with pytest.raises(ValidationError, match="evidence_shape_invalid"):
        YoutubeCompanyOperationalStatus.model_validate_json(
            json.dumps(wrong_role_order), strict=True
        )

    wrong_chain = json.loads(json.dumps(record))
    wrong_chain["roles"][1]["evidence_of_active_reporting_chain"] = False
    with pytest.raises(ValidationError, match="evidence_shape_invalid"):
        YoutubeCompanyOperationalStatus.model_validate_json(json.dumps(wrong_chain), strict=True)
    wrong_total = json.loads(json.dumps(record))
    wrong_total["roles_with_consistent_chain_evidence"] = 1
    with pytest.raises(ValidationError, match="evidence_shape_invalid"):
        YoutubeCompanyOperationalStatus.model_validate_json(json.dumps(wrong_total), strict=True)


def test_ambiguous_live_vp_does_not_select_arbitrary_reporting_identity(database, settings):
    from app.models import AgentInstance
    from app.youtube_company_team import trusted_youtube_company_specs

    company = _real_company_graph(database, settings)
    project_id = company["project"].id
    initial = _view(database, project_id)
    assert initial.all_four_reporting_chains_consistent
    with database.transaction() as session:
        duplicate = AgentInstance(
            agent_id="youtube",
            agent_version=1,
            registry_version=youtube_company_registry_digest(),
            project_id=project_id,
            spec=trusted_youtube_company_specs()[0].model_dump(mode="json"),
            status="active",
        )
        session.add(duplicate)
    report = _view(database, project_id)
    assert report.roles[0].observed_status == "ambiguous_instances"
    assert report.roles[0].agent_instance_id is None
    assert [r.observed_status for r in report.roles[1:]] == [
        "parent_chain_incomplete",
        "parent_chain_incomplete",
        "parent_chain_incomplete",
    ]
    assert report.unambiguous_active_role_instance_count == 3
    assert report.roles_with_consistent_chain_evidence == 0
    assert report.all_four_reporting_chains_consistent is False


def test_company_status_uses_one_sqlite_snapshot_across_concurrent_revocation(
    database, settings, monkeypatch
):
    """Writer commits mid-read, but response must not mix old/new org states."""

    from app.agent_delegations import AgentDelegationService
    from app.schemas import AgentDelegationRevoke
    from app.youtube_company_operational_status import (
        YoutubeCompanyOperationalStatusService,
    )

    company = _real_company_graph(database, settings)
    service = YoutubeCompanyOperationalStatusService(database)
    method = service._task_counts
    changed = []

    def writer_between_reads(session, project_id, agent_id, observed):
        if agent_id == "youtube_content" and not changed:
            AgentDelegationService(database, company["factory"], company["controls"]).revoke(
                company["created"][0].delegation.id,
                AgentDelegationRevoke(reason="Revoke from another connection mid-status read"),
            )
            changed.append(True)
        return method(session, project_id, agent_id, observed)

    monkeypatch.setattr(
        YoutubeCompanyOperationalStatusService,
        "_task_counts",
        staticmethod(writer_between_reads),
    )
    registry, _ = _approved(database)
    first = service.preview(company["project"].id, registry=registry)
    assert changed == [True]
    assert first.all_four_reporting_chains_consistent
    assert first.roles_with_consistent_chain_evidence == 4

    # A NEW observation sees the committed revocation; neither observation
    # may contain half pre/post data or mint fresh authority.
    second = service.preview(company["project"].id, registry=registry)
    assert [r.observed_status for r in second.roles] == [
        "root_assigned",
        "delegation_missing_or_ambiguous",
        "parent_chain_incomplete",
        "parent_chain_incomplete",
    ]
    assert second.roles_with_consistent_chain_evidence == 1
    assert not second.all_four_reporting_chains_consistent


def test_company_status_accepts_existing_valid_extra_evidence_capability_requirements(
    database, settings
):
    """Operational status follows real ChildCreation subset rules, not fixture equality."""

    company = _company_director_ready_to_hire(database, settings)
    request = company["manager_request"].model_copy(
        update={
            "required_capabilities": ["evidence_synthesis", "research"],
            "idempotency_key": "manager-with-two-reviewable-research-capabilities",
        }
    )
    manager = company["children"].create(company["director"].child_instance.id, request)
    assert manager.delegation.required_capabilities == (
        "evidence_synthesis",
        "research",
    ) or manager.delegation.required_capabilities == ["evidence_synthesis", "research"]
    report = _view(database, company["project"].id)
    assert [role.observed_status for role in report.roles] == [
        "root_assigned",
        "delegated_chain_intact",
        "delegated_chain_intact",
        "not_created",
    ]
    assert report.roles_with_consistent_chain_evidence == 3
    assert not report.all_four_reporting_chains_consistent
