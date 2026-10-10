"""Operator design decisions cannot grant agent authority or bypass stale checks."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.community_harness import propose_team
from app.community_harness_design_review import TeamDesignReviewReceipt
from app.models import (
    AgentInstance,
    AuditEvent,
    HarnessTeamDesignReview,
    OrganizationAssignment,
    Project,
    Task,
    utcnow,
)
from app.projects import ProjectService
from app.schemas import ProjectCreate
from tests.conftest import AUTH

BASE = "/integration-candidates/community"


def _proposal(project_id, template):
    return {
        "project_id": project_id,
        "expected_blueprint_sha256": propose_team(template).fingerprint,
        "proposed_budget": {
            "planned_instance_slots": 4,
            "proposed_max_concurrent_workers": 1,
            "proposed_model_tokens_per_task": 4096,
            "proposed_monthly_cost_cents": 5000,
        },
    }


def _body(
    client,
    project_id,
    template,
    *,
    key="design_review_12345",
    decision="accepted_for_design_review",
):
    proposal = _proposal(project_id, template)
    plan = client.post(
        f"{BASE}/teams/{template}/project-readiness",
        headers=AUTH,
        json=proposal,
    )
    assert plan.status_code == 200, plan.text
    return {
        "proposal": proposal,
        "expected_review_plan_sha256": plan.json()["plan_sha256"],
        "idempotency_key": key,
        "decision": decision,
    }


def _post(client, template, body, *, headers=AUTH):
    return client.post(
        f"{BASE}/teams/{template}/design-reviews",
        headers=headers,
        json=body,
    )


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in (
                Task,
                AuditEvent,
                AgentInstance,
                OrganizationAssignment,
                HarnessTeamDesignReview,
            )
        )


@pytest.mark.parametrize(
    "template",
    ["engineering_quality", "business_research", "youtube_content", "trading_research"],
)
def test_design_judgment_is_durable_historical_and_inert(client, database, template):
    project = ProjectService(database).create(
        ProjectCreate(name="Private source name", summary="Must not be executed")
    )
    body = _body(client, project.id, template, key=f"review_{template}_2026")
    before = _counts(database)
    response = _post(client, template, body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["project_id"] == project.id
    assert receipt["template"] == template
    assert receipt["planning_reason"] == "eligible_for_design_review_only"
    assert receipt["decision"] == "accepted_for_design_review"
    assert receipt["operator_subject"] == "ahmad"
    assert receipt["review_plan_sha256"] == body["expected_review_plan_sha256"]
    assert receipt["record_is_historical"] is True
    assert receipt["current_plan_eligibility_claimed"] is False
    assert receipt["real_team_activated"] is False
    assert receipt["design_review_is_execution_authority"] is False
    assert receipt["worker_or_tool_or_space_grants"] == []
    assert TeamDesignReviewReceipt.model_validate_json(json.dumps(receipt), strict=True)
    get = client.get(f"{BASE}/team-design-reviews/{receipt['id']}", headers=AUTH)
    assert get.status_code == 200 and get.json() == receipt
    assert _post(client, template, body).json() == receipt
    after = _counts(database)
    assert after == (*before[:4], before[4] + 1)


def test_denied_project_may_be_rejected_but_never_accepted(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Suspended", summary="Stopped"))
    with database.transaction() as session:
        row = session.get(Project, project.id)
        row.status = "paused"
        row.updated_at = utcnow()
    body = _body(client, project.id, "youtube_content")
    assert _post(client, "youtube_content", body).status_code == 409
    assert _counts(database) == (0, 0, 0, 0, 0)
    body["decision"] = "rejected_for_design_review"
    result = _post(client, "youtube_content", body)
    assert result.status_code == 201, result.text
    assert result.json()["planning_reason"] == "project_inactive"
    assert result.json()["current_plan_eligibility_claimed"] is False
    assert _counts(database) == (0, 0, 0, 0, 1)


def test_stale_plan_refuses_before_idempotent_replay_and_historical_get_still_safe(
    client, database
):
    project = ProjectService(database).create(ProjectCreate(name="Revision", summary="Test"))
    body = _body(client, project.id, "engineering_quality")
    saved = _post(client, "engineering_quality", body)
    assert saved.status_code == 201
    with database.transaction() as session:
        row = session.get(Project, project.id)
        row.updated_at = utcnow()
        row.status = "paused"
    rejected = _post(client, "engineering_quality", body)
    assert rejected.status_code == 409
    assert _counts(database) == (0, 0, 0, 0, 1)
    prior = client.get(f"{BASE}/team-design-reviews/{saved.json()['id']}", headers=AUTH)
    assert prior.status_code == 200
    assert prior.json() == saved.json()
    assert prior.json()["current_plan_eligibility_claimed"] is False


def test_wrong_plan_digest_blueprint_and_replay_collision_refuse_without_writes(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Fresh", summary="A"))
    body = _body(client, project.id, "business_research")
    wrong = {**body, "expected_review_plan_sha256": "1" * 64}
    assert _post(client, "business_research", wrong).status_code == 409
    stale = json.loads(json.dumps(body))
    stale["proposal"]["expected_blueprint_sha256"] = "0" * 64
    assert _post(client, "business_research", stale).status_code == 409
    created = _post(client, "business_research", body)
    assert created.status_code == 201
    altered = {**body, "decision": "rejected_for_design_review"}
    conflict = _post(client, "business_research", altered)
    assert conflict.status_code == 409
    assert _post(client, "youtube_content", body).status_code == 409
    assert _counts(database) == (0, 0, 0, 0, 1)


def test_new_cross_project_role_reservation_blocks_prior_review_plan(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Proposed", summary="A"))
    other = ProjectService(database).create(ProjectCreate(name="Existing", summary="B"))
    template = "youtube_content"
    body = _body(client, project.id, template, key="cross_project_role_case")
    role = propose_team(template).roles[3]
    with database.transaction() as session:
        session.add(
            AgentInstance(
                agent_id=role.agent_id,
                agent_version=role.version,
                registry_version="f" * 64,
                project_id=other.id,
                spec=role.model_dump(mode="json"),
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    assert _post(client, template, body).status_code == 409
    assert _counts(database) == (0, 0, 1, 0, 0)


def test_auth_and_strict_input_disallow_widened_permissions(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Auth", summary="Test"))
    body = _body(client, project.id, "trading_research")
    assert (
        _post(
            client,
            "trading_research",
            body,
            headers={"Authorization": "Bearer not-the-operator"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"{BASE}/team-design-reviews/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": "Bearer not-the-operator"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"{BASE}/team-design-reviews/00000000-0000-0000-0000-000000000000",
            headers=AUTH,
        ).status_code
        == 404
    )
    invalid = json.loads(json.dumps(body))
    invalid["proposal"]["proposed_budget"]["proposed_shell_access"] = True
    assert _post(client, "trading_research", invalid).status_code == 422
    invalid = {**body, "decision": "activate"}
    assert _post(client, "trading_research", invalid).status_code == 422
    invalid = {**body, "idempotency_key": "short"}
    assert _post(client, "trading_research", invalid).status_code == 422
    invalid = {**body, "operator_subject": "someone_else"}
    assert _post(client, "trading_research", invalid).status_code == 422
    assert _counts(database) == (0, 0, 0, 0, 0)


def test_receipt_mutations_refuse_strict_rehydration(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Digests", summary="Test"))
    receipt = _post(client, "youtube_content", _body(client, project.id, "youtube_content")).json()
    altered = json.loads(json.dumps(receipt))
    altered["review_plan_sha256"] = "b" * 64
    with pytest.raises(ValidationError, match="harness_team_design_receipt_digest_invalid"):
        TeamDesignReviewReceipt.model_validate_json(json.dumps(altered), strict=True)
    altered = json.loads(json.dumps(receipt))
    altered["real_team_activated"] = True
    with pytest.raises(ValidationError):
        TeamDesignReviewReceipt.model_validate_json(json.dumps(altered), strict=True)
    altered = json.loads(json.dumps(receipt))
    altered["decision"] = "approved_for_activation"
    with pytest.raises(ValidationError):
        TeamDesignReviewReceipt.model_validate_json(json.dumps(altered), strict=True)


def test_immutable_database_triggers_stop_sql_modification_and_replace(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Immutable", summary="Test"))
    body = _body(client, project.id, "youtube_content")
    saved = _post(client, "youtube_content", body)
    assert saved.status_code == 201, saved.text
    row_id = saved.json()["id"]
    with database.read() as session:
        prior = session.get(HarnessTeamDesignReview, row_id)
        assert prior is not None
        original_digest = prior.receipt_sha256
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE harness_team_design_reviews "
                    "SET decision='rejected_for_design_review' WHERE id=:id"
                ),
                {"id": row_id},
            )
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM harness_team_design_reviews WHERE id=:id"),
                {"id": row_id},
            )
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO harness_team_design_reviews "
                    "(id,project_id,project_status,template,blueprint_sha256,registry_version,"
                    "review_plan_sha256,planning_reason,decision,idempotency_key,"
                    "request_sha256,receipt_sha256,operator_subject,created_at) "
                    "SELECT :id,project_id,project_status,template,blueprint_sha256,"
                    "registry_version,review_plan_sha256,planning_reason,"
                    "'rejected_for_design_review',idempotency_key,request_sha256,"
                    "receipt_sha256,operator_subject,created_at "
                    "FROM harness_team_design_reviews WHERE id=:existing_id"
                ),
                {"id": "00000000-0000-4000-8000-000000000098", "existing_id": row_id},
            )
    with database.read() as session:
        all_records = session.scalars(select(HarnessTeamDesignReview)).all()
        assert len(all_records) == 1
        assert all_records[0].id == row_id
        assert all_records[0].receipt_sha256 == original_digest


def test_db_corruption_refuses_read_without_claiming_authority(client, database):
    project = ProjectService(database).create(ProjectCreate(name="Tamper", summary="Test"))
    saved = _post(client, "youtube_content", _body(client, project.id, "youtube_content"))
    assert saved.status_code == 201
    # ORM model construction or direct storage corruption must never produce
    # a second trusted receipt even if the row has been altered offline.
    with database.read() as session:
        row = session.get(HarnessTeamDesignReview, saved.json()["id"])
        invalid = HarnessTeamDesignReview(
            id=row.id,
            project_id=row.project_id,
            project_status=row.project_status,
            template=row.template,
            blueprint_sha256="0" * 64,
            registry_version=row.registry_version,
            review_plan_sha256=row.review_plan_sha256,
            planning_reason=row.planning_reason,
            decision=row.decision,
            idempotency_key=row.idempotency_key,
            request_sha256=row.request_sha256,
            receipt_sha256=row.receipt_sha256,
            operator_subject=row.operator_subject,
            created_at=row.created_at,
        )
    from app.community_harness_design_review import _receipt
    from app.policy import DomainError

    with pytest.raises(DomainError, match="integrity"):
        _receipt(invalid)
