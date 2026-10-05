"""Inert skill-learning persistence on migrated isolated SQLite databases."""

import asyncio
import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.agent_execution import AgentExecutionService
from app.agent_execution_contracts import AgentExecutionBinding
from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec, default_agent_registry
from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.memory import MemoryService
from app.models import (
    AgentInstance,
    SkillLearningEvent,
    SkillLearningProposalRecord,
    SkillLearningReviewRecord,
    Task,
    TaskAttempt,
    utcnow,
)
from app.policy import DomainError, State
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    ResearchRunInput,
    ResearchRunResult,
    SkillLearningProposalRunInput,
)
from app.skill_learning import (
    SkillLearningEvidenceRef,
    SkillLearningReviewChecks,
    SkillLearningReviewRequest,
)
from app.skill_learning_candidate import SkillLearningCandidateAdapter
from app.skill_learning_records import CoreSkillLearningRecords, task_attempt_source_id
from app.skill_learning_store import SkillLearningProposalStore
from app.skills import default_skill_registry
from app.tasks import TaskService
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


def checks(**overrides):
    values = {
        "evidence_supports_change": True,
        "no_tool_escalation": True,
        "authority_envelope_respected": True,
        "verification_and_failure_steps_explicit": True,
        "no_secrets_or_unrelated_private_data": True,
        "procedure_bounded_and_deterministic": True,
    }
    values.update(overrides)
    return SkillLearningReviewChecks(**values)


def review_request(decision="accepted", *, review_checks=None):
    return SkillLearningReviewRequest(
        decision=decision,
        checks=review_checks or checks(),
        rationale="Reviewed against the proposal and current authority envelope.",
    )


