"""Project-bound Archify diagrams are digest-bound design, never active teams."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.community_archify import CommunityArchifyRefusal, _validate_exact_archify_schema
from app.community_archify_harness_project import (
    HarnessProjectArchitectureView,
    harness_project_architecture,
)
from app.community_harness import propose_team
from app.community_harness_project import HarnessProjectReviewService, TeamProjectPlanningRequest
from app.community_sources import CommunitySourceRefusal
from app.models import AgentInstance, AuditEvent, OrganizationAssignment, Project, Task, utcnow
from app.projects import ProjectService
from app.schemas import ProjectCreate
from tests.conftest import AUTH

BASE = "/integration-candidates/community/teams"


def _body(project_id, template="youtube_content", *, slots=4):
    return {
        "project_id": project_id,
        "expected_blueprint_sha256": propose_team(template).fingerprint,
        "proposed_budget": {
            "planned_instance_slots": slots,
            "proposed_max_concurrent_workers": 1,
            "proposed_model_tokens_per_task": 4096,
            "proposed_monthly_cost_cents": 5000,
        },
    }


def _post(client, template, body, *, headers=AUTH):
    return client.post(f"{BASE}/{template}/project-architecture", headers=headers, json=body)


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in (Task, AuditEvent, AgentInstance, OrganizationAssignment)
        )


@pytest.mark.parametrize(
    "template",
    ["engineering_quality", "business_research", "youtube_content", "trading_research"],
)
def test_project_diagram_is_deterministic_validated_and_read_only(client, database, template):
    project = ProjectService(database).create(
        ProjectCreate(name="Private project secret never appear", summary="Confidential plans")
    )
    before = _counts(database)
    body = _body(project.id, template)
    response = _post(client, template, body)
    assert response.status_code == 200, response.text
    view = response.json()
    assert _post(client, template, body).json() == view
    assert _counts(database) == before
    assert view["project_id"] == project.id
    assert view["template"] == template
    assert view["role_count"] == 4
    assert view["review_reason"] == "eligible_for_design_review_only"
    assert view["eligible_for_design_review_only"] is True
    assert view["blueprint_sha256"] == body["expected_blueprint_sha256"]
    assert view["registry_version"] == client.app.state.agent_factory.registry.version
    assert view["managing_director_approval_required"] is True
    assert view["agent_instances_created"] is False
    assert view["real_team_activated"] is False
    assert view["renderer_executed"] is False
    assert view["plan_is_execution_authority"] is False
    assert view["tool_grants"] == view["worker_grants"] == view["space_grants"] == []
    assert (
        HarnessProjectArchitectureView.model_validate_json(
            json.dumps(view), strict=True
        ).model_dump(mode="json")
        == view
    )
    diagram = json.loads(view["archify_json"])
    _validate_exact_archify_schema(diagram)
    assert len(diagram["components"]) == 5
    assert len(diagram["connections"]) == 4
    nodes = {x["id"] for x in diagram["components"]}
    assert nodes == {"projectDesign", "draftVP", "draftDirector", "draftManager", "draftSpecialist"}
    assert diagram["connections"] == [
        {"from": "projectDesign", "to": "draftVP"},
        {"from": "draftVP", "to": "draftDirector"},
        {"from": "draftDirector", "to": "draftManager"},
        {"from": "draftManager", "to": "draftSpecialist"},
    ]
    labels = " ".join(node["label"] for node in diagram["components"])
    assert "Draft VP" in labels and "Draft SPECIALIST" in labels
    assert "Private project secret" not in labels
    assert "design only" in diagram["meta"]["title"]
    assert "flowchart TD" in view["mermaid_text"]


def test_paused_project_and_insufficient_budget_render_denied_design_not_active(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Paused", summary="Design"))
    with database.transaction() as session:
        row = session.get(Project, project.id)
        row.status = "paused"
        row.updated_at = utcnow()
    before = _counts(database)
    response = _post(client, "business_research", _body(project.id, "business_research"))
    assert response.status_code == 200
    denied = response.json()
    assert denied["review_reason"] == "project_inactive"
    assert denied["eligible_for_design_review_only"] is False
    assert denied["agent_instances_created"] is False
    assert _counts(database) == before

    project2 = ProjectService(database).create(ProjectCreate(name="Small", summary="Budget"))
    response2 = _post(client, "trading_research", _body(project2.id, "trading_research", slots=2))
    assert response2.status_code == 200
    assert response2.json()["review_reason"] == "draft_instance_budget_insufficient"
    assert response2.json()["eligible_for_design_review_only"] is False


def test_auth_input_validation_stale_blueprint_and_missing_project(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Auth", summary="Preview"))
    body = _body(project.id)
    assert (
        _post(client, "youtube_content", body, headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )
    assert (
        client.get(f"{BASE}/youtube_content/project-architecture", headers=AUTH).status_code == 405
    )
    assert _post(client, "unknown", body).status_code == 422
    bad_budget = json.loads(json.dumps(body))
    bad_budget["proposed_budget"]["proposed_shell_access"] = True
    assert _post(client, "youtube_content", bad_budget).status_code == 422
    stale = {**body, "expected_blueprint_sha256": "0" * 64}
    assert _post(client, "youtube_content", stale).status_code == 409
    missing = _body("00000000-0000-4000-8000-000000000099")
    assert _post(client, "youtube_content", missing).status_code == 404


def test_tampered_plan_and_diagram_are_never_trusted(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Integrity", summary="Test"))
    body = _body(project.id)
    service = HarnessProjectReviewService(database, client.app.state.agent_factory)
    plan = service.preview("youtube_content", TeamProjectPlanningRequest.model_validate(body))
    changed = plan.model_copy(update={"project_id": "00000000-0000-4000-8000-000000000099"})
    with pytest.raises(CommunityArchifyRefusal, match="harness_architecture_plan_invalid"):
        harness_project_architecture(changed)
    view = _post(client, "youtube_content", body).json()
    view["real_team_activated"] = True
    with pytest.raises(ValidationError):
        HarnessProjectArchitectureView.model_validate_json(json.dumps(view), strict=True)
    view = _post(client, "youtube_content", body).json()
    view["archify_json"] += " "
    with pytest.raises(ValidationError, match="harness_project_architecture_digest_invalid"):
        HarnessProjectArchitectureView.model_validate_json(json.dumps(view), strict=True)


def test_archify_reference_refusal_is_generic(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Pinned", summary="Test"))
    with patch(
        "app.community_archify.read_pinned_reference",
        side_effect=CommunitySourceRefusal("community_source_bytes_changed"),
    ):
        response = _post(client, "youtube_content", _body(project.id))
    assert response.status_code == 503
    assert response.json() == {"detail": "harness_project_architecture_unavailable"}
    assert "community_source_bytes_changed" not in response.text
