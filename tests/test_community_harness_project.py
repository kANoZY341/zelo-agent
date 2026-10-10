"""Harness opt-in Project readiness never registers Agents or adds authority."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_factory import AgentFactory, AgentRegistry
from app.community_harness import propose_team
from app.community_harness_project import HarnessProjectReviewPlan
from app.models import (
    AgentInstance,
    AuditEvent,
    OrganizationAssignment,
    Project,
    Task,
    utcnow,
)
from app.projects import ProjectService
from app.schemas import ProjectCreate
from tests.conftest import AUTH

BASE = "/integration-candidates/community/teams"


def _request(template, project_id, *, budget=None, fingerprint=None):
    blueprint = propose_team(template)
    body = {
        "project_id": project_id,
        "expected_blueprint_sha256": fingerprint or blueprint.fingerprint,
        "proposed_budget": {
            "planned_instance_slots": 4,
            "proposed_max_concurrent_workers": 1,
            "proposed_model_tokens_per_task": 4096,
            "proposed_monthly_cost_cents": 5000,
        },
    }
    if budget:
        body["proposed_budget"].update(budget)
    return body


def _preview(client, template, body):
    return client.post(
        f"{BASE}/{template}/project-readiness",
        headers=AUTH,
        json=body,
    )


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in (Task, AuditEvent, AgentInstance, OrganizationAssignment)
        )


def test_operator_design_review_binds_exact_project_and_budget_without_writes(client, database):
    project = ProjectService(database).create(
        ProjectCreate(name="Content Department", summary="Human-reviewed publishing plans")
    )
    before = _counts(database)
    body = _request("youtube_content", project.id)
    response = _preview(client, "youtube_content", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["template"] == "youtube_content"
    assert result["project_id"] == project.id
    assert result["project_status"] == "active"
    assert result["registry_version"] == client.app.state.agent_factory.registry.version
    assert result["blueprint_sha256"] == body["expected_blueprint_sha256"]
    assert result["reason"] == "eligible_for_design_review_only"
    assert result["eligible_for_separate_design_review"] is True
    assert result["proposed_budget"]["planned_instance_slots"] == 4
    assert result["proposed_budget"]["proposed_model_tokens_per_task"] == 4096
    assert result["proposed_budget"]["proposed_monthly_cost_cents"] == 5000
    assert [x["organizational_level"] for x in result["roles"]] == [
        "vp",
        "director",
        "manager",
        "specialist",
    ]
    assert [x["reports_to"] for x in result["roles"]] == [
        None,
        "youtube",
        "youtube_content",
        "youtube_research",
    ]
    assert all(x["eligible_for_separate_design_review"] is True for x in result["roles"])
    assert all(x["existing_instance_count"] == 0 for x in result["roles"])
    assert all(x["already_registered"] is False for x in result["roles"])
    assert result["automatic_instance_creation"] is False
    assert result["review_plan_is_execution_authority"] is False
    assert result["resource_budget_enforced_at_runtime"] is False
    assert result["managing_director_approval_required"] is True
    assert result["active_agents_created"] is False
    assert result["tasks_created"] is False
    assert result["skill_activation_authority"] is False
    assert result["tool_grants"] == result["worker_grants"] == result["space_grants"] == []
    assert len(result["plan_sha256"]) == 64
    assert (
        HarnessProjectReviewPlan.model_validate_json(json.dumps(result), strict=True).model_dump(
            mode="json"
        )
        == result
    )
    assert _preview(client, "youtube_content", body).json() == result
    assert _counts(database) == before


@pytest.mark.parametrize(
    "template",
    ["engineering_quality", "business_research", "youtube_content", "trading_research"],
)
def test_all_four_inert_templates_remain_eligible_for_design_only(client, database, template):
    project = ProjectService(database).create(
        ProjectCreate(name="Draft team", summary="Budget preview")
    )
    result = _preview(client, template, _request(template, project.id))
    assert result.status_code == 200, result.text
    assert result.json()["eligible_for_separate_design_review"] is True
    assert len(result.json()["roles"]) == 4
    assert all(
        role["permissions_automatically_granted"] is False for role in result.json()["roles"]
    )


def test_insufficient_instance_ceiling_is_distinct_denial_not_partial_team(client, database):
    project = ProjectService(database).create(
        ProjectCreate(name="Small budget", summary="Only 2 agent slots allowed")
    )
    result = _preview(
        client,
        "engineering_quality",
        _request("engineering_quality", project.id, budget={"planned_instance_slots": 2}),
    )
    assert result.status_code == 200, result.text
    record = result.json()
    assert record["reason"] == "draft_instance_budget_insufficient"
    assert record["eligible_for_separate_design_review"] is False
    assert all(role["eligible_for_separate_design_review"] is False for role in record["roles"])
    assert record["automatic_instance_creation"] is False


def test_inactive_project_snapshot_denies_without_instance_writes(client, database):
    project = ProjectService(database).create(
        ProjectCreate(name="Paused project", summary="Review must not instantiate agents")
    )
    with database.transaction() as session:
        row = session.get(Project, project.id)
        row.status = "paused"
        row.updated_at = utcnow()
    before = _counts(database)
    result = _preview(client, "business_research", _request("business_research", project.id))
    assert result.status_code == 200, result.text
    assert result.json()["project_status"] == "paused"
    assert result.json()["reason"] == "project_inactive"
    assert result.json()["eligible_for_separate_design_review"] is False
    assert _counts(database) == before


def test_global_identity_collision_with_existing_nonretired_agent_refuses(client, database):
    project = ProjectService(database).create(ProjectCreate(name="New", summary="A"))
    other_project = ProjectService(database).create(
        ProjectCreate(name="Existing", summary="Independent project")
    )
    blueprint = propose_team("youtube_content")
    role = blueprint.roles[3]
    with database.transaction() as session:
        session.add(
            AgentInstance(
                agent_id=role.agent_id,
                agent_version=role.version,
                registry_version="a" * 64,
                project_id=other_project.id,
                spec=role.model_dump(mode="json"),
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    before = _counts(database)
    result = _preview(client, "youtube_content", _request("youtube_content", project.id))
    assert result.status_code == 200, result.text
    record = result.json()
    assert record["reason"] == "agent_instance_identity_reserved"
    assert record["eligible_for_separate_design_review"] is False
    assert record["roles"][3]["existing_instance_count"] == 1
    assert record["roles"][3]["eligible_for_separate_design_review"] is False
    assert _counts(database) == before


def test_registry_identity_collision_is_reported_without_registration(
    client, database, monkeypatch
):
    project = ProjectService(database).create(
        ProjectCreate(name="Collision", summary="One draft id already registered")
    )
    role = propose_team("engineering_quality").roles[0]
    registry = AgentRegistry([role])
    monkeypatch.setattr(client.app.state, "agent_factory", AgentFactory(registry))
    response = _preview(client, "engineering_quality", _request("engineering_quality", project.id))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reason"] == "agent_name_already_registered"
    assert result["eligible_for_separate_design_review"] is False
    assert result["roles"][0]["already_registered"] is True
    assert result["roles"][0]["eligible_for_separate_design_review"] is False


def test_missing_project_stale_blueprint_and_template_refuse(client, database):
    project = ProjectService(database).create(
        ProjectCreate(name="Bounded", summary="Exact project and template")
    )
    stale = _preview(
        client,
        "youtube_content",
        _request("youtube_content", project.id, fingerprint="0" * 64),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "harness_project_blueprint_stale"
    missing = _preview(
        client,
        "youtube_content",
        _request("youtube_content", "00000000-0000-4000-8000-000000000099"),
    )
    assert missing.status_code == 404
    invalid = _preview(client, "unreviewed_team", _request("youtube_content", project.id))
    assert invalid.status_code == 422
    assert client.get(f"{BASE}/youtube_content/project-readiness", headers=AUTH).status_code == 405


@pytest.mark.parametrize(
    "invalid",
    [
        {"planned_instance_slots": 5},
        {"planned_instance_slots": 0},
        {"proposed_max_concurrent_workers": 5},
        {"proposed_model_tokens_per_task": 65537},
        {"proposed_monthly_cost_cents": -1},
        {"proposed_external_account_access": True},
        {"proposed_shell_access": True},
        {"proposed_automatic_publication": True},
        {"planned_instance_slots": True},
        {"extra_permission": "shell"},
    ],
)
def test_budget_never_accepts_widened_grants_or_invalid_values(client, database, invalid):
    project = ProjectService(database).create(ProjectCreate(name="Budget", summary="A"))
    result = _preview(
        client, "trading_research", _request("trading_research", project.id, budget=invalid)
    )
    assert result.status_code == 422


def test_operator_auth_and_no_implicit_approval(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Auth", summary="A"))
    body = _request("trading_research", project.id)
    response = client.post(
        f"{BASE}/trading_research/project-readiness",
        json=body,
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
    assert _counts(database) == (0, 0, 0, 0)


def test_tampered_response_digest_permissions_or_structure_refuses(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Integrity", summary="A"))
    plan = _preview(client, "youtube_content", _request("youtube_content", project.id)).json()
    altered = json.loads(json.dumps(plan))
    altered["proposed_budget"]["proposed_monthly_cost_cents"] = 99999
    with pytest.raises(ValidationError, match="harness_project_review_plan_digest_invalid"):
        HarnessProjectReviewPlan.model_validate_json(json.dumps(altered), strict=True)
    altered = json.loads(json.dumps(plan))
    altered["automatic_instance_creation"] = True
    with pytest.raises(ValidationError):
        HarnessProjectReviewPlan.model_validate_json(json.dumps(altered), strict=True)
    altered = json.loads(json.dumps(plan))
    altered["roles"][3]["reports_to"] = "unrelated_manager"
    with pytest.raises(ValidationError):
        HarnessProjectReviewPlan.model_validate_json(json.dumps(altered), strict=True)


def test_retired_role_instance_does_not_reserve_draft_name(client, database):
    project = ProjectService(database).create(
        ProjectCreate(name="Retired predecessor", summary="Historical evidence only")
    )
    template = "trading_research"
    role = propose_team(template).roles[3]
    with database.transaction() as session:
        session.add(
            AgentInstance(
                agent_id=role.agent_id,
                agent_version=role.version,
                registry_version="e" * 64,
                project_id=project.id,
                spec=role.model_dump(mode="json"),
                status="retired",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    result = _preview(client, template, _request(template, project.id))
    assert result.status_code == 200, result.text
    assert result.json()["reason"] == "eligible_for_design_review_only"
    assert all(item["existing_instance_count"] == 0 for item in result.json()["roles"])


def test_project_state_change_yields_distinct_plan_fingerprint(client, database):
    project = ProjectService(database).create(
        ProjectCreate(name="Changed project", summary="Exact source context")
    )
    body = _request("engineering_quality", project.id)
    first = _preview(client, "engineering_quality", body).json()
    from datetime import timedelta

    with database.transaction() as session:
        row = session.get(Project, project.id)
        row.updated_at = row.updated_at + timedelta(seconds=10)
    changed = _preview(client, "engineering_quality", body).json()
    assert first["project_updated_at"] != changed["project_updated_at"]
    assert first["plan_sha256"] != changed["plan_sha256"]
    assert changed["reason"] == "eligible_for_design_review_only"


def test_tampered_harness_source_refuses_without_creating_agent(client, database):
    from unittest.mock import patch

    from app.community_sources import CommunitySourceRefusal

    project = ProjectService(database).create(ProjectCreate(name="Pinned", summary="A"))
    expected = _request("youtube_content", project.id)
    before = _counts(database)
    with patch(
        "app.community_harness.verify_community_sources",
        side_effect=CommunitySourceRefusal("private_pinned_blob_error"),
    ):
        response = _preview(client, "youtube_content", expected)
    assert response.status_code == 503
    assert response.json()["detail"] == "harness_project_source_unavailable"
    assert "private_pinned_blob_error" not in response.text
    assert _counts(database) == before