def test_ahmad_can_accept_only_after_fresh_review_and_no_activation(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    registry_version = store.skill_registry.version
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    decision = review_request()
    reviewed, created = store.record_review(
        stored.proposal.proposal_id, decision, OperatorPrincipal()
    )
    assert created is True
    assert reviewed.status == "accepted"
    assert reviewed.review is not None
    assert reviewed.review.reviewer_id == "ahmad"
    assert reviewed.review.proposal_fingerprint == stored.proposal.proposal_fingerprint
    assert reviewed.review.evidence_refs == stored.proposal.evidence_refs
    assert reviewed.review.authority_envelope_sha256
    assert store.skill_registry.version == registry_version
    assert store.get(stored.proposal.proposal_id) == reviewed
    retried, created = store.record_review(
        stored.proposal.proposal_id, decision, OperatorPrincipal()
    )
    assert created is False and retried == reviewed
    with database.read() as session:
        events = session.scalars(
            select(SkillLearningEvent)
            .where(SkillLearningEvent.proposal_id == stored.proposal.proposal_id)
            .order_by(SkillLearningEvent.sequence)
        ).all()
        assert [event.event for event in events] == ["proposed", "reviewed", "accepted"]
        assert [event.reviewer_id for event in events] == [None, "ahmad", "ahmad"]
        assert session.scalar(select(SkillLearningReviewRecord)) is not None


def test_rejection_is_recorded_and_conflicting_decision_is_denied(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    decision = review_request("rejected", review_checks=checks(no_tool_escalation=False))
    rejected, created = store.record_review(
        stored.proposal.proposal_id, decision, OperatorPrincipal()
    )
    assert created and rejected.status == "rejected"
    with pytest.raises(ValueError, match="skill_learning_proposal_already_reviewed"):
        store.record_review(stored.proposal.proposal_id, review_request(), OperatorPrincipal())


def test_review_requires_trusted_operator_and_complete_acceptance_checks(proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with pytest.raises(ValueError, match="skill_learning_review_checks_failed"):
        review_request(review_checks=checks(no_secrets_or_unrelated_private_data=False))
    with pytest.raises(PermissionError, match="skill_learning_reviewer_forbidden"):
        store.record_review(
            stored.proposal.proposal_id,
            review_request(),
            OperatorPrincipal(subject="untrusted"),
        )
    with pytest.raises(ValidationError):
        SkillLearningReviewRequest.model_validate(
            {
                "decision": "accepted",
                "checks": checks().model_dump(),
                "rationale": "No caller may claim reviewer identity.",
                "reviewer_id": "ahmad",
            }
        )


def test_stale_evidence_cannot_be_accepted(database, proposal_case):
    store, agent, task, claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with database.transaction() as session:
        saved_task = session.get(Task, task.id)
        saved_attempt = session.get(TaskAttempt, claimed.attempt_id)
        changed = deepcopy(saved_attempt.result)
        changed["answer"] = "Changed after proposal creation."
        saved_attempt.result = changed
        saved_task.result = changed
    with pytest.raises(ValueError, match="skill_learning_proposal_stale"):
        store.record_review(stored.proposal.proposal_id, review_request(), OperatorPrincipal())
    assert store.get(stored.proposal.proposal_id).status == "proposed"


def test_operator_api_authenticates_ahmad_without_identity_claims(client, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    client.app.state.skill_learning_store = store
    proposal_id = stored.proposal.proposal_id
    invalid = client.post(
        f"/skill-learning/proposals/{proposal_id}/review",
        headers={"Authorization": "Bearer invalid-operator-token"},
        json={"decision": "accepted", "checks": checks().model_dump(), "rationale": "review"},
    )
    assert invalid.status_code == 401
    identity_claim = client.post(
        f"/skill-learning/proposals/{proposal_id}/review",
        json={
            "decision": "accepted",
            "checks": checks().model_dump(),
            "rationale": "review",
            "reviewer_id": "ahmad",
        },
    )
    assert identity_claim.status_code == 422
    accepted = client.post(
        f"/skill-learning/proposals/{proposal_id}/review",
        json={"decision": "accepted", "checks": checks().model_dump(), "rationale": "review"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["review"]["reviewer_id"] == "ahmad"
    assert client.get("/skill-learning/proposals").status_code == 200
    assert client.get(f"/skill-learning/proposals/{proposal_id}").json()["status"] == "accepted"


def test_database_rejects_accepted_review_with_missing_checks(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    with pytest.raises(IntegrityError, match="not authorized or valid"):
        with database.transaction() as session:
            session.execute(
                text(
                    """INSERT INTO skill_learning_reviews
                    (id, proposal_id, reviewer_id, decision, review_fingerprint,
                     review_json, created_at)
                    VALUES ('00000000-0000-4000-8000-000000000099', :proposal_id,
                            'ahmad', 'accepted', :fingerprint, '{}', :created_at)"""
                ),
                {
                    "proposal_id": stored.proposal.proposal_id,
                    "fingerprint": "b" * 64,
                    "created_at": utcnow(),
                },
            )
    assert store.get(stored.proposal.proposal_id).status == "proposed"


def test_review_records_cannot_be_modified_or_removed(database, proposal_case):
    store, agent, _task, _claimed, candidate, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    reviewed, _ = store.record_review(
        stored.proposal.proposal_id, review_request(), OperatorPrincipal()
    )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(text("DELETE FROM skill_learning_reviews"))
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(text("UPDATE skill_learning_reviews SET id = id"))
    assert store.get(reviewed.proposal.proposal_id) == reviewed


def proposal_task(revision_value, evidence_ref):
    return SkillLearningProposalRunInput(
        kind="skill_learning_proposal_run",
        payload={
            "revision": revision_value.model_dump(mode="json"),
            "evidence_refs": [evidence_ref.model_dump(mode="json")],
        },
    )


def learning_task_services(database, settings, store):
    tasks = TaskService(
        database,
        settings,
        agent_factory=store.agent_factory,
        skill_learning_store=store,
    )
    return tasks, AgentExecutionService(database, store.agent_factory, tasks)


def test_agent_task_api_runs_bounded_proposal_through_core_and_is_idempotent(
    client, database, settings, proposal_case
):
    store, agent, _source_task, _source_attempt, candidate, ref = proposal_case
    tasks, execution = learning_task_services(database, settings, store)
    client.app.state.agent_execution = execution
    task_input = proposal_task(candidate, ref)
    headers = {"Idempotency-Key": "skill-learning-proposal:synthetic-e2e"}
    response = client.post(
        f"/agents/{agent.id}/tasks",
        json={"task": task_input.model_dump(mode="json")},
        headers=headers,
    )

    assert response.status_code == 201
    submitted = response.json()
    assert submitted["kind"] == "skill_learning_proposal_run"
    assert submitted["status"] == "pending"
    assert store.list() == ()

    execution = tasks.claim()
    assert execution is not None and execution.task_id == submitted["id"]
    assert execution.agent_execution is not None
    assert execution.agent_execution.agent_instance_id == agent.id
    asyncio.run(Runner(tasks)._run(execution))

    completed = tasks.get(submitted["id"])
    assert completed.status == State.SUCCEEDED
    assert completed.result == {
        "proposal_id": completed.result["proposal_id"],
        "proposal_fingerprint": completed.result["proposal_fingerprint"],
        "status": "proposed",
        "created": True,
    }
    assert "instructions" not in completed.result
    stored = store.get(completed.result["proposal_id"])
    assert stored is not None and stored.status == "proposed"
    assert stored.proposal.proposed_revision == candidate

    replay = client.post(
        f"/agents/{agent.id}/tasks",
        json={"task": task_input.model_dump(mode="json")},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == submitted["id"]
    assert len(store.list()) == 1


def test_learning_task_requires_proposal_policy_and_agent_binding(
    database, settings, proposal_case
):
    _store, _agent, _task, _attempt, candidate, ref = proposal_case
    request = proposal_task(candidate, ref)
    unbound = TaskService(database, settings)
    with pytest.raises(DomainError, match="requires Agent execution"):
        unbound.submit(request, None)

    factory, instances, _tasks, execution = direct_services(database, settings)
    disabled_project = project(database, "Disabled skill learning")
    disabled_agent = instances.create(
        AgentInstanceCreate(agent_id="research", project_id=disabled_project.id)
    )
    with pytest.raises(DomainError, match="skill-learning policy"):
        execution.submit(
            disabled_agent.id,
            AgentTaskCreate(task=request),
            "skill-learning-disabled:denied",
        )

    store, proposing_agent, _task, _attempt, _candidate, _ref = proposal_case
    proposing_execution = AgentExecutionService(
        database,
        store.agent_factory,
        TaskService(database, settings, agent_factory=store.agent_factory),
    )
    with database.read() as session:
        binding = proposing_execution._binding(
            session,
            proposing_agent.id,
            AgentTaskCreate(task=request),
        )
    delegated_binding = {
        **binding.model_dump(mode="json"),
        "delegation": {
            "delegation_id": "10000000-0000-0000-0000-000000000001",
            "parent_instance_id": "10000000-0000-0000-0000-000000000002",
            "child_instance_id": proposing_agent.id,
            "campaign_id": "10000000-0000-0000-0000-000000000003",
            "budget_reservation_id": "10000000-0000-0000-0000-000000000004",
            "idempotency_key": "delegated-proposal",
            "request_hash": "0" * 64,
            "objective": "Create a proposal",
            "allowed_task_kinds": ["skill_learning_proposal_run"],
            "required_capabilities": ["research"],
            "depth": 1,
            "max_depth": 1,
            "budget_resource": "mind_wakes",
            "budget_units": 1,
        },
    }
    with pytest.raises(ValidationError, match="skill_learning_agent_forbidden"):
        AgentExecutionBinding.model_validate(delegated_binding)


def test_paused_agent_revocation_after_claim_prevents_proposal_persistence(
    database, settings, proposal_case
):
    store, agent, _source_task, _source_attempt, candidate, ref = proposal_case
    tasks, execution = learning_task_services(database, settings, store)
    submitted, _ = execution.submit(
        agent.id,
        AgentTaskCreate(task=proposal_task(candidate, ref)),
        "skill-learning-pause:revoked",
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == submitted.id

    instances = AgentInstanceService(database, store.agent_factory, MemoryService(database))
    instances.transition(agent.id, AgentInstanceTransition(action="pause"))
    asyncio.run(Runner(tasks)._run(claimed))

    failed = tasks.get(submitted.id)
    assert failed.status == State.FAILED
    assert failed.error == "skill_learning_agent_stale"
    assert store.list() == ()


def test_evidence_mutation_after_claim_prevents_learning_proposal(
    database, settings, proposal_case
):
    store, agent, source_task, source_attempt, candidate, ref = proposal_case
    tasks, execution = learning_task_services(database, settings, store)
    submitted, _ = execution.submit(
        agent.id,
        AgentTaskCreate(task=proposal_task(candidate, ref)),
        "skill-learning-evidence:revoked",
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == submitted.id

    with database.transaction() as session:
        saved_task = session.get(Task, source_task.id)
        saved_attempt = session.get(TaskAttempt, source_attempt.attempt_id)
        changed = deepcopy(saved_attempt.result)
        changed["answer"] = "Mutated before proposal persistence."
        saved_attempt.result = changed
        saved_task.result = changed
    asyncio.run(Runner(tasks)._run(claimed))

    failed = tasks.get(submitted.id)
    assert failed.status == State.FAILED
    assert failed.error == "skill_learning_evidence_stale"
    assert store.list() == ()


def accepted(store, agent, candidate, ref):
    proposed, _ = store.submit(agent_instance_id=agent.id, revision=candidate, evidence_refs=(ref,))
    reviewed, _ = store.record_review(
        proposed.proposal.proposal_id, review_request(), OperatorPrincipal()
    )
    return reviewed


def test_candidate_is_canonical_content_addressed_and_idempotent(tmp_path, proposal_case):
    store, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(store, agent, revision, ref)
    registry_version = store.skill_registry.version
    adapter = SkillLearningCandidateAdapter(store)

    first = adapter.materialize(
        reviewed.proposal.proposal_id,
        staging_root=tmp_path,
        principal=OperatorPrincipal(),
    )
    second = adapter.materialize(
        reviewed.proposal.proposal_id,
        staging_root=tmp_path,
        principal=OperatorPrincipal(),
    )

    assert first.created is True
    assert second.created is False
    assert (
        first.content == second.content
        and first.relative_path == second.relative_path
        and first.artifact_sha256 == second.artifact_sha256
    )
    assert first.relative_path.startswith("skills/procedures/")
    assert first.relative_path.endswith(f"v{revision.version}-{revision.content_sha256}.json")
    assert hashlib.sha256(first.content).hexdigest() == first.artifact_sha256
    assert json.loads(first.content)["content_sha256"] == first.content_sha256
    assert (tmp_path / first.relative_path).read_bytes() == first.content
    assert store.skill_registry.version == registry_version
    assert store.get(reviewed.proposal.proposal_id) == reviewed


def test_candidate_requires_accepted_review_and_ahmad_operator(tmp_path, proposal_case):
    store, agent, _task, _attempt, revision, ref = proposal_case
    proposed, _ = store.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    adapter = SkillLearningCandidateAdapter(store)

    with pytest.raises(ValueError, match="not_accepted_or_stale"):
        adapter.materialize(
            proposed.proposal.proposal_id,
            staging_root=tmp_path,
            principal=OperatorPrincipal(),
        )
    with pytest.raises(PermissionError, match="reviewer_forbidden"):
        store.accepted_candidate_basis(
            proposed.proposal.proposal_id, OperatorPrincipal(subject="someone_else")
        )
    assert not list(tmp_path.rglob("*.json"))


def test_conflicting_existing_bytes_are_never_overwritten(tmp_path, proposal_case):
    store, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(store, agent, revision, ref)
    adapter = SkillLearningCandidateAdapter(store)
    artifact = adapter.materialize(
        reviewed.proposal.proposal_id,
        staging_root=tmp_path,
        principal=OperatorPrincipal(),
    )
    target = tmp_path / artifact.relative_path
    target.chmod(0o644)
    target.write_bytes(b"modified candidate")
    with pytest.raises(ValueError, match="candidate_conflict"):
        adapter.materialize(
            reviewed.proposal.proposal_id,
            staging_root=tmp_path,
            principal=OperatorPrincipal(),
        )
    assert target.read_bytes() == b"modified candidate"


def test_symlinked_candidate_parent_cannot_escape_staging_root(tmp_path, proposal_case):
    store, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(store, agent, revision, ref)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "skills").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        SkillLearningCandidateAdapter(store).materialize(
            reviewed.proposal.proposal_id,
            staging_root=tmp_path,
            principal=OperatorPrincipal(),
        )
    assert list(outside.iterdir()) == []


def test_stale_evidence_after_acceptance_blocks_candidate(tmp_path, proposal_case, database):
    store, agent, task, attempt, revision, ref = proposal_case
    reviewed = accepted(store, agent, revision, ref)
    with database.transaction() as session:
        stored_task = session.get(Task, task.id)
        stored_attempt = session.get(TaskAttempt, attempt.attempt_id)
        changed = dict(stored_attempt.result)
        changed["answer"] = "Changed after acceptance."
        stored_attempt.result = changed
        stored_task.result = changed

    with pytest.raises(ValueError, match="not_accepted_or_stale"):
        SkillLearningCandidateAdapter(store).materialize(
            reviewed.proposal.proposal_id,
            staging_root=tmp_path,
            principal=OperatorPrincipal(),
        )
    assert not list(tmp_path.rglob("*.json"))
