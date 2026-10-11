"""Durable, serialized YouTube Research submission limits: no extra grants."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_factory import AgentFactory
from app.agent_instances import AgentInstanceService
from app.application import create_app
from app.memory import MemoryService
from app.models import AgentInstance, AuditEvent, Task, utcnow
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    ProjectCreate,
    ResearchRunInput,
)
from app.youtube_research_quota import (
    MAX_NEW_TASKS,
    YoutubeResearchQuotaService,
    YoutubeResearchQuotaView,
)
from app.youtube_research_specialist import (
    admitted_youtube_research_registry,
    youtube_research_spec_digest,
)
from tests.conftest import AUTH
from tests.test_agent_execution import direct_services

SUBJECT = "youtube_topic_researcher"
ENDPOINT = "/agents"


def _setup(database, settings, *, project_name="Limited"):
    registry = admitted_youtube_research_registry(
        database, approved_spec_sha256=youtube_research_spec_digest()
    )
    _, instances, tasks, execution = direct_services(
        database, settings, agent_factory=AgentFactory(registry)
    )
    project = ProjectService(database).create(
        ProjectCreate(name=project_name, summary="Bounded task admission")
    )
    agent = instances.create(AgentInstanceCreate(agent_id=SUBJECT, project_id=project.id))
    return project, agent, tasks, execution


def _submit(execution, instance_id, number):
    return execution.submit(
        instance_id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": f"Find verifiable space-news fact set #{number}"},
            )
        ),
        f"youtube-research-quota:{instance_id}:{number}",
    )


def _count_tasks(database):
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(Task))


def test_eight_new_tasks_are_durable_and_ninth_is_atomic_refusal(database, settings):
    project, actor, tasks, execution = _setup(database, settings)
    task_ids = []
    for number in range(MAX_NEW_TASKS):
        task, created = _submit(execution, actor.id, number)
        assert created
        assert task.agent_execution_spec is not None
        assert task.agent_execution_spec.project_id == project.id
        task_ids.append(task.id)
    assert len(set(task_ids)) == MAX_NEW_TASKS
    assert _count_tasks(database) == MAX_NEW_TASKS
    receipt = YoutubeResearchQuotaService(database).preview(actor.id)
    assert receipt.schema_version == "zelo-youtube-research-new-task-quota-v1"
    assert receipt.max_new_tasks == 8
    assert receipt.rolling_window_hours == 24
    assert receipt.used_new_tasks == 8
    assert receipt.remaining_new_tasks == 0
    assert receipt.counts_failed_cancelled_and_completed_submissions is True
    assert receipt.tracks_worker_attempts_or_token_spend is False
    assert receipt.current_execution_authority_claimed is False
    assert receipt.has_live_spending_or_publishing_authority is False
    assert receipt.additional_tool_or_space_grants == ()
    assert (
        YoutubeResearchQuotaView.model_validate_json(receipt.model_dump_json(), strict=True)
        == receipt
    )

    before = _count_tasks(database)
    with database.read() as session:
        before_audit = session.scalar(select(func.count()).select_from(AuditEvent))
    with pytest.raises(DomainError, match="quota_exhausted") as denied:
        _submit(execution, actor.id, 9)
    assert denied.value.status_code == 429
    assert _count_tasks(database) == before
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == before_audit

    exact_replay, created = _submit(execution, actor.id, 0)
    assert not created
    assert exact_replay.id == task_ids[0]
    assert _count_tasks(database) == MAX_NEW_TASKS
    assert YoutubeResearchQuotaService(database).preview(actor.id).used_new_tasks == 8

    # A colliding idempotency key with changed query must still fail closed.
    with pytest.raises(DomainError, match="Idempotency key"):
        execution.submit(
            actor.id,
            AgentTaskCreate(
                task=ResearchRunInput(
                    kind="research_run",
                    payload={"query": "Different research content using the same key"},
                )
            ),
            f"youtube-research-quota:{actor.id}:0",
        )
    assert _count_tasks(database) == MAX_NEW_TASKS


def test_quota_is_scoped_to_one_instance_and_other_agent_tasks_are_unaffected(database, settings):
    _, primary, tasks, execution = _setup(database, settings)
    for i in range(8):
        _submit(execution, primary.id, i)
    assert _count_tasks(database) == 8

    project2 = ProjectService(database).create(
        ProjectCreate(name="Second independent", summary="Own instance")
    )
    second = AgentInstanceService(database, execution.factory, MemoryService(database)).create(
        AgentInstanceCreate(agent_id=SUBJECT, project_id=project2.id)
    )
    new, created = _submit(execution, second.id, 1)
    assert created and new.agent_execution_spec.agent_instance_id == second.id
    assert YoutubeResearchQuotaService(database).preview(second.id).used_new_tasks == 1
    assert YoutubeResearchQuotaService(database).preview(primary.id).used_new_tasks == 8

    baseline_project = ProjectService(database).create(
        ProjectCreate(name="Default research", summary="Different AgentSpec")
    )
    default_agent = AgentInstanceService(
        database, execution.factory, MemoryService(database)
    ).create(AgentInstanceCreate(agent_id="research", project_id=baseline_project.id))
    baseline, created = execution.submit(
        default_agent.id,
        AgentTaskCreate(
            task=ResearchRunInput(kind="research_run", payload={"query": "Read the workspace."})
        ),
        "generic-research-with-youtube-quota",
    )
    assert created and baseline.agent_execution_spec.spec.agent_id == "research"
    assert _count_tasks(database) == 10


def test_all_previous_statuses_count_and_24h_window_releases_old_slots(database, settings):
    _, actor, tasks, execution = _setup(database, settings)
    first = []
    for i in range(8):
        task, _ = _submit(execution, actor.id, i)
        first.append(task.id)
    with database.transaction() as session:
        # Task submission records, not runtime status, are quota evidence.
        session.get(Task, first[0]).status = "cancelled"
        session.get(Task, first[1]).status = "failed"
        session.get(Task, first[2]).status = "succeeded"
    assert YoutubeResearchQuotaService(database).preview(actor.id).used_new_tasks == 8
    with pytest.raises(DomainError, match="quota_exhausted"):
        _submit(execution, actor.id, 9)

    # Controlled clock used only for read-only window semantics.
    advanced = utcnow() + timedelta(hours=25)
    preview = YoutubeResearchQuotaService(database).preview(actor.id, now=advanced)
    assert preview.used_new_tasks == 0
    assert preview.remaining_new_tasks == 8
    with database.transaction() as session:
        session.get(Task, first[0]).created_at = utcnow() - timedelta(hours=25)
    assert YoutubeResearchQuotaService(database).preview(actor.id).used_new_tasks == 7
    after, created = _submit(execution, actor.id, 9)
    assert created
    assert after.id not in first
    assert _count_tasks(database) == 9


def test_operator_get_quota_is_read_only_strict_and_does_not_trigger_workers(database, settings):
    enabled_settings = settings.model_copy(
        update={
            "youtube_research_approved_spec_sha256": youtube_research_spec_digest(),
            "runner_poll_seconds": 1.0,
        }
    )
    before_tasks = _count_tasks(database)
    with TestClient(create_app(enabled_settings), headers=AUTH) as client:
        project = client.post("/projects", json={"name": "Quota API", "summary": "Read-only"})
        assert project.status_code == 201, project.text
        instance = client.post(
            "/agents", json={"agent_id": SUBJECT, "project_id": project.json()["id"]}
        )
        assert instance.status_code == 201, instance.text
        url = f"{ENDPOINT}/{instance.json()['id']}/research-quota"
        denied = client.get(url, headers={"Authorization": "Bearer incorrect"})
        assert denied.status_code == 401
        first = client.get(url)
        assert first.status_code == 200, first.text
        assert first.json()["used_new_tasks"] == 0
        assert first.json()["remaining_new_tasks"] == 8
        assert first.json()["instance_status"] == "active"
        assert first.json()["tracks_worker_attempts_or_token_spend"] is False
        assert first.json()["additional_tool_or_space_grants"] == []

        created = client.post(
            f"{ENDPOINT}/{instance.json()['id']}/tasks",
            json={
                "task": {
                    "kind": "research_run",
                    "payload": {"query": "Compare source-grounded astronomy topics"},
                }
            },
            headers={**AUTH, "Idempotency-Key": "quota-api-one"},
        )
        assert created.status_code == 201, created.text
        queried = client.get(url)
        assert queried.status_code == 200
        assert queried.json()["used_new_tasks"] == 1
        assert queried.json()["remaining_new_tasks"] == 7

        missing = client.get(f"{ENDPOINT}/00000000-0000-4000-8000-000000000000/research-quota")
        assert missing.status_code == 404

    assert _count_tasks(database) == before_tasks + 1
    with database.read() as session:
        actor = session.get(AgentInstance, instance.json()["id"])
        assert actor is not None
        assert actor.status == "active"
        assert session.scalar(select(func.count()).select_from(AuditEvent)) >= 1


def test_permanent_quota_invariant_refuses_forged_grants(database, settings):
    _, actor, _tasks, _execution = _setup(database, settings)
    record = YoutubeResearchQuotaService(database).preview(actor.id).model_dump(mode="json")
    altered = json.loads(json.dumps(record))
    altered["has_live_spending_or_publishing_authority"] = True
    with pytest.raises(ValidationError):
        YoutubeResearchQuotaView.model_validate_json(json.dumps(altered), strict=True)
    altered = json.loads(json.dumps(record))
    altered["additional_tool_or_space_grants"] = ["upload_video"]
    with pytest.raises(ValidationError):
        YoutubeResearchQuotaView.model_validate_json(json.dumps(altered), strict=True)


def test_concurrent_ninth_submissions_cannot_oversubscribe(database, settings):
    _, actor, _tasks, execution = _setup(database, settings)
    for i in range(7):
        _submit(execution, actor.id, i)

    def contender(number):
        try:
            _task, created = _submit(execution, actor.id, number)
            return "accepted" if created else "replay"
        except DomainError as error:
            assert error.status_code == 429
            return "limited"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(list(pool.map(contender, (8, 9))))
    assert outcomes == ["accepted", "limited"]
    assert YoutubeResearchQuotaService(database).preview(actor.id).used_new_tasks == 8
    assert _count_tasks(database) == 8


def test_retired_then_recreated_agent_cannot_reset_project_quota(database, settings):
    project, first, _tasks, execution = _setup(database, settings)
    for index in range(MAX_NEW_TASKS):
        _submit(execution, first.id, index)
    assert YoutubeResearchQuotaService(database).preview(first.id).remaining_new_tasks == 0

    service = AgentInstanceService(database, execution.factory, MemoryService(database))
    retired = service.transition(first.id, AgentInstanceTransition(action="retire"))
    assert retired.status == "retired"
    replacement = service.create(AgentInstanceCreate(agent_id=SUBJECT, project_id=project.id))
    assert replacement.id != first.id
    assert replacement.status == "active"
    quota = YoutubeResearchQuotaService(database).preview(replacement.id)
    assert quota.quota_scoped_to_project_and_agent_id is True
    assert quota.used_new_tasks == 8
    assert quota.remaining_new_tasks == 0
    with pytest.raises(DomainError, match="quota_exhausted"):
        _submit(execution, replacement.id, 9)
    assert _count_tasks(database) == 8


def test_http_operator_eighth_slot_and_ninth_429_with_idempotent_retry(database, settings):
    """Admission limit is an actual HTTP contract, not only a service error."""
    configuration = settings.model_copy(
        update={
            "youtube_research_approved_spec_sha256": youtube_research_spec_digest(),
            "runner_poll_seconds": 1.0,
        }
    )
    with TestClient(create_app(configuration), headers=AUTH) as client:
        project = client.post("/projects", json={"name": "HTTP quota", "summary": "Bounded API"})
        assert project.status_code == 201, project.text
        actor = client.post(
            "/agents", json={"agent_id": SUBJECT, "project_id": project.json()["id"]}
        )
        assert actor.status_code == 201, actor.text
        agent_id = actor.json()["id"]
        url = f"/agents/{agent_id}/tasks"
        saved = []
        for index in range(MAX_NEW_TASKS):
            result = client.post(
                url,
                json={
                    "task": {
                        "kind": "research_run",
                        "payload": {"query": f"Review public video topic {index}"},
                    }
                },
                headers={**AUTH, "Idempotency-Key": f"http-youtube-quota-{index}"},
            )
            assert result.status_code == 201, result.text
            saved.append(result.json()["id"])
        assert len(set(saved)) == MAX_NEW_TASKS
        denied = client.post(
            url,
            json={"task": {"kind": "research_run", "payload": {"query": "Ninth topic"}}},
            headers={**AUTH, "Idempotency-Key": "http-youtube-quota-nine"},
        )
        assert denied.status_code == 429, denied.text
        assert denied.json()["detail"] == "youtube_research_new_task_quota_exhausted"

        replay = client.post(
            url,
            json={
                "task": {
                    "kind": "research_run",
                    "payload": {"query": "Review public video topic 0"},
                }
            },
            headers={**AUTH, "Idempotency-Key": "http-youtube-quota-0"},
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == saved[0]

        conflict = client.post(
            url,
            json={"task": {"kind": "research_run", "payload": {"query": "Changed topic"}}},
            headers={**AUTH, "Idempotency-Key": "http-youtube-quota-0"},
        )
        assert conflict.status_code == 409, conflict.text

        quota = client.get(f"/agents/{agent_id}/research-quota")
        assert quota.status_code == 200
        assert quota.json()["used_new_tasks"] == MAX_NEW_TASKS
        assert quota.json()["remaining_new_tasks"] == 0
    assert _count_tasks(database) == MAX_NEW_TASKS
