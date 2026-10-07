"""Read-only historical promoted-revision resolution with no activation authority."""

import pytest
from pydantic import ValidationError

from app.auth import OperatorPrincipal
from app.models import AgentInstance
from app.skill_learning import SkillLearningPromotedRevisionSnapshot
from app.skill_learning_store import SkillLearningProposalStore
from tests import test_skill_learning_promotion as promotion_tests
from tests import test_skill_learning_store as skill_learning_store_tests
from tests.test_skill_learning import revision as make_revision
from tests.test_skill_learning_store import accepted


@pytest.fixture(name="proposal_case")
def proposal_case_fixture(database, settings):
    return skill_learning_store_tests.proposal_case.__wrapped__(database, settings)


def promoted_case(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_tests.promotion_store(database, base)
    merge_task_id = promotion_tests.successful_lineage(database, revision)
    link, created = store.record_promotion_link(
        reviewed.proposal.proposal_id,
        merge_task_id,
        OperatorPrincipal(),
    )
    assert created is True
    return store, reviewed, link, revision, agent


def test_resolver_returns_exact_promoted_revision_without_runtime_authority(
    database, proposal_case
):
    store, reviewed, link, revision, agent = promoted_case(database, proposal_case)
    registry_version = store.skill_registry.version

    resolved = store.resolve_promoted_revision(reviewed.proposal.proposal_id)

    assert resolved is not None
    assert resolved.proposal_id == reviewed.proposal.proposal_id
    assert resolved.proposal_fingerprint == reviewed.proposal.proposal_fingerprint
    assert resolved.review_fingerprint == reviewed.review.review_fingerprint
    assert resolved.reviewer_id == "ahmad"
    assert resolved.promotion_link_id == link.link_id
    assert resolved.promotion_link_fingerprint == link.link_fingerprint
    assert resolved.agent_instance_id == agent.id
    assert resolved.source_skill_id == revision.source_skill_id
    assert resolved.source_skill_version == revision.source_skill_version
    assert resolved.procedure_id == revision.procedure_id
    assert resolved.revision_sha256 == revision.content_sha256
    assert resolved.revision == revision
    assert resolved.repository == promotion_tests.REPOSITORY
    assert resolved.artifact_path == link.artifact_path
    assert resolved.artifact_sha256 == link.artifact_sha256
    assert resolved.artifact_bytes == link.artifact_bytes
    assert resolved.merge_commit_sha == link.merge_commit_sha
    assert resolved.promoted_at == link.linked_at
    assert resolved.runtime_authority is False
    assert store.skill_registry.version == registry_version


def test_resolver_returns_none_without_promotion_link(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_tests.promotion_store(database, base)

    assert store.resolve_promoted_revision(reviewed.proposal.proposal_id) is None
    assert store.resolve_promoted_revision("00000000-0000-4000-8000-000000000001") is None


@pytest.mark.parametrize("repository", (None, "other/repo"))
def test_resolver_fails_closed_when_promotion_repository_trust_drifts(
    database, proposal_case, repository
):
    _store, reviewed, _link, _revision, _agent = promoted_case(database, proposal_case)
    base = proposal_case[0]
    drifted = SkillLearningProposalStore(
        database,
        base.agent_factory,
        base.skill_registry,
        promotion_repository=repository,
    )

    with pytest.raises(ValueError, match="promoted_revision_repository_stale"):
        drifted.resolve_promoted_revision(reviewed.proposal.proposal_id)


def test_resolver_is_historical_when_agent_is_later_paused(database, proposal_case):
    store, reviewed, link, revision, agent = promoted_case(database, proposal_case)
    with database.transaction() as session:
        session.get(AgentInstance, agent.id).status = "paused"

    resolved = store.resolve_promoted_revision(reviewed.proposal.proposal_id)

    assert resolved is not None
    assert resolved.revision == revision
    assert resolved.promotion_link_id == link.link_id
    assert resolved.runtime_authority is False


def test_procedure_resolver_returns_unique_latest_promoted_revision(database, proposal_case):
    store, reviewed, link, revision, agent = promoted_case(database, proposal_case)

    resolved = store.resolve_promoted_procedure(
        agent.id,
        revision.source_skill_id,
        revision.procedure_id,
    )

    assert resolved is not None
    assert resolved.proposal_id == reviewed.proposal.proposal_id
    assert resolved.promotion_link_id == link.link_id
    assert resolved.revision == revision
    assert resolved.runtime_authority is False
    assert (
        store.resolve_promoted_procedure(
            "00000000-0000-4000-8000-000000000001",
            revision.source_skill_id,
            revision.procedure_id,
        )
        is None
    )


def test_procedure_resolver_refuses_two_promoted_heads_at_same_version(database, proposal_case):
    base, agent, _task, _attempt, first_revision, ref = proposal_case
    first_reviewed = accepted(base, agent, first_revision, ref)
    store = promotion_tests.promotion_store(database, base)
    first_merge = promotion_tests.successful_lineage(database, first_revision)
    store.record_promotion_link(
        first_reviewed.proposal.proposal_id,
        first_merge,
        OperatorPrincipal(),
    )

    second_revision = make_revision(
        title="Alternate promoted evidence retrieval",
        instructions=(
            "Gather only authorized evidence, retain exact identity, and use the alternate "
            "reviewed sequence."
        ),
        space_permissions=(),
    )
    second_reviewed = accepted(base, agent, second_revision, ref)
    second_merge = promotion_tests.successful_lineage(database, second_revision)
    store.record_promotion_link(
        second_reviewed.proposal.proposal_id,
        second_merge,
        OperatorPrincipal(),
    )

    with pytest.raises(ValueError, match="promoted_revision_ambiguous"):
        store.resolve_promoted_procedure(
            agent.id,
            first_revision.source_skill_id,
            first_revision.procedure_id,
        )


def test_resolver_is_not_wired_into_new_proposal_lineage(database, proposal_case):
    store, _reviewed, _link, first_revision, agent = promoted_case(database, proposal_case)
    ref = proposal_case[5]
    second_revision = make_revision(
        version=2,
        prior_revision_sha256=first_revision.content_sha256,
        title="Second reviewed evidence retrieval revision",
        instructions=(
            "Gather only authorized evidence and retain exact source identity using the "
            "second reviewed revision."
        ),
        space_permissions=(),
    )

    assert (
        store.resolve_promoted_procedure(
            agent.id,
            first_revision.source_skill_id,
            first_revision.procedure_id,
        )
        is not None
    )
    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        store.submit(
            agent_instance_id=agent.id,
            revision=second_revision,
            evidence_refs=(ref,),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("revision_sha256", "0" * 64),
        ("artifact_sha256", "1" * 64),
        ("artifact_bytes", 1),
        ("source_skill_id", "other_skill"),
        ("procedure_id", "other_procedure"),
        ("runtime_authority", True),
    ),
)
def test_promoted_revision_snapshot_rejects_identity_or_authority_drift(
    database, proposal_case, field, value
):
    store, reviewed, _link, _revision, _agent = promoted_case(database, proposal_case)
    resolved = store.resolve_promoted_revision(reviewed.proposal.proposal_id)
    assert resolved is not None
    payload = resolved.model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError):
        SkillLearningPromotedRevisionSnapshot.model_validate(payload)
