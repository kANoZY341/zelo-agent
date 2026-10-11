"""Real owner-approved YouTube VP/Director/Manager/Specialist integrated Core tests."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_factory import AgentFactory, default_agent_registry
from app.agent_instances import AgentInstanceService
from app.application import create_app
from app.campaign_control import CampaignControlService
from app.campaigns import CampaignService
from app.config import ROOT, Settings
from app.memory import MemoryService
from app.models import AgentChildCreation, AgentInstance, OrganizationAssignment, Task
from app.organization import OrganizationService
from app.policy import DomainError, State
from app.projects import ProjectService
from app.schemas import (
    AgentChildCreate,
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    CampaignBudgetReserve,
    CampaignBudgetSet,
    CampaignCreate,
    GoalCreate,
    GoalSpec,
    OrganizationAssignmentCreate,
    OrganizationUnitCreate,
    ProjectCreate,
    ResearchRunInput,
)
from app.youtube_company_team import (
    TEAM_LEVELS,
    TEAM_ROLE_IDS,
    YoutubeCompanyActivationRefusal,
    YoutubeCompanyCandidateView,
    admitted_youtube_company_registry,
    organization_coordination_skill,
    trusted_youtube_company_specs,
    youtube_company_candidate_view,
    youtube_company_registry_digest,
)
from app.youtube_research_specialist import (
    youtube_research_spec_digest,
)
from tests.conftest import AUTH
from tests.test_api import wait_for
from tests.test_research import call, final

NO_TEAM_CONFIG = {
    "pilot_spec_sha256": None,
    "company_approved_registry_sha256": None,
}


def _approved(database):
    return admitted_youtube_company_registry(
        database,
        pilot_spec_sha256=None,
        company_approved_registry_sha256=youtube_company_registry_digest(),
    )


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(klass))
            for klass in (AgentInstance, OrganizationAssignment, Task, AgentChildCreation)
        )


def _config(settings):
    return settings.model_copy(
        update={"youtube_company_approved_registry_sha256": youtube_company_registry_digest()}
    )


def test_disabled_by_default_does_not_change_registry_or_skill_or_database(database):
    before = _counts(database)
    registry, skills = admitted_youtube_company_registry(database, **NO_TEAM_CONFIG)
    assert registry.version == default_agent_registry().version
    assert registry.get_agent("youtube") is None
    assert registry.get_agent("youtube_research") is None
    assert skills.get("organization") is None
    assert _counts(database) == before

    view = youtube_company_candidate_view(registry)
    assert view.enabled_on_this_process is False
    assert view.required_operator_registry_sha256 == youtube_company_registry_digest()
    assert view.active_organizational_levels == TEAM_LEVELS
    assert tuple(role.agent_id for role in view.trusted_roles) == TEAM_ROLE_IDS
    assert view.requires_explicit_separate_operator_startup_opt_in
    assert view.active_instances_automatically_created is False
    assert view.campaign_budget_auto_reserved is False
    assert view.project_hierarchy_auto_created is False
    assert view.publishing_or_account_access_granted is False
    assert view.human_operator_required_for_org_mutation
    assert (
        YoutubeCompanyCandidateView.model_validate_json(view.model_dump_json(), strict=True) == view
    )
    assert _counts(database) == before


def test_invalid_approval_combination_or_stale_digest_refused_without_changes(
    database,
):
    before = _counts(database)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, youtube_company_approved_registry_sha256="YES")
    with pytest.raises(YoutubeCompanyActivationRefusal, match="operator_approval_stale"):
        admitted_youtube_company_registry(
            database,
            pilot_spec_sha256=None,
            company_approved_registry_sha256="0" * 64,
        )
    with pytest.raises(YoutubeCompanyActivationRefusal, match="mutually_exclusive"):
        admitted_youtube_company_registry(
            database,
            pilot_spec_sha256=youtube_research_spec_digest(),
            company_approved_registry_sha256=youtube_company_registry_digest(),
        )
    assert _counts(database) == before


def test_active_company_has_exact_harness_hierarchy_and_no_escalated_tool_grants(database):
    before = _counts(database)
    registry, skills = _approved(database)
    assert registry.version == youtube_company_registry_digest()
    assert registry.version != default_agent_registry().version
    assert tuple(x.agent_id for x in trusted_youtube_company_specs()) == TEAM_ROLE_IDS
    assert tuple(x.organizational_level for x in trusted_youtube_company_specs()) == TEAM_LEVELS
    assert skills.get("organization") == organization_coordination_skill()
    assert skills.get("organization").tools == ()
    assert skills.get("organization").task_kinds == ("research_run",)
    skills.validate_agents(registry.list_agents())
    for index, agent_id in enumerate(TEAM_ROLE_IDS):
        item = registry.get_agent(agent_id)
        assert item is not None
        assert item.lifecycle == "active"
        assert item.organizational_level == TEAM_LEVELS[index]
        assert item.allowed_task_kinds == ("research_run",)
        assert item.allowed_tools == ("list_workspace_files", "read_workspace_file", "repo_status")
        assert item.space_permissions == ()
        assert item.computer is None
        assert item.skill_learning == "disabled"
        assert item.worker_requirement.role == "research"
        assert item.worker_requirement.capabilities == ("research",)
        assert item.max_active_instances == 1
        assert not item.review_required
        if index < 3:
            assert item.allow_subagents and item.max_subagents == 1
            assert item.permissions == ("organization_hire", "workspace_read")
            assert item.skill_ids == ("evidence_retrieval", "organization")
            assert item.procedures == ("evidence_retrieval", "organization")
        else:
            assert not item.allow_subagents and item.max_subagents == 0
            assert item.permissions == ("workspace_read",)
            assert item.skill_ids == ("evidence_retrieval",)
            assert item.procedures == ("evidence_retrieval",)
    assert _counts(database) == before


def test_operator_only_candidate_api_and_real_activation_surface(settings, database):
    approved = _config(settings)
    before = _counts(database)
    with TestClient(create_app(settings), headers=AUTH) as client:
        resource = "/agent-factory/optional-teams/youtube-content"
        assert client.get(resource, headers={"Authorization": "Bearer bad"}).status_code == 401
        preview = client.get(resource)
        assert preview.status_code == 200, preview.text
        assert preview.json()["enabled_on_this_process"] is False
        assert _counts(database) == before

    with TestClient(create_app(approved), headers=AUTH) as client:
        preview = client.get(resource)
        assert preview.status_code == 200, preview.text
        assert preview.json()["enabled_on_this_process"] is True
        report = client.get("/agent-factory/activation")
        assert report.status_code == 200, report.text
        assert report.json()["status"] == "accepted"
        evaluations = report.json()["evaluations"]
        assert {e["agent_id"] for e in evaluations if e["agent_id"] in TEAM_ROLE_IDS} == set(
            TEAM_ROLE_IDS
        )
        assert all(e["status"] == "accepted" for e in evaluations)
        skills = client.get("/skills")
        if skills.status_code == 200:
            assert "organization" in skills.text
        assert _counts(database) == before


def test_same_exact_registry_restarts_with_live_company_but_unsafe_disable_denied(
    settings,
    database,
):
    approved = _config(settings)
    with TestClient(create_app(approved), headers=AUTH) as client:
        project = client.post(
            "/projects", json={"name": "Company restart", "summary": "Trusted org identity"}
        )
        assert project.status_code == 201
        root = client.post(
            "/agents",
            json={"agent_id": "youtube", "project_id": project.json()["id"]},
        )
        assert root.status_code == 201, root.text
        instance_id = root.json()["id"]
        assert root.json()["registry_version"] == youtube_company_registry_digest()
    with TestClient(create_app(approved), headers=AUTH) as client:
        assert client.get(f"/agents/{instance_id}").json()["status"] == "active"
    with pytest.raises(
        YoutubeCompanyActivationRefusal, match="disable_requires_retiring_live_agents"
    ):
        with TestClient(create_app(settings), headers=AUTH):
            pass
    with pytest.raises(YoutubeCompanyActivationRefusal, match="pilot_and_team_mutually_exclusive"):
        with TestClient(
            create_app(
                approved.model_copy(
                    update={"youtube_research_approved_spec_sha256": youtube_research_spec_digest()}
                )
            ),
            headers=AUTH,
        ):
            pass
    with TestClient(create_app(approved), headers=AUTH) as client:
        retired = client.post(f"/agents/{instance_id}/transition", json={"action": "retire"})
        assert retired.status_code == 200
        assert retired.json()["status"] == "retired"
    with TestClient(create_app(settings), headers=AUTH) as client:
        assert (
            client.get("/agent-factory/optional-teams/youtube-content").json()[
                "enabled_on_this_process"
            ]
            is False
        )


def test_company_optin_cannot_invalidate_previously_registered_active_instances(database):
    project = ProjectService(database).create(
        ProjectCreate(name="Existing baseline", summary="Nonretired actor")
    )
    default = AgentInstanceService(
        database, AgentFactory(default_agent_registry()), MemoryService(database)
    )
    older = default.create(AgentInstanceCreate(agent_id="research", project_id=project.id))
    before = _counts(database)
    with pytest.raises(
        YoutubeCompanyActivationRefusal, match="existing_live_agent_registry_incompatible"
    ):
        _approved(database)
    assert _counts(database) == before
    default.transition(older.id, AgentInstanceTransition(action="pause"))
    with pytest.raises(
        YoutubeCompanyActivationRefusal, match="existing_live_agent_registry_incompatible"
    ):
        _approved(database)
    default.transition(older.id, AgentInstanceTransition(action="retire"))
    assert _approved(database)[0].version == youtube_company_registry_digest()


def _real_company_graph(database, settings):
    """Use the actual Core Org and child-creation Services, no alternate factory."""

    from app.agent_child_creation import AgentChildCreationService
    from tests.test_agent_execution import direct_services

    registry, _skill_registry = _approved(database)
    factory, instances, tasks, execution = direct_services(
        database, settings, agent_factory=AgentFactory(registry)
    )
    organization = OrganizationService(database, factory)
    projects = ProjectService(database)
    project = projects.create(
        ProjectCreate(name="YouTube Company", summary="Manual owner-approved Media company")
    )
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Research five original YouTube ideas",
            spec=GoalSpec(
                objective="Use bounded read-only evidence to draft editorial options.",
                success_criteria=["sourced ideas reviewed by operator"],
                constraints=["no account access", "no publishing", "no additional tool grants"],
            ),
        ),
    )
    campaign = CampaignService(database).create(
        goal.id, CampaignCreate(title="Manual four-level organizational proof")
    )
    division = organization.create_unit(OrganizationUnitCreate(name="Media", unit_type="division"))
    department = organization.create_unit(
        OrganizationUnitCreate(name="YouTube", unit_type="department", parent_unit_id=division.id)
    )
    team = organization.create_unit(
        OrganizationUnitCreate(
            name="YouTube Research", unit_type="team", parent_unit_id=department.id
        )
    )

    vp = instances.create(AgentInstanceCreate(agent_id=TEAM_ROLE_IDS[0], project_id=project.id))
    vp_assignment = organization.create_assignment(
        OrganizationAssignmentCreate(agent_instance_id=vp.id, unit_id=division.id, level="vp")
    )
    assert vp_assignment.reports_to_principal == "zelo_ceo"
    controls = CampaignControlService(database)
    initial_budget = controls.set_budget(
        campaign.id, "agent_steps", CampaignBudgetSet(limit_units=20)
    )
    assert initial_budget.available_units == 20
    children = AgentChildCreationService(database, factory)
    created = []
    for idx, unit in enumerate((department, team, team)):
        acting = vp if idx == 0 else created[-1].child_instance
        assert acting is not None
        reservation = controls.reserve_budget(
            campaign.id,
            CampaignBudgetReserve(
                resource="agent_steps",
                owner_id=acting.id,
                idempotency_key=f"youco-reservation-{idx}",
                units=2,
                ttl_seconds=3600,
            ),
        )
        assert reservation.status == "reserved"
        body = AgentChildCreate(
            child_agent_id=TEAM_ROLE_IDS[idx + 1],
            unit_id=unit.id,
            campaign_id=campaign.id,
            budget_reservation_id=reservation.id,
            idempotency_key=f"youco-child-{idx}",
            objective=f"Produce bounded sourced editorial plans at level {idx + 1}",
            allowed_task_kinds=["research_run"],
            required_capabilities=["research"],
            max_depth=3,
            budget_resource="agent_steps",
            budget_units=2,
        )
        result = children.create(acting.id, body)
        assert result.delegation.status == "active"
        assert result.delegation.depth == idx + 1
        assert result.delegation.campaign_id == campaign.id
        assert result.child_instance.registry_version == registry.version
        assert result.child_instance.spec.organizational_level == TEAM_LEVELS[idx + 1]
        assert result.child_assignment.level == TEAM_LEVELS[idx + 1]
        assert result.child_assignment.reports_to_assignment_id == (
            vp_assignment.id if idx == 0 else created[-1].child_assignment.id
        )
        assert children.create(acting.id, body).id == result.id
        created.append(result)
    assert _counts(database) == (4, 4, 0, 3)
    return {
        "registry": registry,
        "factory": factory,
        "project": project,
        "campaign": campaign,
        "organization": organization,
        "controls": controls,
        "instances": instances,
        "tasks": tasks,
        "execution": execution,
        "children": children,
        "vp": vp,
        "created": created,
        "division": division,
        "department": department,
        "team": team,
    }


def test_real_operator_hierarchy_atomic_children_and_budget_owner(database, settings):
    company = _real_company_graph(database, settings)
    snapshot = company["organization"].snapshot()
    assert snapshot.managing_director.id == "human_managing_director"
    assert snapshot.ceo.id == "zelo_ceo"
    assert snapshot.ceo.reports_to == "human_managing_director"
    assignments = {x.agent_instance_id: x for x in snapshot.assignments}
    assert len(assignments) == 4
    assert set(x.level for x in assignments.values()) == set(TEAM_LEVELS)
    assert {x.unit_type for x in snapshot.units} == {"division", "department", "team"}
    assert sum(x.agent_instance_id == company["vp"].id for x in snapshot.assignments) == 1
    for i, creation in enumerate(company["created"]):
        assert creation.child_assignment.reports_to_assignment_id == (
            assignments[company["vp"].id].id
            if i == 0
            else company["created"][i - 1].child_assignment.id
        )
        assert creation.child_instance.spec.allowed_tools == (
            "list_workspace_files",
            "read_workspace_file",
            "repo_status",
        )
    assert company["controls"].get_budget(company["campaign"].id, "agent_steps").reserved_units == 6
    # A second active Director in this Department is refused before another
    # Instance, Assignment or Delegation is persisted.
    vp = company["vp"]
    with pytest.raises(DomainError, match="active child leader"):
        company["children"].create(
            vp.id,
            AgentChildCreate(
                child_agent_id="youtube_content",
                unit_id=company["department"].id,
                campaign_id=company["campaign"].id,
                budget_reservation_id=company["created"][0].delegation.budget_reservation_id,
                idempotency_key="youco-second-director",
                objective="Repeat hire not allowed",
                allowed_task_kinds=["research_run"],
                required_capabilities=["research"],
                max_depth=3,
                budget_resource="agent_steps",
                budget_units=2,
            ),
        )
    assert _counts(database) == (4, 4, 0, 3)


def test_real_specialist_executes_budget_bound_research_and_revocation_cancels_claim(
    database,
    settings,
):
    from app.agent_delegations import AgentDelegationService
    from app.schemas import AgentDelegationRevoke

    company = _real_company_graph(database, settings)
    specialist = company["created"][-1].child_instance
    delegation = company["created"][-1].delegation
    assert specialist.spec.allowed_task_kinds == ("research_run",)
    created, was_created = company["execution"].submit(
        specialist.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Study verified deep-sea science video evidence"},
                campaign_id=company["campaign"].id,
            ),
            delegation_id=delegation.id,
        ),
        "manual-company-sourced-task-1",
    )
    assert was_created
    assert created.status == State.PENDING
    assert created.agent_execution_spec is not None
    assert created.agent_execution_spec.delegation.depth == 3
    assert created.agent_execution_spec.spec.agent_id == "youtube_script_writer"
    assert created.agent_execution_spec.delegation.budget_reservation_id == (
        delegation.budget_reservation_id
    )
    claimed = company["tasks"].claim()
    assert claimed is not None and claimed.task_id == created.id
    assert claimed.agent_execution.spec.instructions.startswith("Create research")
    assert claimed.agent_execution.delegation.depth == 3
    company["tasks"].finish(claimed, State.FAILED, error="synthetic_evidence_only")

    queued, was_created = company["execution"].submit(
        specialist.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Another independently reviewed local science idea"},
                campaign_id=company["campaign"].id,
            ),
            delegation_id=delegation.id,
        ),
        "manual-company-sourced-task-2",
    )
    assert was_created and queued.status == State.PENDING
    denied = company["execution"].submit
    with pytest.raises(DomainError, match="delegation Campaign"):
        denied(
            specialist.id,
            AgentTaskCreate(
                task=ResearchRunInput(
                    kind="research_run",
                    payload={"query": "Wrong campaign"},
                ),
                delegation_id=delegation.id,
            ),
            "wrong-company-campaign",
        )
    with pytest.raises(DomainError, match="not authorized"):
        from app.schemas import DeveloperRunInput

        denied(
            specialist.id,
            AgentTaskCreate(
                task=DeveloperRunInput(
                    kind="developer_run",
                    payload={"instruction": "Rewrite project code"},
                ),
                delegation_id=delegation.id,
            ),
            "wrong-company-task-kind",
        )

    revocations = AgentDelegationService(database, company["factory"], company["controls"])
    revoked = revocations.revoke(
        delegation.id, AgentDelegationRevoke(reason="Operator stopped media research")
    )
    assert revoked.status == "revoked"
    assert company["tasks"].claim() is None
    result = company["tasks"].get(queued.id)
    assert result.status == State.CANCELLED
    assert result.error == "agent_execution_invalid"
    assert _counts(database) == (4, 4, 2, 3)


def test_research_models_receive_real_hierarchy_agent_instructions(settings, database):
    from tests.test_research import ResearchProvider

    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="test-youtube-company-", dir=ROOT / "workspaces") as workspace:
        root = Path(workspace)
        (root / "facts.txt").write_text(
            "Synthetic cited astronomy content used by a real Company Specialist.",
            encoding="utf-8",
        )
        provider = ResearchProvider(
            call("read_workspace_file", path="facts.txt"),
            final("The astronomy evidence is sourced. [S1]", "S1"),
        )
        with TestClient(
            create_app(
                _config(settings).model_copy(update={"workspace_root": root}), provider=provider
            ),
            headers=AUTH,
        ) as client:
            company = _real_company_graph(database, settings)
            specialist = company["created"][-1].child_instance
            delegation = company["created"][-1].delegation
            response = client.post(
                f"/agents/{specialist.id}/tasks",
                json={
                    "task": {
                        "kind": "research_run",
                        "campaign_id": company["campaign"].id,
                        "payload": {"query": "Assess cited astronomy footage."},
                    },
                    "delegation_id": delegation.id,
                },
                headers={**AUTH, "Idempotency-Key": "real-company-model-request"},
            )
            assert response.status_code == 201, response.text
            task = wait_for(client, response.json()["id"], "succeeded")
            assert task["agent_execution_spec"]["spec"]["agent_id"] == "youtube_script_writer"
            assert len(provider.requests) == 2
            assert '"agent_id": "youtube_script_writer"' in provider.requests[0].system_prompt
            assert "Create research and potential scripts" in provider.requests[0].system_prompt
            assert "read_workspace_file" in provider.requests[0].system_prompt
            assert "organization_hire" not in provider.requests[0].system_prompt
            assert "Assess cited astronomy footage" not in provider.requests[0].system_prompt
            assert _counts(database) == (4, 4, 1, 3)


def test_offline_company_cli_outputs_exact_trusted_digest_and_no_records(database, capsys):
    from scripts.preview_youtube_company_optin import main

    before = _counts(database)
    assert main() == 0
    record = json.loads(capsys.readouterr().out)
    assert record["schema_version"] == "zelo-youtube-company-optin-preview-v1"
    assert record["startup_env"] == "ZELO_YOUTUBE_COMPANY_APPROVED_REGISTRY_SHA256"
    assert record["required_exact_registry_sha256"] == youtube_company_registry_digest()
    assert len(record["source_blueprint_sha256"]) == 64
    assert [role["agent_id"] for role in record["roles"]] == list(TEAM_ROLE_IDS)
    assert [role["level"] for role in record["roles"]] == list(TEAM_LEVELS)
    assert record["creates_agent_instances"] is False
    assert record["creates_project_or_campaign"] is False
    assert record["adds_tool_or_space_grants"] is False
    assert record["publishing_enabled"] is False
    assert record["manual_operator_org_creation_required"] is True
    assert _counts(database) == before


def test_existing_standalone_research_pilot_behavior_unchanged_by_team_feature(database):
    registry, skills = admitted_youtube_company_registry(
        database,
        pilot_spec_sha256=youtube_research_spec_digest(),
        company_approved_registry_sha256=None,
    )
    assert registry.get_agent("youtube_topic_researcher") is not None
    assert registry.get_agent("youtube") is None
    assert registry.get_agent("youtube_research") is None
    assert skills.get("organization") is None
    assert _counts(database) == (0, 0, 0, 0)


def test_company_descendant_cannot_be_created_detached_from_organization(database, settings):
    from tests.test_agent_execution import direct_services

    registry, _ = _approved(database)
    _factory, instances, _tasks, _execution = direct_services(
        database, settings, agent_factory=AgentFactory(registry)
    )
    project = ProjectService(database).create(
        ProjectCreate(name="Detached attack", summary="Unassigned company role")
    )
    for role in TEAM_ROLE_IDS[1:]:
        with pytest.raises(DomainError, match="budget-bound child creation"):
            instances.create(AgentInstanceCreate(agent_id=role, project_id=project.id))
    assert _counts(database) == (0, 0, 0, 0)


def test_company_vp_cannot_dispatch_without_ceo_rooted_assignment(database, settings):
    from tests.test_agent_execution import direct_services

    registry, _ = _approved(database)
    _factory, instances, _tasks, execution = direct_services(
        database, settings, agent_factory=AgentFactory(registry)
    )
    project = ProjectService(database).create(
        ProjectCreate(name="Unassigned VP", summary="Must have a real Org Division")
    )
    vp = instances.create(AgentInstanceCreate(agent_id="youtube", project_id=project.id))
    before = _counts(database)
    body = AgentTaskCreate(
        task=ResearchRunInput(
            kind="research_run",
            payload={"query": "Create evidence-based editorial priorities"},
        )
    )
    with pytest.raises(DomainError, match="active org assignment"):
        execution.submit(vp.id, body, "youtube-company-vp-unassigned")
    assert _counts(database) == before

    org = OrganizationService(database, AgentFactory(registry))
    unit = org.create_unit(OrganizationUnitCreate(name="Media proof", unit_type="division"))
    org.create_assignment(
        OrganizationAssignmentCreate(agent_instance_id=vp.id, unit_id=unit.id, level="vp")
    )
    # Active VP Org identity alone cannot dispatch unbudgeted campaignless work.
    with pytest.raises(DomainError, match="requires active Campaign"):
        execution.submit(vp.id, body, "youtube-company-vp-no-campaign")

    projects = ProjectService(database)
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Approved VP research brief",
            spec=GoalSpec(
                objective="Outline a sourced editorial programme",
                success_criteria=["one researched idea"],
                constraints=["no publication", "no account credentials"],
            ),
        ),
    )
    campaign = CampaignService(database).create(
        goal.id, CampaignCreate(title="VP read-only research")
    )
    approved_body = AgentTaskCreate(
        task=ResearchRunInput(
            kind="research_run",
            payload={"query": "Create evidence-based editorial priorities"},
            campaign_id=campaign.id,
        )
    )
    task, created = execution.submit(vp.id, approved_body, "youtube-company-vp-assigned")
    assert created and task.status == State.PENDING
    assert task.agent_execution_spec.spec.organizational_level == "vp"
    assert task.agent_execution_spec.delegation is None


def test_company_descendant_cannot_skip_campaign_delegation_at_submit(database, settings):
    company = _real_company_graph(database, settings)
    specialist = company["created"][-1].child_instance
    before = _counts(database)
    body = AgentTaskCreate(
        task=ResearchRunInput(
            kind="research_run",
            payload={"query": "Attempt unbudgeted research"},
            campaign_id=company["campaign"].id,
        ),
        delegation_id=None,
    )
    with pytest.raises(DomainError, match="requires active delegation"):
        company["execution"].submit(specialist.id, body, "youtube-company-no-delegation")
    assert _counts(database) == before


def test_ending_company_assignment_invalidates_already_queued_task_claim(database, settings):
    from app.schemas import OrganizationAssignmentEnd

    company = _real_company_graph(database, settings)
    specialist = company["created"][-1].child_instance
    delegated = company["created"][-1].delegation
    task, created = company["execution"].submit(
        specialist.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Research before manual assignment ended"},
                campaign_id=company["campaign"].id,
            ),
            delegation_id=delegated.id,
        ),
        "youtube-company-assignment-then-revoke",
    )
    assert created and task.status == State.PENDING
    company["organization"].end_assignment(
        company["created"][-1].child_assignment.id,
        OrganizationAssignmentEnd(reason="Operator revoked Specialist assignment"),
    )
    assert company["tasks"].claim() is None
    denied = company["tasks"].get(task.id)
    assert denied.status == State.CANCELLED
    assert denied.error == "agent_execution_invalid"
    from app.models import TaskAttempt

    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(TaskAttempt)) == 0


def _company_director_ready_to_hire(database, settings):
    """Just the real VP→Director prefix, leaving Manager slot unused."""

    from app.agent_child_creation import AgentChildCreationService
    from tests.test_agent_execution import direct_services

    registry, _ = _approved(database)
    factory, instances, tasks, execution = direct_services(
        database, settings, agent_factory=AgentFactory(registry)
    )
    projects = ProjectService(database)
    project = projects.create(ProjectCreate(name="Parent gate test", summary="No Manager yet"))
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Safely hire one Manager",
            spec=GoalSpec(
                objective="Bound delegated research hiring",
                success_criteria=["one explicitly budgeted Manager"],
                constraints=["no Campaign switches", "no expired parent authority"],
            ),
        ),
    )
    campaign = CampaignService(database).create(
        goal.id, CampaignCreate(title="Parent gate Campaign")
    )
    organization = OrganizationService(database, factory)
    division = organization.create_unit(
        OrganizationUnitCreate(name="Media test", unit_type="division")
    )
    department = organization.create_unit(
        OrganizationUnitCreate(
            name="Media testing", unit_type="department", parent_unit_id=division.id
        )
    )
    team = organization.create_unit(
        OrganizationUnitCreate(
            name="Media test Research", unit_type="team", parent_unit_id=department.id
        )
    )
    vp = instances.create(AgentInstanceCreate(agent_id="youtube", project_id=project.id))
    organization.create_assignment(
        OrganizationAssignmentCreate(agent_instance_id=vp.id, unit_id=division.id, level="vp")
    )
    controls = CampaignControlService(database)
    controls.set_budget(campaign.id, "agent_steps", CampaignBudgetSet(limit_units=20))
    vp_reservation = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=vp.id,
            idempotency_key="parent-gate-vp-budget",
            units=2,
        ),
    )
    children = AgentChildCreationService(database, factory)
    director = children.create(
        vp.id,
        AgentChildCreate(
            child_agent_id="youtube_content",
            unit_id=department.id,
            campaign_id=campaign.id,
            budget_reservation_id=vp_reservation.id,
            idempotency_key="parent-gate-director",
            objective="Direct scoped evidence only",
            allowed_task_kinds=["research_run"],
            required_capabilities=["research"],
            max_depth=3,
            budget_resource="agent_steps",
            budget_units=2,
        ),
    )
    new_reservation = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=director.child_instance.id,
            idempotency_key="parent-gate-director-budget",
            units=2,
        ),
    )
    proposed_manager = AgentChildCreate(
        child_agent_id="youtube_research",
        unit_id=team.id,
        campaign_id=campaign.id,
        budget_reservation_id=new_reservation.id,
        idempotency_key="parent-gate-manager",
        objective="Manage tightly bounded evidence only",
        allowed_task_kinds=["research_run"],
        required_capabilities=["research"],
        max_depth=3,
        budget_resource="agent_steps",
        budget_units=2,
    )
    assert _counts(database) == (2, 2, 0, 1)
    return {
        "project": project,
        "goal": goal,
        "campaign": campaign,
        "vp": vp,
        "director": director,
        "manager_request": proposed_manager,
        "children": children,
        "controls": controls,
        "organization": organization,
    }


def test_revoked_parent_company_delegation_cannot_hire_new_manager(database, settings):
    from app.agent_delegations import AgentDelegationService
    from app.schemas import AgentDelegationRevoke

    setup = _company_director_ready_to_hire(database, settings)
    director = setup["director"]
    before = _counts(database)
    AgentDelegationService(database, setup["children"].factory, setup["controls"]).revoke(
        director.delegation.id,
        AgentDelegationRevoke(reason="Operator revoked Director hiring authority"),
    )
    with pytest.raises(DomainError, match="parent incoming delegation not active"):
        setup["children"].create(director.child_instance.id, setup["manager_request"])
    assert _counts(database) == before


def test_expired_parent_company_budget_cannot_hire_manager(database, settings, monkeypatch):
    from datetime import timedelta

    from app.models import utcnow

    setup = _company_director_ready_to_hire(database, settings)
    director = setup["director"]
    before = _counts(database)
    # Budget columns are protected by SQLite's immutable reservation triggers.
    # Simulate the future at the ChildCreation clock instead of corrupting DB.
    after_expiry = utcnow() + timedelta(hours=3)
    monkeypatch.setattr("app.agent_child_creation.utcnow", lambda: after_expiry)
    with pytest.raises(DomainError, match="parent Delegation budget not live"):
        setup["children"].create(director.child_instance.id, setup["manager_request"])
    assert _counts(database) == before


def test_company_director_cannot_shift_child_hiring_to_other_campaign(database, settings):
    setup = _company_director_ready_to_hire(database, settings)
    before = _counts(database)
    project = setup["project"]
    projects = ProjectService(database)
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Different Campaign",
            spec=GoalSpec(
                objective="Separate Project Campaign",
                success_criteria=["no unauthorized cross-Campaign child"],
                constraints=["preserve original parent budget scope"],
            ),
        ),
    )
    other_campaign = CampaignService(database).create(
        goal.id, CampaignCreate(title="Other Campaign")
    )
    controls = setup["controls"]
    controls.set_budget(other_campaign.id, "agent_steps", CampaignBudgetSet(limit_units=10))
    fresh = controls.reserve_budget(
        other_campaign.id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=setup["director"].child_instance.id,
            idempotency_key="other-campaign-proposed-hire",
            units=2,
        ),
    )

    request = setup["manager_request"].model_copy(
        update={
            "campaign_id": other_campaign.id,
            "budget_reservation_id": fresh.id,
            "idempotency_key": "other-campaign-manager",
        }
    )
    with pytest.raises(DomainError, match="must use parent Delegation Campaign"):
        setup["children"].create(setup["director"].child_instance.id, request)
    assert _counts(database) == before


def test_revoked_vp_director_budget_cascades_to_specialist_claim(database, settings):
    """Revoking one ancestor must deny descendant Worker dispatch, not just hiring."""

    from app.agent_delegations import AgentDelegationService
    from app.models import TaskAttempt
    from app.schemas import AgentDelegationRevoke

    company = _real_company_graph(database, settings)
    specialist = company["created"][-1].child_instance
    delegation = company["created"][-1].delegation
    task, created = company["execution"].submit(
        specialist.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Research only under the full approved VP chain"},
                campaign_id=company["campaign"].id,
            ),
            delegation_id=delegation.id,
        ),
        "youtube-company-cascade-vp-revoke",
    )
    assert created and task.status == State.PENDING
    before = _counts(database)
    revoked = AgentDelegationService(database, company["factory"], company["controls"]).revoke(
        company["created"][0].delegation.id,
        AgentDelegationRevoke(reason="Managing Director revoked VP→Director"),
    )
    assert revoked.status == "revoked"
    assert company["tasks"].claim() is None
    denied = company["tasks"].get(task.id)
    assert denied.status == State.CANCELLED
    assert denied.error == "agent_execution_invalid"
    assert _counts(database) == before
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(TaskAttempt)) == 0


def test_revoked_top_company_delegation_blocks_new_grandchild_hiring(database, settings):
    """Manager cannot hire a Specialist after VP→Director funding is revoked."""

    from app.agent_delegations import AgentDelegationService
    from app.schemas import AgentDelegationRevoke

    setup = _company_director_ready_to_hire(database, settings)
    director = setup["director"]
    manager = setup["children"].create(director.child_instance.id, setup["manager_request"])
    controls = setup["controls"]
    budget = controls.reserve_budget(
        setup["campaign"].id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=manager.child_instance.id,
            idempotency_key="manager-grandchild-after-revoked-vp",
            units=2,
        ),
    )
    request = AgentChildCreate(
        child_agent_id="youtube_script_writer",
        unit_id=manager.child_assignment.unit_id,
        campaign_id=setup["campaign"].id,
        budget_reservation_id=budget.id,
        idempotency_key="grandchild-after-vp-revocation",
        objective="Propose a bounded evidence-grounded video script",
        allowed_task_kinds=["research_run"],
        required_capabilities=["research"],
        max_depth=3,
        budget_resource="agent_steps",
        budget_units=2,
    )
    before = _counts(database)
    AgentDelegationService(database, setup["children"].factory, controls).revoke(
        director.delegation.id,
        AgentDelegationRevoke(reason="Revoke Director before grandchild hiring"),
    )
    with pytest.raises(DomainError, match="ancestor Delegation missing or ambiguous"):
        setup["children"].create(manager.child_instance.id, request)
    assert _counts(database) == before


def test_company_parent_child_roles_cannot_be_swapped_by_an_operator():
    from app.youtube_company_authority import validate_youtube_company_child_parent

    for parent, child in (
        ("research", "youtube_content"),
        ("youtube", "youtube_script_writer"),
        ("youtube_research", "youtube_content"),
        ("youtube_script_writer", "youtube"),
    ):
        if parent == "youtube_script_writer":
            # Specialist cannot have permanent children in any case.
            continue
        with pytest.raises(DomainError, match="exact trusted parent|assigned child role"):
            validate_youtube_company_child_parent(parent, child)


def test_expired_company_ancestor_budget_denies_specialist_worker_claim(
    database, settings, monkeypatch
):
    """Expiry of an ancestor proof cascades before TaskAttempt insertion."""

    from datetime import timedelta

    from app.models import TaskAttempt, utcnow

    company = _real_company_graph(database, settings)
    specialist = company["created"][-1].child_instance
    delegation = company["created"][-1].delegation
    queued, created = company["execution"].submit(
        specialist.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                campaign_id=company["campaign"].id,
                payload={"query": "Propose one source-grounded clip without publication"},
            ),
            delegation_id=delegation.id,
        ),
        "youtube-company-grandparent-budget-expiry",
    )
    assert created and queued.status == State.PENDING

    # The SQLite reservation fields are tamper protected. Advance only the
    # trusted company authority clock, not any persisted DB timestamp.
    simulated_future = utcnow() + timedelta(hours=3)
    monkeypatch.setattr("app.youtube_company_authority.utcnow", lambda: simulated_future)
    assert company["tasks"].claim() is None
    refused = company["tasks"].get(queued.id)
    assert refused.status == State.CANCELLED
    assert refused.error == "agent_execution_invalid"
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(TaskAttempt)) == 0


def _revoked_director_and_generic_research_candidate(database, settings):
    """Reproduce the forbidden generic delegation loophole without real effects."""

    from app.agent_delegations import AgentDelegationService
    from app.schemas import AgentDelegationRevoke

    company = _real_company_graph(database, settings)
    director = company["created"][0].child_instance
    delegations = AgentDelegationService(database, company["factory"], company["controls"])
    # Revoke the VP authority and outgoing Manager link. This leaves the
    # Director AgentInstance/Org assignment active but without hiring rights.
    delegations.revoke(
        company["created"][0].delegation.id,
        AgentDelegationRevoke(reason="Managing Director revoked Director"),
    )
    delegations.revoke(
        company["created"][1].delegation.id,
        AgentDelegationRevoke(reason="Remove outgoing Manager delegation"),
    )
    child = company["instances"].create(
        AgentInstanceCreate(agent_id="research", project_id=company["project"].id)
    )
    receipt = company["controls"].reserve_budget(
        company["campaign"].id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=director.id,
            idempotency_key="forbidden-director-generic-budget",
            units=2,
            ttl_seconds=3600,
        ),
    )
    return company, delegations, director, child, receipt


def test_revoked_company_director_cannot_delegate_to_generic_research_worker(database, settings):
    """Real authenticated DelegationService must refuse before persisting."""

    from app.models import AgentDelegation
    from app.schemas import AgentDelegationCreate

    company, delegations, director, child, receipt = (
        _revoked_director_and_generic_research_candidate(database, settings)
    )
    with database.read() as session:
        original = session.scalar(select(func.count()).select_from(AgentDelegation))
    body = AgentDelegationCreate(
        child_instance_id=child.id,
        campaign_id=company["campaign"].id,
        budget_reservation_id=receipt.id,
        idempotency_key="forbidden-company-director-generic-worker",
        objective="Wrongly pass revoked authority to generic Research",
        allowed_task_kinds=["research_run"],
        required_capabilities=["research"],
        max_depth=3,
        budget_resource="agent_steps",
        budget_units=2,
    )
    with pytest.raises(DomainError, match="requires budget-bound child creation"):
        delegations.create(director.id, body)
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentDelegation)) == original
    assert _counts(database)[2] == 0


def test_historical_generic_delegate_edge_from_company_executive_cannot_submit_task(
    database, settings
):
    """Even a pre-upgrade/forged stored Delegation cannot bypass TaskService."""

    from app.models import AgentDelegation, utcnow
    from app.policy import fingerprint

    company, _delegations, director, child, receipt = (
        _revoked_director_and_generic_research_candidate(database, settings)
    )
    legacy_fields = {
        "child_instance_id": child.id,
        "campaign_id": company["campaign"].id,
        "budget_reservation_id": receipt.id,
        "objective": "Stale generic delegated worker evidence",
        "allowed_task_kinds": ["research_run"],
        "required_capabilities": ["research"],
        "max_depth": 3,
        "budget_resource": "agent_steps",
        "budget_units": 2,
    }
    with database.transaction() as session:
        stored = AgentDelegation(
            parent_instance_id=director.id,
            child_instance_id=child.id,
            campaign_id=company["campaign"].id,
            budget_reservation_id=receipt.id,
            idempotency_key="pre-upgrade-bad-company-link",
            request_hash=fingerprint(legacy_fields),
            objective=legacy_fields["objective"],
            allowed_task_kinds=["research_run"],
            required_capabilities=["research"],
            depth=1,
            max_depth=3,
            budget_resource="agent_steps",
            budget_units=2,
            status="active",
            created_at=utcnow(),
        )
        session.add(stored)
        session.flush()
        edge_id = stored.id
    before = _counts(database)
    with pytest.raises(DomainError, match="assigned child role"):
        company["execution"].submit(
            child.id,
            AgentTaskCreate(
                task=ResearchRunInput(
                    kind="research_run",
                    payload={"query": "Attempt research using stale company delegation"},
                    campaign_id=company["campaign"].id,
                ),
                delegation_id=edge_id,
            ),
            "forbidden-stored-company-generic-child-task",
        )
    assert _counts(database) == before


def test_company_child_creation_refuses_narrow_or_extended_depth_grants(database, settings):
    """Operator cannot alter the approved three-edge company hierarchy."""

    setup = _company_director_ready_to_hire(database, settings)
    before = _counts(database)
    for forbidden_depth in (1, 2, 4):
        changed = setup["manager_request"].model_copy(
            update={
                "max_depth": forbidden_depth,
                "idempotency_key": f"company-forbidden-depth-{forbidden_depth}",
            }
        )
        with pytest.raises(DomainError, match="exact depth three"):
            setup["children"].create(setup["director"].child_instance.id, changed)
        assert _counts(database) == before


def test_replaced_ancestor_delegation_with_narrow_depth_cannot_reactivate_specialist(
    database, settings
):
    """A legacy row with a smaller top-level grant must not restore leaf execution."""

    from app.agent_delegations import AgentDelegationService
    from app.models import AgentDelegation, TaskAttempt, utcnow
    from app.policy import fingerprint
    from app.schemas import AgentDelegationRevoke

    company = _real_company_graph(database, settings)
    specialist = company["created"][-1].child_instance
    delegation = company["created"][-1].delegation
    task, created = company["execution"].submit(
        specialist.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                campaign_id=company["campaign"].id,
                payload={"query": "Review sourced script without changing team scope"},
            ),
            delegation_id=delegation.id,
        ),
        "company-narrowed-ancestor-before-claim",
    )
    assert created and task.status == State.PENDING
    AgentDelegationService(database, company["factory"], company["controls"]).revoke(
        company["created"][0].delegation.id,
        AgentDelegationRevoke(reason="Operator revoked original VP→Director edge"),
    )
    vp = company["vp"]
    director = company["created"][0].child_instance
    reservation = company["controls"].reserve_budget(
        company["campaign"].id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=vp.id,
            idempotency_key="legacy-narrow-depth-budget",
            units=2,
        ),
    )
    legacy = {
        "child_instance_id": director.id,
        "campaign_id": company["campaign"].id,
        "budget_reservation_id": reservation.id,
        "objective": "Previously permitted, now too narrow to run Specialist",
        "allowed_task_kinds": ["research_run"],
        "required_capabilities": ["research"],
        "max_depth": 1,
        "budget_resource": "agent_steps",
        "budget_units": 2,
    }
    # Emulate a pre-upgrade persisted edge that normal Service now refuses.
    # No authorization boundary is mocked away during Task claim itself.
    with database.transaction() as session:
        session.add(
            AgentDelegation(
                parent_instance_id=vp.id,
                child_instance_id=director.id,
                campaign_id=company["campaign"].id,
                budget_reservation_id=reservation.id,
                idempotency_key="legacy-replacement-top-edge",
                request_hash=fingerprint(legacy),
                objective=legacy["objective"],
                allowed_task_kinds=["research_run"],
                required_capabilities=["research"],
                depth=1,
                max_depth=1,
                budget_resource="agent_steps",
                budget_units=2,
                status="active",
                created_at=utcnow(),
            )
        )
    assert company["tasks"].claim() is None
    rejected = company["tasks"].get(task.id)
    assert rejected.status == State.CANCELLED
    assert rejected.error == "agent_execution_invalid"
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(TaskAttempt)) == 0
