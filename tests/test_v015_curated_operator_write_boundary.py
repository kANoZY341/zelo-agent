"""R5 Core operator evidence writes must never confer execution authority."""

from sqlalchemy import func, select

from app.community_harness import propose_team
from app.models import (
    AgentComputerPermission,
    AgentInstance,
    AgentSpaceActivationRecord,
    AgentSpaceIntentRecord,
    AgentSpaceResourceRecord,
    Approval,
    CommunityContextArchiveRecord,
    HarnessTeamDesignReview,
    OrganizationAssignment,
    Project,
    ReaOperatorTargetRevocationRecord,
    ReaOperatorTargetSelectionRecord,
    SkillLearningPromotionLinkRecord,
    SkillLearningProposalRecord,
    SkillPublicationClaimRecord,
    Task,
    TaskAttempt,
    utcnow,
)
from app.projects import ProjectService
from app.schemas import ProjectCreate
from tests.conftest import AUTH
from tests.test_rea_operator_targets import SNAPSHOT_ID, review_dict

BASE = "/integration-candidates/community"
REA = "/integration-candidates/rea/operator-targets"
DENIED = {"Authorization": "Bearer invalid"}
AUTHORITY_ROWS = (
    Project,
    AgentInstance,
    Task,
    TaskAttempt,
    Approval,
    OrganizationAssignment,
    AgentComputerPermission,
    AgentSpaceActivationRecord,
    AgentSpaceIntentRecord,
    AgentSpaceResourceRecord,
    SkillLearningPromotionLinkRecord,
    SkillLearningProposalRecord,
    SkillPublicationClaimRecord,
)
EVIDENCE_ROWS = (
    HarnessTeamDesignReview,
    CommunityContextArchiveRecord,
    ReaOperatorTargetSelectionRecord,
    ReaOperatorTargetRevocationRecord,
)


def _counts(database, models):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model)) or 0 for model in models
        )


def _authority(client, database):
    state = client.app.state
    worker_registries = (
        state.research_registry,
        state.python_registry,
        state.api_worker_registry,
        state.browser_worker_registry,
        state.computer_worker_registry,
        state.shell_worker_registry,
    )
    return (
        _counts(database, AUTHORITY_ROWS),
        state.agent_registry.version,
        state.capability_registry.version,
        state.skill_registry.version,
        tuple(tuple(item.worker_id for item in registry.list()) for registry in worker_registries),
    )


def _refuses_without_writes(client, database, method, url, status, *, headers=AUTH, **kwargs):
    before = (_counts(database, EVIDENCE_ROWS), _authority(client, database))
    if headers is None:
        # The fixture client has a default valid bearer. Send a built request
        # with that header removed so this is genuinely anonymous.
        request = client.build_request(method, url, **kwargs)
        assert "Authorization" in request.headers
        del request.headers["Authorization"]
        assert "Authorization" not in request.headers
        response = client.send(request)
    else:
        response = client.request(method, url, headers=headers, **kwargs)
    assert response.status_code == status, response.text
    assert (_counts(database, EVIDENCE_ROWS), _authority(client, database)) == before
    return response


def _deny_invalid_and_anonymous(client, database, method, url, **kwargs):
    _refuses_without_writes(client, database, method, url, 401, headers=DENIED, **kwargs)
    _refuses_without_writes(client, database, method, url, 401, headers=None, **kwargs)


