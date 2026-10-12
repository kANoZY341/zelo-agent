"""Exclusive local learned-skill publication claims: no external publish authority."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.memory import MemoryService
from app.models import SkillPublicationClaimRecord
from app.promotion_merge import PromotionMergeRuntime
from app.schemas import AgentInstanceTransition
from app.skill_publication_claim import SkillPublicationClaim, SkillPublicationClaimService
from app.skill_publication_intent import prepare_skill_publication_intent
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_promotion as promotion_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_learning import revision as make_revision
from tests.test_skill_learning_store import accepted
from tests.test_skill_publication_intent import accepted_case

OPERATOR = OperatorPrincipal()


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _scope(database, proposal_case):
    store, reviewed, agent, rev, reference = accepted_case(database, proposal_case)
    service = SkillPublicationClaimService(store)
    intent = prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)
    return store, reviewed, agent, rev, reference, service, intent


def _rows(database):
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(SkillPublicationClaimRecord))


def test_owner_claim_serialized_durable_and_exact_retry_is_inert(database, proposal_case):
    store, reviewed, agent, revision, _ref, service, intent = _scope(database, proposal_case)
    before = _rows(database)
    claim, created = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert created is True
    assert isinstance(claim, SkillPublicationClaim)
    assert claim.id and claim.agent_instance_id == agent.id
    assert claim.review_fingerprint == reviewed.review.review_fingerprint
    assert claim.artifact_sha256 == intent.artifact_sha256
    assert claim.repository == store.promotion_repository
    assert claim.proposed_version == revision.version == 1
    assert claim.publication_authority is False
    assert claim.runtime_authority is False
    assert store.get_promotion_link(intent.proposal_id) is None
    assert _rows(database) == before + 1
    replay, created = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert not created and replay == claim
    assert service.get(claim.id, OPERATOR) == claim
    assert _rows(database) == before + 1


def test_competing_accepted_same_slot_cannot_claim_second(database, proposal_case):
    store, first, agent, revision, reference, service, intent = _scope(database, proposal_case)
    competing_revision = make_revision(
        version=1, title="Competing accepted candidate", space_permissions=()
    )
    competing = accepted(store, agent, competing_revision, reference)
    competing_intent = prepare_skill_publication_intent(
        store, competing.proposal.proposal_id, OPERATOR
    )
    assert intent.intent_fingerprint != competing_intent.intent_fingerprint
    first_claim, created = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert created
    with pytest.raises(ValueError, match="scope_already_reserved"):
        service.reserve(competing_intent.proposal_id, competing_intent.intent_fingerprint, OPERATOR)
    assert service.get(first_claim.id, OPERATOR) == first_claim
    assert _rows(database) == 1


def test_wrong_operator_or_stale_fingerprint_cannot_reserve(database, proposal_case):
    _store, _reviewed, _agent, _rev, _ref, service, intent = _scope(database, proposal_case)
    before = _rows(database)
    with pytest.raises(PermissionError, match="operator_required"):
        service.reserve(intent.proposal_id, intent.intent_fingerprint, "ahmad")
    with pytest.raises(ValueError, match="intent_stale_or_mismatched"):
        service.reserve(intent.proposal_id, "f" * 64, OPERATOR)
    with pytest.raises(PermissionError, match="operator_required"):
        service.get("00000000-0000-4000-8000-000000000001", "ahmad")
    assert _rows(database) == before


def test_paused_agent_refuses_reclaim_but_historical_claim_is_read_only(
    database, settings, proposal_case
):
    store, _reviewed, agent, _rev, _ref, service, intent = _scope(database, proposal_case)
    claim, _ = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    instances = AgentInstanceService(database, store.agent_factory, MemoryService(database))
    instances.transition(agent.id, AgentInstanceTransition(action="pause"))
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert service.get(claim.id, OPERATOR) == claim
    assert _rows(database) == 1


def test_claim_is_immutable_under_update_delete_or_insert_replace(database, proposal_case):
    _store, _reviewed, _agent, _rev, _ref, service, intent = _scope(database, proposal_case)
    claim, _ = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE skill_publication_claims SET source_skill_id='hijacked' WHERE id=:id"),
                {"id": claim.id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_publication_claims WHERE id=:id"),
                {"id": claim.id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO skill_publication_claims "
                    "(id, proposal_id, agent_instance_id, source_skill_id, procedure_id, "
                    "proposed_version, repository, review_fingerprint, intent_fingerprint, "
                    "claim_fingerprint, artifact_sha256, operator_subject, "
                    "publication_authority, runtime_authority, created_at) "
                    "SELECT id, proposal_id, agent_instance_id, source_skill_id, procedure_id, "
                    "proposed_version, repository, review_fingerprint, intent_fingerprint, "
                    "claim_fingerprint, artifact_sha256, operator_subject, "
                    "publication_authority, runtime_authority, created_at "
                    "FROM skill_publication_claims WHERE id=:id"
                ),
                {"id": claim.id},
            )
    assert service.get(claim.id, OPERATOR) == claim
    assert _rows(database) == 1


def test_concurrent_competing_claims_only_one_scope_winner(database, proposal_case):
    store, _first, agent, _rev, ref, service, first_intent = _scope(database, proposal_case)
    second = accepted(
        store,
        agent,
        make_revision(version=1, title="Alternative title", space_permissions=()),
        ref,
    )
    second_intent = prepare_skill_publication_intent(store, second.proposal.proposal_id, OPERATOR)

    def try_claim(intent):
        try:
            claim, created = service.reserve(
                intent.proposal_id, intent.intent_fingerprint, OPERATOR
            )
            return ("won", claim.proposal_id, created)
        except ValueError as error:
            return ("denied", str(error), False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(try_claim, (first_intent, second_intent)))
    assert sorted(outcome[0] for outcome in results) == ["denied", "won"]
    assert "scope_already_reserved" in next(x[1] for x in results if x[0] == "denied")
    assert _rows(database) == 1


def test_next_version_can_claim_only_after_real_predecessor_promotion(database, proposal_case):
    store, first, agent, version_one, ref, service, intent = _scope(database, proposal_case)
    service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    promoted_task_id = promotion_tests.successful_lineage(database, version_one)
    promoted, created = store.record_promotion_link(
        first.proposal.proposal_id, promoted_task_id, OPERATOR
    )
    assert created
    second_version = make_revision(
        version=2,
        prior_revision_sha256=version_one.content_sha256,
        space_permissions=(),
    )
    second = accepted(store, agent, second_version, ref)
    second_intent = prepare_skill_publication_intent(store, second.proposal.proposal_id, OPERATOR)
    assert second_intent.expected_promoted_link_fingerprint == promoted.link_fingerprint
    second_claim, created = service.reserve(
        second_intent.proposal_id, second_intent.intent_fingerprint, OPERATOR
    )
    assert created and second_claim.proposed_version == 2
    assert _rows(database) == 2


def test_forged_approval_flag_refused_at_contract_and_database(database, proposal_case):
    _store, _reviewed, _agent, _rev, _ref, service, intent = _scope(database, proposal_case)
    claim, _ = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    with pytest.raises(ValidationError):
        SkillPublicationClaim.model_validate(
            {**claim.model_dump(mode="json"), "publication_authority": True}
        )
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO skill_publication_claims "
                    "(id,proposal_id,agent_instance_id,source_skill_id,procedure_id,"
                    "proposed_version,repository,review_fingerprint,intent_fingerprint,"
                    "claim_fingerprint,artifact_sha256,operator_subject,"
                    "publication_authority,runtime_authority,created_at) "
                    "SELECT '00000000-0000-4000-8000-000000000123',proposal_id,"
                    "agent_instance_id,source_skill_id,procedure_id,proposed_version+1,"
                    "repository,review_fingerprint,intent_fingerprint,"
                    "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',"
                    "artifact_sha256,operator_subject,1,0,created_at "
                    "FROM skill_publication_claims WHERE id=:id"
                ),
                {"id": claim.id},
            )
    assert _rows(database) == 1


def test_valid_local_claim_does_not_bypass_generic_github_publication_refusal(
    database, proposal_case, tmp_path
):
    _store, _reviewed, _agent, _revision, _ref, service, intent = _scope(database, proposal_case)
    claim, created = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert created and claim.publication_authority is False

    settings, generic, ci = merge_tests.fixture(tmp_path)
    protected_file = generic.promotion.review.candidate_manifest[0].model_copy(
        update={
            "path": intent.artifact_path,
            "operation": "add",
            "object_type": "file",
            "mode": "100644",
            "size": intent.artifact_bytes,
            "sha256": intent.artifact_sha256,
        }
    )
    review = generic.promotion.review.model_copy(update={"candidate_manifest": [protected_file]})
    request = generic.model_copy(
        update={"promotion": generic.promotion.model_copy(update={"review": review})}
    )
    endpoint = f"/repos/{request.promotion.repository}/pulls/{request.publication.pr_number}"
    transport = merge_tests.ScriptedTransport([("GET", endpoint, merge_tests.open_pr(request))])
    verifier = merge_tests.FakeCIVerifier(ci)
    with pytest.raises(DeveloperError, match="promotion_merge_skill_publication_unqualified"):
        asyncio.run(PromotionMergeRuntime(settings, verifier, transport).run(request))
    assert transport.calls == [("GET", endpoint, None)]
    assert verifier.calls == []
    assert _rows(database) == 1


def test_parallel_same_proposal_reservations_return_one_immutable_claim(database, proposal_case):
    _store, _reviewed, _agent, _rev, _ref, service, intent = _scope(database, proposal_case)

    def try_claim(_):
        return service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(try_claim, range(2)))
    assert sorted(created for _claim, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert _rows(database) == 1


def test_claim_receipt_fingerprint_cannot_be_rewritten_or_forged(database, proposal_case):
    _store, _reviewed, _agent, _revision, _ref, service, intent = _scope(database, proposal_case)
    claim, created = service.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert created
    snapshot = claim.model_dump(mode="json")
    for altered in (
        {"claim_fingerprint": "f" * 64},
        {"intent_fingerprint": "f" * 64},
        {"proposal_id": "00000000-0000-4000-8000-000000000001"},
        {"operator_subject": "anonymous"},
        {"publication_authority": True},
    ):
        with pytest.raises(ValidationError):
            SkillPublicationClaim.model_validate({**snapshot, **altered})
    assert service.get(claim.id, OPERATOR) == claim
    assert _rows(database) == 1
