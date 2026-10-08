"""Inert, identity-bound learned-skill publication intent; never merge authority."""

import pytest
from pydantic import ValidationError

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.memory import MemoryService
from app.schemas import AgentInstanceTransition
from app.skill_learning import (
    skill_procedure_artifact_bytes,
    skill_procedure_artifact_path,
    skill_procedure_artifact_sha256,
)
from app.skill_learning_store import SkillLearningProposalStore
from app.skill_publication_intent import (
    SkillPublicationIntent,
    prepare_skill_publication_intent,
)
from tests import test_skill_learning_promotion as promotion_tests
from tests import test_skill_learning_store as skill_learning_store_tests
from tests.test_skill_learning import revision as make_revision
from tests.test_skill_learning_store import accepted

OPERATOR = OperatorPrincipal()


@pytest.fixture(name="proposal_case")
def proposal_case_fixture(database, settings):
    return skill_learning_store_tests.proposal_case.__wrapped__(database, settings)


def accepted_case(database, proposal_case):
    base, agent, _task, _attempt, candidate, ref = proposal_case
    reviewed = accepted(base, agent, candidate, ref)
    configured = promotion_tests.promotion_store(database, base)
    return configured, reviewed, agent, candidate, ref


def test_v1_intent_binds_live_accepted_review_and_artifact_without_authority(
    database, proposal_case
):
    store, reviewed, agent, revision, _ref = accepted_case(database, proposal_case)

    intent = prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)
    repeated = prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)

    assert intent == repeated
    assert isinstance(intent, SkillPublicationIntent)
    assert intent.schema_version == "zelo-skill-publication-intent-v1"
    assert intent.proposal_id == reviewed.proposal.proposal_id
    assert intent.proposal_fingerprint == reviewed.proposal.proposal_fingerprint
    assert intent.review_fingerprint == reviewed.review.review_fingerprint
    assert intent.agent_instance_id == agent.id
    assert intent.source_skill_id == revision.source_skill_id
    assert intent.procedure_id == revision.procedure_id
    assert intent.proposed_version == 1
    assert intent.revision_sha256 == revision.content_sha256
    assert intent.prior_revision_sha256 is None
    assert intent.expected_promoted_link_fingerprint is None
    assert intent.artifact_path == skill_procedure_artifact_path(revision)
    assert intent.artifact_sha256 == skill_procedure_artifact_sha256(revision)
    assert intent.artifact_bytes == len(skill_procedure_artifact_bytes(revision))
    assert intent.repository == promotion_tests.REPOSITORY
    assert len(intent.intent_fingerprint) == 64
    assert intent.publication_authority is False
    assert intent.tool_authority is False
    assert intent.runtime_authority is False
    assert store.get_promotion_link(intent.proposal_id) is None


