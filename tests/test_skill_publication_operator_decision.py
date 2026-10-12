"""Immutable owner decision for a reviewed Skill PR; no GitHub write authority."""

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.memory import MemoryService
from app.models import SkillPublicationOperatorDecisionRecord
from app.schemas import AgentInstanceTransition
from app.skill_publication_operator_decision import (
    SkillPublicationOperatorDecision,
    SkillPublicationOperatorDecisionService,
)
from tests import test_promotion_ci as ci_tests
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_remote_evidence import _remote_fixture

OPERATOR = OperatorPrincipal()
CONFIRM = "APPROVE_EXACT_SKILL_PUBLICATION_REVIEW"


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _count(database):
    with database.read() as session:
        return session.scalar(
            select(func.count()).select_from(SkillPublicationOperatorDecisionRecord)
        )


def _reseat(transport, request):
    repo = request.publication.repository
    first = merge_tests.open_pr(request)
    final = merge_tests.open_pr(request)
    final["draft"] = False
    transport.responses = [
        ("GET", f"/repos/{repo}/pulls/{request.publication.pr_number}", first),
        (
            "GET",
            f"/repos/{repo}/commits/{request.publication.pr_head_sha}/check-runs?filter=latest&per_page=100",
            {"total_count": 1, "check_runs": [ci_tests.check()]},
        ),
        ("GET", f"/repos/{repo}/pulls/{request.publication.pr_number}", final),
    ]
    transport.calls = []


def test_owner_exact_pr_decision_is_durable_and_idempotent_without_github_put(
    database, proposal_case, tmp_path
):
    store, agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationOperatorDecisionService(remote)
    before = _count(database)
    decision, created = asyncio.run(service.record_approval(claim.id, request, CONFIRM, OPERATOR))
    assert created
    assert isinstance(decision, SkillPublicationOperatorDecision)
    assert decision.claim_id == claim.id
    assert decision.claim_fingerprint == claim.claim_fingerprint
    assert decision.pr_number == request.publication.pr_number
    assert decision.pr_head_sha == request.publication.pr_head_sha
    assert decision.decision == "approved_for_review"
    assert decision.publication_authority is False
    assert decision.external_merge_authority is False
    assert decision.runtime_authority is False
    assert service.get(claim.id, OPERATOR) == decision
    assert _count(database) == before + 1
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert len(transport.calls) == 3
    assert store.get_promotion_link(claim.proposal_id) is None

    _reseat(transport, request)
    replay, created = asyncio.run(service.record_approval(claim.id, request, CONFIRM, OPERATOR))
    assert not created
    assert replay == decision
    assert _count(database) == before + 1
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_operator_and_explicit_confirmation_needed_before_network_or_database(
    database, proposal_case, tmp_path
):
    _store, _agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationOperatorDecisionService(remote)
    before = _count(database)
    with pytest.raises(ValueError, match="explicit_confirmation_required"):
        asyncio.run(service.record_approval(claim.id, request, "yes", OPERATOR))
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.record_approval(claim.id, request, CONFIRM, "ahmad"))
    with pytest.raises(PermissionError, match="operator_required"):
        service.get(claim.id, "ahmad")
    assert transport.calls == [] and _count(database) == before


def test_exact_claim_replay_with_changed_merge_body_is_conflict(database, proposal_case, tmp_path):
    _store, _agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationOperatorDecisionService(remote)
    first, _ = asyncio.run(service.record_approval(claim.id, request, CONFIRM, OPERATOR))
    changed = request.model_copy(update={"merge_commit_message": "Unapproved changed content"})
    _reseat(transport, changed)
    with pytest.raises(ValueError, match="skill_publication_operator_decision_conflict"):
        asyncio.run(service.record_approval(claim.id, changed, CONFIRM, OPERATOR))
    assert _count(database) == 1
    assert service.get(claim.id, OPERATOR) == first


def test_approval_response_cannot_claim_runtime_or_merge_authority(
    database, proposal_case, tmp_path
):
    _store, _agent, claim, request, _transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationOperatorDecisionService(remote)
    result, _ = asyncio.run(service.record_approval(claim.id, request, CONFIRM, OPERATOR))
    for forged in (
        {"publication_authority": True},
        {"external_merge_authority": True},
        {"runtime_authority": True},
        {"operator_subject": "other"},
        {"decision_fingerprint": "f" * 64},
        {"merge_request_sha256": "f" * 64},
    ):
        with pytest.raises(ValidationError):
            SkillPublicationOperatorDecision.model_validate(
                {**result.model_dump(mode="json"), **forged}
            )


def test_persisted_decision_is_append_only_even_replace(database, proposal_case, tmp_path):
    _store, _agent, claim, request, _transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationOperatorDecisionService(remote)
    receipt, _ = asyncio.run(service.record_approval(claim.id, request, CONFIRM, OPERATOR))
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE skill_publication_operator_decisions SET decision='other' WHERE id=:id"
                ),
                {"id": receipt.id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_publication_operator_decisions WHERE id=:id"),
                {"id": receipt.id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO skill_publication_operator_decisions "
                    "SELECT * FROM skill_publication_operator_decisions WHERE id=:id"
                ),
                {"id": receipt.id},
            )
    assert service.get(claim.id, OPERATOR) == receipt
    assert _count(database) == 1


def test_pause_after_remote_verification_before_commit_refuses_decision(
    database, settings, proposal_case, tmp_path
):
    store, agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    original_verify = remote.verify

    async def verification_then_revoke(claim_id, merge_request, principal):
        observation = await original_verify(claim_id, merge_request, principal)
        AgentInstanceService(database, store.agent_factory, MemoryService(database)).transition(
            agent.id, AgentInstanceTransition(action="pause")
        )
        return observation

    remote.verify = verification_then_revoke
    service = SkillPublicationOperatorDecisionService(remote)
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        asyncio.run(service.record_approval(claim.id, request, CONFIRM, OPERATOR))
    assert _count(database) == 0
    assert service.get(claim.id, OPERATOR) is None
    assert len(transport.calls) == 3
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_malformed_nested_merge_request_denied_before_network(database, proposal_case, tmp_path):
    _store, _agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationOperatorDecisionService(remote)
    invalid = request.model_copy(
        update={"ci": request.ci.model_copy(update={"head_sha": "invalid"})}
    )
    with pytest.raises(ValueError, match="decision_request_invalid"):
        asyncio.run(service.record_approval(claim.id, invalid, CONFIRM, OPERATOR))
    assert transport.calls == []
    assert _count(database) == 0