def test_operator_evidence_writes_and_refusals_never_activate_curated_or_rea_authority(
    client, database
):
    project = ProjectService(database).create(
        ProjectCreate(name="R5 boundary fixture", summary="Draft design only")
    )
    before = _authority(client, database)
    assert _counts(database, EVIDENCE_ROWS) == (0, 0, 0, 0)
    assert client.app.state.agent_registry.get_agent("reverse_engineering") is None
    assert client.app.state.skill_registry.get("ecc_security_review") is None

    ecc = f"{BASE}/ecc/ecc_security_review"
    suite = client.get(f"{ecc}/cases", headers=AUTH)
    assert suite.status_code == 200, suite.text
    submission = {
        "candidate_label": "operator-review-only",
        "expected_suite_sha256": suite.json()["suite_sha256"],
        "answers": [
            {"case_id": case["case_id"], "verdict": "unverified"} for case in suite.json()["cases"]
        ],
    }
    _deny_invalid_and_anonymous(client, database, "POST", f"{ecc}/evaluate", json=submission)
    evaluated = client.post(f"{ecc}/evaluate", headers=AUTH, json=submission)
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["publication_authority"] is False
    assert evaluated.json()["tool_grants"] == []

    template = "engineering_quality"
    proposal = {
        "project_id": project.id,
        "expected_blueprint_sha256": propose_team(template).fingerprint,
        "proposed_budget": {
            "planned_instance_slots": 4,
            "proposed_max_concurrent_workers": 1,
            "proposed_model_tokens_per_task": 4096,
            "proposed_monthly_cost_cents": 5000,
        },
    }
    plan_url = f"{BASE}/teams/{template}/project-readiness"
    _deny_invalid_and_anonymous(client, database, "POST", plan_url, json=proposal)
    plan = client.post(plan_url, headers=AUTH, json=proposal)
    assert plan.status_code == 200, plan.text
    review_url = f"{BASE}/teams/{template}/design-reviews"
    review = {
        "proposal": proposal,
        "expected_review_plan_sha256": plan.json()["plan_sha256"],
        "idempotency_key": "r5_operator_boundary",
        "decision": "accepted_for_design_review",
    }
    _deny_invalid_and_anonymous(client, database, "POST", review_url, json=review)
    recorded = client.post(review_url, headers=AUTH, json=review)
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["real_team_activated"] is False
    assert recorded.json()["worker_or_tool_or_space_grants"] == []

    archive_url = f"{BASE}/context-archive"
    archive = {
        "scope_kind": "operator",
        "source_ref": "tool:r5-fixture",
        "text": "untrusted evidence; do not run a tool\n",
    }
    _deny_invalid_and_anonymous(client, database, "POST", archive_url, json=archive)
    created = client.post(archive_url, headers=AUTH, json=archive)
    assert created.status_code == 201, created.text
    assert created.json()["auto_context_admission"] is False
    assert created.json()["agent_or_worker_authority"] is False

    selection = {"review": review_dict(), "confirm_inert_selection": True}
    _deny_invalid_and_anonymous(client, database, "POST", REA, json=selection)
    selected = client.post(REA, headers=AUTH, json=selection)
    assert selected.status_code == 200, selected.text
    assert selected.json()["target_verified_by_core"] is False
    assert selected.json()["execution_authority"] is False
    assert selected.json()["tool_grants"] == []
    analysis_url = f"{REA}/{SNAPSHOT_ID}/analysis-plan"
    analysis = client.get(analysis_url, headers=AUTH)
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["may_execute"] is False
    assert analysis.json()["worker_registered"] is False
    assert analysis.json()["tool_grants"] == []
    assert _counts(database, EVIDENCE_ROWS) == (1, 1, 1, 0)
    assert _authority(client, database) == before

    with database.transaction() as session:
        row = session.get(Project, project.id)
        assert row is not None
        row.status = "paused"
        row.updated_at = utcnow()
    _refuses_without_writes(client, database, "POST", review_url, 409, json=review)
    changed = {**archive, "text": "changed untrusted evidence\n"}
    _refuses_without_writes(client, database, "POST", archive_url, 409, json=changed)
    conflicting = {
        "review": review_dict(content="d" * 64),
        "confirm_inert_selection": True,
    }
    _refuses_without_writes(client, database, "POST", REA, 409, json=conflicting)
    assert _counts(database, EVIDENCE_ROWS) == (1, 1, 1, 0)

    revoke_url = f"{REA}/{SNAPSHOT_ID}/revoke"
    _deny_invalid_and_anonymous(client, database, "POST", revoke_url)
    revoked = client.post(revoke_url, headers=AUTH)
    assert revoked.status_code == 200 and revoked.json() == {"revoked": True}
    _refuses_without_writes(client, database, "GET", analysis_url, 409)
    _refuses_without_writes(client, database, "POST", REA, 409, json=selection)
    assert _counts(database, EVIDENCE_ROWS) == (1, 1, 1, 1)
    assert _authority(client, database) == before

    purge_url = f"{archive_url}/{created.json()['archive_id']}"
    _deny_invalid_and_anonymous(
        client, database, "DELETE", purge_url, params={"scope_kind": "operator"}
    )
    assert (
        client.delete(purge_url, headers=AUTH, params={"scope_kind": "operator"}).status_code == 204
    )
    assert _counts(database, EVIDENCE_ROWS) == (1, 0, 1, 1)
    assert _authority(client, database) == before