def test_preparing_intent_does_not_reserve_exclusive_head(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    initial = accepted(base, agent, revision, ref)
    competing_revision = make_revision(
        version=1, title="Second separately accepted candidate", space_permissions=()
    )
    competing = accepted(base, agent, competing_revision, ref)
    store = promotion_tests.promotion_store(database, base)

    first = prepare_skill_publication_intent(store, initial.proposal.proposal_id, OPERATOR)
    second = prepare_skill_publication_intent(store, competing.proposal.proposal_id, OPERATOR)

    assert first.procedure_id == second.procedure_id
    assert first.agent_instance_id == second.agent_instance_id
    assert first.revision_sha256 != second.revision_sha256
    assert first.intent_fingerprint != second.intent_fingerprint
    assert not first.publication_authority and not second.publication_authority


def test_not_accepted_or_missing_proposals_are_refused(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    stored, _ = base.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    store = promotion_tests.promotion_store(database, base)
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        prepare_skill_publication_intent(store, stored.proposal.proposal_id, OPERATOR)
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        prepare_skill_publication_intent(store, "00000000-0000-4000-8000-000000000001", OPERATOR)


def test_repository_unconfigured_and_wrong_operator_fail_before_preview(database, proposal_case):
    configured, reviewed, _agent, _revision, _ref = accepted_case(database, proposal_case)
    with pytest.raises(PermissionError, match="operator_required"):
        prepare_skill_publication_intent(configured, reviewed.proposal.proposal_id, "ahmad")
    unconfigured = SkillLearningProposalStore(
        database,
        configured.agent_factory,
        configured.skill_registry,
        promotion_repository=None,
    )
    with pytest.raises(ValueError, match="repository_unconfigured"):
        prepare_skill_publication_intent(unconfigured, reviewed.proposal.proposal_id, OPERATOR)


def test_already_linked_proposal_is_not_reissued_as_new_intent(database, proposal_case):
    store, reviewed, _agent, revision, _ref = accepted_case(database, proposal_case)
    proposal_id = reviewed.proposal.proposal_id
    assert prepare_skill_publication_intent(store, proposal_id, OPERATOR)
    merged = promotion_tests.successful_lineage(database, revision)
    link, created = store.record_promotion_link(proposal_id, merged, OPERATOR)
    assert created and not link.runtime_authority
    with pytest.raises(ValueError, match="already_promoted"):
        prepare_skill_publication_intent(store, proposal_id, OPERATOR)


def test_preaccepted_competing_v1_becomes_stale_after_first_promotion(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    first = accepted(base, agent, revision, ref)
    second_revision = make_revision(version=1, title="Conflicting candidate", space_permissions=())
    second = accepted(base, agent, second_revision, ref)
    store = promotion_tests.promotion_store(database, base)
    assert prepare_skill_publication_intent(store, second.proposal.proposal_id, OPERATOR)
    merge_task_id = promotion_tests.successful_lineage(database, revision)
    store.record_promotion_link(first.proposal.proposal_id, merge_task_id, OPERATOR)
    with pytest.raises(ValueError, match="promoted_head_advanced"):
        prepare_skill_publication_intent(store, second.proposal.proposal_id, OPERATOR)


def test_v2_intent_binds_exact_promoted_predecessor(database, proposal_case):
    store, reviewed, agent, version_one, ref = accepted_case(database, proposal_case)
    merge_task_id = promotion_tests.successful_lineage(database, version_one)
    first_link, _ = store.record_promotion_link(
        reviewed.proposal.proposal_id, merge_task_id, OPERATOR
    )
    second_revision = make_revision(
        version=2,
        prior_revision_sha256=version_one.content_sha256,
        space_permissions=(),
    )
    second = accepted(store, agent, second_revision, ref)

    intent = prepare_skill_publication_intent(store, second.proposal.proposal_id, OPERATOR)

    assert intent.proposed_version == 2
    assert intent.prior_revision_sha256 == first_link.revision_sha256
    assert intent.expected_promoted_link_fingerprint == first_link.link_fingerprint
    assert intent.artifact_sha256 == skill_procedure_artifact_sha256(second_revision)
    assert not intent.publication_authority


def test_paused_agent_invalidates_fresh_accepted_intent(database, proposal_case):
    store, reviewed, agent, _revision, _ref = accepted_case(database, proposal_case)
    assert prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)
    instances = AgentInstanceService(database, store.agent_factory, MemoryService(database))
    instances.transition(agent.id, AgentInstanceTransition(action="pause"))

    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)


@pytest.mark.parametrize(
    "change",
    [
        {"artifact_sha256": "f" * 64},
        {"artifact_path": "skills/procedures/unrelated/v1-other.json"},
        {"repository": "evil\\repo/repo"},
        {"repository": "evil/repo/another"},
        {"review_fingerprint": "f" * 64},
        {"repository": "malicious/repo"},
        {"proposal_fingerprint": "f" * 64},
        {"revision_sha256": "f" * 64},
        {"intent_fingerprint": "f" * 64},
        {"publication_authority": True},
        {"runtime_authority": True},
        {"tool_authority": True},
        {"proposed_version": 2},
        {"expected_promoted_link_fingerprint": "f" * 64},
    ],
)
def test_contract_refuses_tampered_frozen_metadata(database, proposal_case, change):
    store, reviewed, _agent, _revision, _ref = accepted_case(database, proposal_case)
    intent = prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)
    with pytest.raises(ValidationError):
        SkillPublicationIntent.model_validate({**intent.model_dump(mode="json"), **change})


def test_contract_is_frozen(database, proposal_case):
    store, reviewed, _agent, _revision, _ref = accepted_case(database, proposal_case)
    intent = prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)
    with pytest.raises(ValidationError):
        intent.publication_authority = True
