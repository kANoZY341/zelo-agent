"""Claim-time attempt budget closes retry bypass of new-Task admission quota."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_instances import AgentInstanceService
from app.application import create_app
from app.memory import MemoryService
from app.models import AuditEvent, TaskAttempt, utcnow
from app.policy import State
from app.projects import ProjectService
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    ProjectCreate,
    ResearchRunInput,
)
from app.youtube_research_attempt_quota import (
    MAX_ATTEMPTS,
    YoutubeResearchAttemptQuotaService,
    YoutubeResearchAttemptQuotaView,
)
from app.youtube_research_specialist import (
    youtube_research_spec_digest,
)
from tests.conftest import AUTH
from tests.test_youtube_research_quota import _setup, _submit

AGENT_ID = "youtube_topic_researcher"


def _attempt_count(database):
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(TaskAttempt))


def _exhaust(tasks, execution, agent_id):
    task, was_created = _submit(execution, agent_id, 0)
    assert was_created
    attempt_ids = []
    for i in range(MAX_ATTEMPTS):
        if i:
            retried = tasks.retry(task.id)
            assert retried.status == State.PENDING
        claimed = tasks.claim()
        assert claimed is not None
        assert claimed.task_id == task.id
        assert claimed.agent_execution is not None
        assert claimed.agent_execution.spec.agent_id == AGENT_ID
        assert claimed.attempt_id not in attempt_ids
        attempt_ids.append(claimed.attempt_id)
        tasks.finish(claimed, State.FAILED, error=f"synthetic_research_failure_{i}")
    assert len(attempt_ids) == MAX_ATTEMPTS
    return task, attempt_ids


def test_max_twelve_worker_attempts_survives_retries_and_denies_before_dispatch(database, settings):
    project, actor, tasks, execution = _setup(database, settings)
    original, ids = _exhaust(tasks, execution, actor.id)
    assert _attempt_count(database) == MAX_ATTEMPTS
    result = YoutubeResearchAttemptQuotaService(database).preview(actor.id)
    assert result.project_id == project.id
    assert result.used_attempts == MAX_ATTEMPTS
    assert result.max_attempts == 12
    assert result.remaining_attempts == 0
    assert result.project_scoped_across_agent_replacements is True
    assert result.counts_failures_retries_and_completed_attempts is True
    assert result.claim_rejection_creates_no_attempt is True
    assert result.measures_tokens_or_money is False
    assert result.grants_new_tasks_or_tools_or_spaces is False
    assert (
        YoutubeResearchAttemptQuotaView.model_validate_json(result.model_dump_json(), strict=True)
        == result
    )

    queued, created = _submit(execution, actor.id, 1)
    assert created
    with database.read() as session:
        audit_before = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.task_id == queued.id, AuditEvent.event == "execution_started")
        )
    assert tasks.claim() is None
    refused = tasks.get(queued.id)
    assert refused.status == State.CANCELLED
    assert refused.error == "youtube_research_attempt_quota_exhausted"
    assert _attempt_count(database) == MAX_ATTEMPTS
    with database.read() as session:
        audit_after = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.task_id == queued.id, AuditEvent.event == "execution_started")
        )
        denial = session.scalar(
            select(AuditEvent).where(
                AuditEvent.task_id == queued.id, AuditEvent.event == "execution_denied"
            )
        )
        assert denial is not None
        assert denial.details.get("reason") == "youtube_research_attempt_quota_exhausted"
    assert audit_before == audit_after == 0

    # Retrying a cancelled Task cannot create another Worker attempt either.
    tasks.retry(queued.id)
    assert tasks.claim() is None
    assert tasks.get(queued.id).status == State.CANCELLED
    assert _attempt_count(database) == MAX_ATTEMPTS
    assert len(ids) == 12
    assert tasks.get(original.id).status == State.FAILED


def test_an_older_attempt_expires_without_erasing_persisted_failure(database, settings):
    _, actor, tasks, execution = _setup(database, settings)
    original, ids = _exhaust(tasks, execution, actor.id)
    with database.transaction() as session:
        old = session.get(TaskAttempt, ids[0])
        old.started_at = utcnow() - timedelta(hours=25)
    preview = YoutubeResearchAttemptQuotaService(database).preview(actor.id)
    assert preview.used_attempts == MAX_ATTEMPTS - 1
    assert preview.remaining_attempts == 1
    new, created = _submit(execution, actor.id, 1)
    assert created
    claimed = tasks.claim()
    assert claimed is not None
    assert claimed.task_id == new.id
    assert claimed.agent_execution.spec.agent_id == AGENT_ID
    assert _attempt_count(database) == MAX_ATTEMPTS + 1
    assert YoutubeResearchAttemptQuotaService(database).preview(actor.id).used_attempts == 12
    with database.read() as session:
        assert session.get(TaskAttempt, ids[0]).outcome == State.FAILED


def test_retiring_and_recreating_the_actor_cannot_reset_project_attempt_budget(database, settings):
    project, actor, tasks, execution = _setup(database, settings)
    _exhaust(tasks, execution, actor.id)
    agency = AgentInstanceService(database, execution.factory, MemoryService(database))
    assert agency.transition(actor.id, AgentInstanceTransition(action="retire")).status == "retired"
    replacement = agency.create(AgentInstanceCreate(agent_id=AGENT_ID, project_id=project.id))
    assert replacement.id != actor.id
    assert YoutubeResearchAttemptQuotaService(database).preview(replacement.id).used_attempts == 12
    new, _ = _submit(execution, replacement.id, 1)
    assert tasks.claim() is None
    assert tasks.get(new.id).error == "youtube_research_attempt_quota_exhausted"
    assert _attempt_count(database) == 12


def test_another_project_and_baseline_research_remain_independently_claimable(database, settings):
    _, first, tasks, execution = _setup(database, settings)
    _exhaust(tasks, execution, first.id)

    agency = AgentInstanceService(database, execution.factory, MemoryService(database))
    unrelated = ProjectService(database).create(
        ProjectCreate(name="Other project", summary="Independent quota")
    )
    second = agency.create(AgentInstanceCreate(agent_id=AGENT_ID, project_id=unrelated.id))
    accepted, _ = _submit(execution, second.id, 5)
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == accepted.id
    tasks.finish(claimed, State.FAILED, error="synthetic-unrelated-project")
    assert YoutubeResearchAttemptQuotaService(database).preview(second.id).used_attempts == 1
    assert YoutubeResearchAttemptQuotaService(database).preview(first.id).used_attempts == 12

    baseline_project = ProjectService(database).create(
        ProjectCreate(name="Normal research", summary="Unaffected Worker")
    )
    generic = agency.create(
        AgentInstanceCreate(agent_id="research", project_id=baseline_project.id)
    )
    task, created = execution.submit(
        generic.id,
        AgentTaskCreate(
            task=ResearchRunInput(kind="research_run", payload={"query": "Review local facts."})
        ),
        "unrelated-generic-research-attempt",
    )
    assert created
    claim = tasks.claim()
    assert claim is not None and claim.task_id == task.id
    assert claim.agent_execution.spec.agent_id == "research"


def test_authenticated_attempt_quota_projection_is_inert(database, settings):
    enabled = settings.model_copy(
        update={"youtube_research_approved_spec_sha256": youtube_research_spec_digest()}
    )
    with TestClient(create_app(enabled), headers=AUTH) as client:
        project = client.post("/projects", json={"name": "Attempt report", "summary": "No grants"})
        assert project.status_code == 201
        created = client.post(
            "/agents", json={"project_id": project.json()["id"], "agent_id": AGENT_ID}
        )
        assert created.status_code == 201
        actor_id = created.json()["id"]
        endpoint = f"/agents/{actor_id}/research-attempt-quota"
        denied = client.get(endpoint, headers={"Authorization": "Bearer not-the-operator"})
        assert denied.status_code == 401
        before = client.get(endpoint)
        assert before.status_code == 200
        data = before.json()
        assert data["used_attempts"] == 0
        assert data["remaining_attempts"] == 12
        assert data["measures_tokens_or_money"] is False
        assert data["grants_new_tasks_or_tools_or_spaces"] is False
        missing = client.get("/agents/00000000-0000-4000-8000-000000000000/research-attempt-quota")
        assert missing.status_code == 404
    assert _attempt_count(database) == 0


def test_strict_projection_forged_permissions_refused(database, settings):
    _, actor, _, _ = _setup(database, settings)
    record = YoutubeResearchAttemptQuotaService(database).preview(actor.id).model_dump(mode="json")
    forged = json.loads(json.dumps(record))
    forged["grants_new_tasks_or_tools_or_spaces"] = True
    with pytest.raises(ValidationError):
        YoutubeResearchAttemptQuotaView.model_validate_json(json.dumps(forged), strict=True)
    forged = json.loads(json.dumps(record))
    forged["measures_tokens_or_money"] = True
    with pytest.raises(ValidationError):
        YoutubeResearchAttemptQuotaView.model_validate_json(json.dumps(forged), strict=True)
