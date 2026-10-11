"""Real per-Project Research attempt budget shared across all 4 company roles."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_factory import default_agent_registry
from app.application import create_app
from app.models import AuditEvent, TaskAttempt, utcnow
from app.policy import State
from app.projects import ProjectService
from app.schemas import AgentTaskCreate, ProjectCreate, ResearchRunInput
from app.youtube_company_attempt_budget import (
    MAX_TEAM_ATTEMPTS,
    YoutubeCompanyAttemptBudgetService,
    YoutubeCompanyAttemptBudgetView,
)
from app.youtube_company_team import (
    TEAM_ROLE_IDS,
    youtube_company_registry_digest,
)
from tests.conftest import AUTH
from tests.test_youtube_company_team import _config, _real_company_graph


def _actor(company, index):
    if index == 0:
        return company["vp"], None
    entry = company["created"][index - 1]
    return entry.child_instance, entry.delegation.id


def _new(company, *, index, key):
    instance, delegation_id = _actor(company, index)
    body = AgentTaskCreate(
        task=ResearchRunInput(
            kind="research_run",
            campaign_id=company["campaign"].id,
            payload={
                "query": f"Research trustworthy evidence for editorial role {index}, key {key}"
            },
        ),
        delegation_id=delegation_id,
    )
    result, created = company["execution"].submit(instance.id, body, key)
    assert created
    assert result.status == State.PENDING
    return result


def _run_failures(company, *, index, count, key):
    result = _new(company, index=index, key=key)
    for step in range(count):
        if step:
            reset = company["tasks"].retry(result.id)
            assert reset.status == State.PENDING
        worker = company["tasks"].claim()
        assert worker is not None
        assert worker.task_id == result.id
        assert worker.agent_execution is not None
        assert worker.agent_execution.spec.agent_id == TEAM_ROLE_IDS[index]
        company["tasks"].finish(worker, State.FAILED, error=f"synthetic-failure-{index}-{step}")
    assert company["tasks"].get(result.id).status == State.FAILED
    return result


def _attempt_count(database):
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(TaskAttempt))


def _report(database, project_id, *, now=None):
    return YoutubeCompanyAttemptBudgetService(database).preview(
        project_id,
        current_registry_version=youtube_company_registry_digest(),
        now=now,
    )


def test_24_started_attempts_shared_across_vp_director_manager_specialist(database, settings):
    company = _real_company_graph(database, settings)
    project = company["project"]
    assert _report(database, project.id).counted_research_worker_attempts == 0

    for idx in range(4):
        _run_failures(company, index=idx, count=6, key=f"team-six-{idx}")

    assert _attempt_count(database) == 24
    report = _report(database, project.id)
    assert report.schema_version == "zelo-youtube-company-attempt-budget-v1"
    assert report.max_research_worker_attempts == MAX_TEAM_ATTEMPTS == 24
    assert report.counted_research_worker_attempts == 24
    assert report.remaining_research_worker_attempts == 0
    assert report.rolling_window_hours == 24
    assert tuple(report.trusted_team_roles) == TEAM_ROLE_IDS
    assert report.counts_failed_retries_and_completed_attempts
    assert report.shared_across_roles_and_retired_instances
    assert report.rejects_claim_before_worker_dispatch
    assert report.current_company_registry_enabled
    assert report.grants_new_tools_spaces_delegations_or_hires is False
    assert report.measures_money_tokens_or_provider_costs is False
    assert (
        YoutubeCompanyAttemptBudgetView.model_validate_json(report.model_dump_json(), strict=True)
        == report
    )

    extra = _new(company, index=3, key="team-over-budget-task-25")
    with database.read() as session:
        before = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.task_id == extra.id,
                AuditEvent.event == "execution_started",
            )
        )
        assert before == 0
    assert company["tasks"].claim() is None
    denied = company["tasks"].get(extra.id)
    assert denied.status == State.CANCELLED
    assert denied.error == "youtube_company_attempt_budget_exhausted"
    assert _attempt_count(database) == 24

    with database.read() as session:
        denial = session.scalar(
            select(AuditEvent).where(
                AuditEvent.task_id == extra.id, AuditEvent.event == "execution_denied"
            )
        )
        assert denial is not None
        assert denial.details["reason"] == "youtube_company_attempt_budget_exhausted"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.task_id == extra.id,
                    AuditEvent.event == "execution_started",
                )
            )
            == 0
        )
    company["tasks"].retry(extra.id)
    assert company["tasks"].claim() is None
    assert _attempt_count(database) == 24


def test_24_hour_attempt_budget_expires_without_deleting_terminal_evidence(database, settings):
    company = _real_company_graph(database, settings)
    task = _run_failures(company, index=3, count=1, key="expiry-proof")
    assert _report(database, company["project"].id).counted_research_worker_attempts == 1

    far_future = utcnow() + timedelta(hours=25)
    assert (
        _report(database, company["project"].id, now=far_future).counted_research_worker_attempts
        == 0
    )

    with database.transaction() as session:
        first_attempt = session.scalar(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
        assert first_attempt is not None
        first_attempt.started_at = utcnow() - timedelta(hours=25)
    report = _report(database, company["project"].id)
    assert report.counted_research_worker_attempts == 0
    assert report.remaining_research_worker_attempts == 24
    with database.read() as session:
        persisted = session.scalar(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
        assert persisted is not None and persisted.outcome == State.FAILED


def test_other_project_and_generic_research_do_not_consume_company_budget(database, settings):
    from app.agent_instances import AgentInstanceService
    from app.memory import MemoryService
    from app.schemas import AgentInstanceCreate

    company = _real_company_graph(database, settings)
    _run_failures(company, index=0, count=3, key="root-vp-first-three")
    other = ProjectService(database).create(
        ProjectCreate(name="Second project", summary="Separate team budget")
    )
    assert _report(database, other.id).counted_research_worker_attempts == 0

    generic = AgentInstanceService(database, company["factory"], MemoryService(database)).create(
        AgentInstanceCreate(agent_id="research", project_id=other.id)
    )
    result, created = company["execution"].submit(
        generic.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run", payload={"query": "Generic local research task"}
            )
        ),
        "generic-is-not-company-worker",
    )
    assert created
    claimed = company["tasks"].claim()
    assert claimed is not None and claimed.task_id == result.id
    company["tasks"].finish(claimed, State.FAILED, error="synthetic-generic-worker")
    assert _attempt_count(database) == 4
    assert _report(database, other.id).counted_research_worker_attempts == 0
    assert _report(database, company["project"].id).counted_research_worker_attempts == 3


def test_operator_only_project_attempt_receipt_has_no_implicit_authority(database, settings):
    company = _real_company_graph(database, settings)
    _run_failures(company, index=1, count=1, key="director-report-proof")
    project_id = company["project"].id
    endpoint = f"/projects/{project_id}/youtube-company-attempt-budget"
    with TestClient(
        create_app(_config(settings)),
        headers=AUTH,
    ) as client:
        assert client.get(endpoint, headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["counted_research_worker_attempts"] == 1
        assert data["remaining_research_worker_attempts"] == 23
        assert data["current_company_registry_enabled"] is True
        assert data["grants_new_tools_spaces_delegations_or_hires"] is False
        assert data["measures_money_tokens_or_provider_costs"] is False
        missing = client.get(
            "/projects/00000000-0000-4000-8000-000000000000/youtube-company-attempt-budget"
        )
        assert missing.status_code == 404

    assert _attempt_count(database) == 1
    baseline_version = default_agent_registry().version
    assert (
        YoutubeCompanyAttemptBudgetService(database)
        .preview(
            project_id,
            current_registry_version=baseline_version,
        )
        .current_company_registry_enabled
        is False
    )


def test_shared_budget_forged_positive_authority_fields_are_refused(database, settings):
    company = _real_company_graph(database, settings)
    record = _report(database, company["project"].id).model_dump(mode="json")
    for field in (
        "grants_new_tools_spaces_delegations_or_hires",
        "measures_money_tokens_or_provider_costs",
    ):
        altered = json.loads(json.dumps(record))
        altered[field] = True
        with pytest.raises(ValidationError):
            YoutubeCompanyAttemptBudgetView.model_validate_json(json.dumps(altered), strict=True)
    forged_roles = json.loads(json.dumps(record))
    forged_roles["trusted_team_roles"][-1] = "generic_research"
    with pytest.raises(ValidationError):
        YoutubeCompanyAttemptBudgetView.model_validate_json(json.dumps(forged_roles), strict=True)


def test_concurrent_24th_claim_admits_exactly_one_worker(database, settings):
    company = _real_company_graph(database, settings)
    _run_failures(company, index=3, count=23, key="twenty-three-worker-attempts")
    assert _report(database, company["project"].id).counted_research_worker_attempts == 23
    a = _new(company, index=3, key="concurrent-company-claim-one")
    b = _new(company, index=3, key="concurrent-company-claim-two")

    def contender(_index):
        try:
            return company["tasks"].claim()
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, range(2)))

    assert all(not isinstance(item, Exception) for item in results)
    accepted = [item for item in results if item is not None]
    denied = [item for item in results if item is None]
    assert len(accepted) == 1 and len(denied) == 1
    assert accepted[0].task_id in {a.id, b.id}
    assert _attempt_count(database) == 24
    assert _report(database, company["project"].id).counted_research_worker_attempts == 24
    refused_id = b.id if accepted[0].task_id == a.id else a.id
    rejected = company["tasks"].get(refused_id)
    # Core may defer the second claim while the first is still RUNNING under
    # Campaign/Worker capacity checks. The attempt cap is enforced when that
    # queued Task actually reaches its next claim, never before selection.
    if rejected.status == State.PENDING:
        company["tasks"].finish(accepted[0], State.FAILED, error="synthetic-first-concurrent-claim")
        assert company["tasks"].claim() is None
        rejected = company["tasks"].get(refused_id)
    assert rejected.status == State.CANCELLED
    assert rejected.error == "youtube_company_attempt_budget_exhausted"
    assert _attempt_count(database) == 24


def test_two_campaigns_same_company_project_share_one_attempt_budget(database, settings):
    """Campaigns authorize work; they do not reset the real Project-wide bound."""

    from app.campaigns import CampaignService
    from app.schemas import CampaignCreate, GoalCreate, GoalSpec

    company = _real_company_graph(database, settings)
    _run_failures(company, index=0, count=2, key="first-campaign-vp")
    projects = ProjectService(database)
    another_goal = projects.create_goal(
        company["project"].id,
        GoalCreate(
            title="Different editorial Campaign",
            spec=GoalSpec(
                objective="Bound additional sourced-video research",
                success_criteria=["one other sourced idea"],
                constraints=["no channel access", "no publication"],
            ),
        ),
    )
    second = CampaignService(database).create(
        another_goal.id,
        CampaignCreate(title="Second Campaign in same Project"),
    )
    vp = company["vp"]
    task, created = company["execution"].submit(
        vp.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Research an independently grounded ecology topic"},
                campaign_id=second.id,
            )
        ),
        "second-company-campaign-vp-task",
    )
    assert created and task.status == State.PENDING
    worker = company["tasks"].claim()
    assert worker is not None and worker.task_id == task.id
    company["tasks"].finish(worker, State.FAILED, error="synthetic-second-campaign")
    assert _report(database, company["project"].id).counted_research_worker_attempts == 3
    assert _report(database, company["project"].id).remaining_research_worker_attempts == 21
