"""Inert skill-learning persistence on migrated isolated SQLite databases."""

from copy import deepcopy

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec, default_agent_registry
from app.models import (
    AgentInstance,
    SkillLearningEvent,
    SkillLearningProposalRecord,
    Task,
    TaskAttempt,
    utcnow,
)
from app.policy import State
from app.schemas import AgentInstanceCreate, AgentTaskCreate, ResearchRunInput, ResearchRunResult
from app.skill_learning import SkillLearningEvidenceRef
from app.skill_learning_records import CoreSkillLearningRecords, task_attempt_source_id
from app.skill_learning_store import SkillLearningProposalStore
from app.skills import default_skill_registry
from tests.test_agent_execution import direct_services, project
from tests.test_skill_learning import revision


@pytest.fixture
def proposal_case(database, settings):
    default = default_agent_registry()
    specs = tuple(
        AgentSpec.model_validate({**spec.model_dump(mode="json"), "skill_learning": "propose"})
        if spec.agent_id == "research"
        else spec
        for spec in default.list_agents()
    )
    factory = AgentFactory(AgentRegistry(specs, default.list_departments()))
    _factory, instances, tasks, execution = direct_services(
        database, settings, agent_factory=factory
    )
    owned_project = project(database, "Skill proposal")
    agent = instances.create(AgentInstanceCreate(agent_id="research", project_id=owned_project.id))
    task, created = execution.submit(
        agent.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run",
                payload={"query": "Inspect one bounded project source."},
            )
        ),
        "skill-store:research-attempt",
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
    evidence = records.evidence_snapshot("task_attempt", source_id)
    assert evidence is not None
    ref = SkillLearningEvidenceRef(
        kind="task_attempt", source_id=source_id, content_sha256=evidence.content_sha256
    )
    candidate = revision(version=1, space_permissions=())
    store = SkillLearningProposalStore(database, factory, default_skill_registry())
    return store, agent, task, claimed, candidate, ref


def test_submission_is_frozen_idempotent_and_reviewable(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    first, created = store.submit(
        agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,)
    )
    assert created is True
    assert first.status == "proposed"
    assert first.proposal.agent_instance_id == agent.id
    assert first.proposal.runtime_authority is False
    assert first.proposal.authority_widening is False
    assert store.get(first.proposal.proposal_id) == first
    assert store.review_basis(first.proposal.proposal_id) == first
    retried, created = store.submit(
        agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,)
    )
    assert created is False and retried == first
    with database.read() as session:
        assert len(session.scalars(select(SkillLearningProposalRecord)).all()) == 1
        assert len(session.scalars(select(SkillLearningEvent)).all()) == 1


def test_disabled_policy_and_cross_agent_evidence_never_persist(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    disabled = SkillLearningProposalStore(
        database, AgentFactory(default_agent_registry()), default_skill_registry()
    )
    with pytest.raises(ValueError):
        disabled.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with pytest.raises(ValueError):
        store.submit(
            agent_instance_id="00000000-0000-4000-8000-000000000001",
            revision=candidate,
            evidence_refs=(ref,),
        )
    with database.read() as session:
        assert session.scalars(select(SkillLearningProposalRecord)).all() == []


def test_review_basis_rechecks_content_and_current_identity(database, proposal_case):
    store, agent, task, claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with database.transaction() as session:
        stored_task = session.get(Task, task.id)
        stored_attempt = session.get(TaskAttempt, claimed.attempt_id)
        changed = deepcopy(stored_attempt.result)
        changed["answer"] = "Changed retained result."
        stored_attempt.result = changed
        stored_task.result = changed
    assert store.get(stored.proposal.proposal_id) == stored
    assert store.review_basis(stored.proposal.proposal_id) is None
    stale_registry = SkillLearningProposalStore(
        database, AgentFactory(default_agent_registry()), default_skill_registry()
    )
    assert stale_registry.review_basis(stored.proposal.proposal_id) is None
    with database.transaction() as session:
        session.get(AgentInstance, agent.id).status = "paused"
    assert store.review_basis(stored.proposal.proposal_id) is None


@pytest.mark.parametrize("table", ("skill_learning_proposals", "skill_learning_events"))
def test_persisted_records_are_append_only(database, proposal_case, table):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(text(f"DELETE FROM {table}"))
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(text(f"UPDATE {table} SET id = id"))
    assert store.get(stored.proposal.proposal_id) == stored


def test_forged_frozen_revision_cannot_be_submitted(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    forged = candidate.model_copy(update={"instructions": "Mutated after digest freeze."})
    with pytest.raises(ValueError, match="procedure_digest_mismatch"):
        store.submit(agent_instance_id=agent.id, revision=forged, evidence_refs=(ref,))
    with database.read() as session:
        assert session.scalars(select(SkillLearningProposalRecord)).all() == []


def test_review_decision_cannot_be_written_by_this_slice(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.add(
                SkillLearningEvent(
                    proposal_id=stored.proposal.proposal_id,
                    sequence=2,
                    event="accepted",
                )
            )
    assert store.get(stored.proposal.proposal_id) == stored
