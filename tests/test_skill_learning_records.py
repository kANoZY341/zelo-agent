"""Read-only skill-learning evidence against isolated migrated Core records."""

from copy import deepcopy
from uuid import uuid4

import pytest

from app.models import AgentInstance, Task, TaskAttempt, utcnow
from app.policy import State
from app.schemas import AgentInstanceCreate, AgentTaskCreate, ResearchRunInput, ResearchRunResult
from app.skill_learning import SkillLearningEvidenceRef, verify_skill_evidence_refs
from app.skill_learning_records import CoreSkillLearningRecords, task_attempt_source_id
from app.tasks import audit
from tests.test_agent_execution import direct_services, project


@pytest.fixture
def successful_attempt(database, settings):
    factory, instances, tasks, execution = direct_services(database, settings)
    owned_project = project(database, "Skill evidence")
    instance = instances.create(
        AgentInstanceCreate(agent_id="research", project_id=owned_project.id)
    )
    task, created = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Inspect one bounded project source."},
            )
        ),
        "skill-records:research-attempt",
    )
    assert created is True
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    result = ResearchRunResult(
        answer="One retained source supports the answer.",
        sources=[
            {
                "id": "S1",
                "tool": "repo_status",
                "locator": "project repository",
                "sha256": "a" * 64,
                "truncated": False,
            }
        ],
        alias="local_general",
        actual_model="test-model",
        model_steps=1,
        tool_calls=1,
        usage=[],
        completed_at=utcnow(),
    ).model_dump(mode="json")
    tasks.finish(claimed, State.SUCCEEDED, result)
    records = CoreSkillLearningRecords(database, factory)
    source_id = task_attempt_source_id(task.id, claimed.attempt_id)
    snapshot = records.evidence_snapshot("task_attempt", source_id)
    assert snapshot is not None
    ref = SkillLearningEvidenceRef(
        kind="task_attempt", source_id=source_id, content_sha256=snapshot.content_sha256
    )
    return records, instance, task, claimed, ref


def test_current_owned_research_attempt_is_digest_bound(successful_attempt):
    records, instance, _task, _claimed, ref = successful_attempt
    agent = records.agent_snapshot(instance.id)
    assert agent is not None and agent.status == "active"
    resolved = records.evidence_snapshot(ref.kind, ref.source_id)
    assert resolved is not None
    assert resolved.owner_agent_instance_id == instance.id
    assert resolved.agent_registry_version == agent.agent_registry_version
    assert resolved.terminal is True and resolved.accepted is True
    assert resolved.visible_to_agent_instance_ids == ()
    assert verify_skill_evidence_refs(
        agent_instance_id=instance.id,
        agent_registry_version=agent.agent_registry_version,
        evidence_refs=(ref,),
        resolve=records.evidence_snapshot,
    ) == (ref,)
    assert records.evidence_snapshot(ref.kind, ref.source_id) == resolved


def test_unowned_or_unsupported_sources_default_deny(successful_attempt):
    records, instance, task, claimed, ref = successful_attempt
    assert records.evidence_snapshot("operator_feedback", ref.source_id) is None
    assert records.evidence_snapshot("promotion_review", ref.source_id) is None
    assert records.evidence_snapshot("task_attempt", "task:attempt-1") is None
    assert (
        records.evidence_snapshot("task_attempt", task_attempt_source_id(task.id, str(uuid4())))
        is None
    )
    assert (
        records.evidence_snapshot(
            "task_attempt", task_attempt_source_id(str(uuid4()), claimed.attempt_id)
        )
        is None
    )
    assert records.agent_snapshot("not-a-uuid") is None
    other = str(uuid4())
    with pytest.raises(ValueError, match="skill_learning_evidence_out_of_scope"):
        verify_skill_evidence_refs(
            agent_instance_id=other,
            agent_registry_version=records.agent_snapshot(instance.id).agent_registry_version,
            evidence_refs=(ref,),
            resolve=records.evidence_snapshot,
        )


def test_result_content_change_invalidates_retained_ref(database, successful_attempt):
    records, instance, task, claimed, ref = successful_attempt
    with database.transaction() as session:
        stored_task = session.get(Task, task.id)
        stored_attempt = session.get(TaskAttempt, claimed.attempt_id)
        changed = deepcopy(stored_attempt.result)
        changed["answer"] = "A different answer."
        stored_attempt.result = changed
        stored_task.result = changed
    changed_snapshot = records.evidence_snapshot(ref.kind, ref.source_id)
    assert changed_snapshot is not None
    assert changed_snapshot.content_sha256 != ref.content_sha256
    with pytest.raises(ValueError, match="skill_learning_evidence_stale"):
        verify_skill_evidence_refs(
            agent_instance_id=instance.id,
            agent_registry_version=records.agent_snapshot(instance.id).agent_registry_version,
            evidence_refs=(ref,),
            resolve=records.evidence_snapshot,
        )


@pytest.mark.parametrize(
    "change", ("failed_attempt", "result_mismatch", "invalid_result", "wrong_kind")
)
def test_unaccepted_or_inconsistent_attempt_denies(database, successful_attempt, change):
    records, _instance, task, claimed, ref = successful_attempt
    with database.transaction() as session:
        stored_task = session.get(Task, task.id)
        stored_attempt = session.get(TaskAttempt, claimed.attempt_id)
        if change == "failed_attempt":
            stored_attempt.outcome = State.FAILED
        elif change == "result_mismatch":
            stored_task.result = {"different": True}
        elif change == "invalid_result":
            stored_task.result = {"answer": "missing required provenance"}
            stored_attempt.result = stored_task.result
        elif change == "wrong_kind":
            stored_task.kind = "api_run"
    assert records.evidence_snapshot(ref.kind, ref.source_id) is None


@pytest.mark.parametrize("change", ("paused", "paused_project"))
def test_current_agent_and_project_required(database, successful_attempt, change):
    records, instance, _task, _claimed, ref = successful_attempt
    with database.transaction() as session:
        stored = session.get(AgentInstance, instance.id)
        if change == "paused":
            stored.status = "paused"
        elif change == "paused_project":
            from app.models import Project

            session.get(Project, stored.project_id).status = "paused"
    assert records.evidence_snapshot(ref.kind, ref.source_id) is None


@pytest.mark.parametrize("event", ("agent_execution_bound", "execution_completed"))
def test_duplicate_append_only_audit_event_denies(database, successful_attempt, event):
    records, _instance, task, claimed, ref = successful_attempt
    with database.transaction() as session:
        audit(
            session,
            session.get(Task, task.id),
            event,
            attempt_id=claimed.attempt_id if event == "execution_completed" else None,
        )
    assert records.evidence_snapshot(ref.kind, ref.source_id) is None
