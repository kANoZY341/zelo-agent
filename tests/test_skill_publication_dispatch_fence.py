"""Core-exclusive pre-dispatch review fence; never sends a GitHub merge."""

import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.memory import MemoryService
from app.models import SkillPublicationDispatchFenceRecord
from app.schemas import AgentInstanceTransition
from app.skill_publication_dispatch_fence import (
    SkillPublicationDispatchFence,
    SkillPublicationDispatchFenceService,
)
from app.skill_publication_operator_decision import SkillPublicationOperatorDecisionService
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_blob_evidence import _arrange

OPERATOR = OperatorPrincipal()
DECISION_CONFIRM = "APPROVE_EXACT_SKILL_PUBLICATION_REVIEW"
FENCE_CONFIRM = "APPROVE_EXACT_SKILL_PUBLICATION_FENCE_NO_SEND"


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _setup(database, proposal_case, tmp_path):
    store, claim, request, transport, blob, indexes = _arrange(database, proposal_case, tmp_path)
    original = deepcopy(transport.responses)
    decision, created = asyncio.run(
        SkillPublicationOperatorDecisionService(blob.remote).record_approval(
            claim.id, request, DECISION_CONFIRM, OPERATOR
        )
    )
    assert created
    assert len(transport.calls) == 3
    assert all(method == "GET" for method, _, _ in transport.calls)

    transport.responses = deepcopy(original)
    transport.calls = []
    service = SkillPublicationDispatchFenceService(blob)
    return store, claim, decision, request, transport, service, indexes


def _count(database):
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(SkillPublicationDispatchFenceRecord))


def _rewind(transport, responses):
    transport.responses = deepcopy(responses)
    transport.calls = []


def test_exact_owner_selected_fence_is_durable_inert_and_idempotent(
    database, proposal_case, tmp_path
):
    store, claim, decision, request, transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    before = _count(database)
    original = deepcopy(transport.responses)
    fence, created = asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    assert created
    assert isinstance(fence, SkillPublicationDispatchFence)
    assert fence.decision_id == decision.id
    assert fence.claim_id == claim.id
    assert fence.pr_head_sha == request.publication.pr_head_sha
    assert fence.pr_base_sha == request.publication.pr_base_sha
    assert fence.decision_fingerprint == decision.decision_fingerprint
    assert fence.phase == "reserved_no_send"
    assert fence.network_dispatch_authorized is False
    assert fence.network_dispatch_started is False
    assert fence.runtime_authority is False
    assert len(transport.calls) == 12
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert store.get_promotion_link(claim.proposal_id) is None
    assert service.get(claim.id, OPERATOR) == fence
    assert _count(database) == before + 1

    _rewind(transport, original)
    duplicate, created = asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    assert not created and duplicate == fence
    assert _count(database) == before + 1
    assert len(transport.calls) == 12
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_incorrect_operator_or_confirmation_denied_before_any_external_get(
    database, proposal_case, tmp_path
):
    _store, claim, _decision, request, transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    with pytest.raises(ValueError, match="explicit_confirmation_required"):
        asyncio.run(service.reserve(claim.id, request, "yes", OPERATOR))
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, "ahmad"))
    with pytest.raises(PermissionError, match="operator_required"):
        service.get(claim.id, "ahmad")
    assert transport.calls == [] and _count(database) == 0


def test_changed_request_cannot_reuse_exact_operator_selection(database, proposal_case, tmp_path):
    _store, claim, _decision, request, transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    changed = request.model_copy(
        update={"merge_commit_message": "Unreviewed alternate merge action"}
    )
    with pytest.raises(ValueError, match="approval_mismatch"):
        asyncio.run(service.reserve(claim.id, changed, FENCE_CONFIRM, OPERATOR))
    assert len(transport.calls) == 12
    assert _count(database) == 0


def test_stale_agent_after_approval_denied_before_fetch_and_no_fence(
    database, settings, proposal_case, tmp_path
):
    store, claim, _decision, request, transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    from app.models import AgentInstance

    with database.read() as session:
        instance = session.get(AgentInstance, claim.agent_instance_id)
        assert instance is not None
    AgentInstanceService(database, store.agent_factory, MemoryService(database)).transition(
        claim.agent_instance_id, AgentInstanceTransition(action="pause")
    )
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    assert _count(database) == 0
    assert transport.calls == []


def test_remote_head_change_after_approval_denied_and_no_fence(database, proposal_case, tmp_path):
    _store, claim, _decision, request, transport, service, idx = _setup(
        database, proposal_case, tmp_path
    )
    transport.responses[idx["final"]][2]["head"]["sha"] = "f" * 40
    with pytest.raises(DeveloperError, match="skill_publication_remote_pr_changed"):
        asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    assert _count(database) == 0
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_absent_operator_decision_refuses_before_provider_or_new_database_write(
    database, proposal_case, tmp_path
):
    _store, claim, decision, request, transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    # A claim with no matching current decision must never reach the GitHub
    # blob verifier. Foreign claim belongs to nobody in this test.
    with pytest.raises(ValueError, match="operator_decision_missing"):
        asyncio.run(
            service.reserve(
                "00000000-0000-4000-8000-000000000002",
                request,
                FENCE_CONFIRM,
                OPERATOR,
            )
        )
    assert transport.calls == []
    assert _count(database) == 0


def test_receipt_cannot_forge_network_dispatch_or_swap_approved_action(
    database, proposal_case, tmp_path
):
    _store, claim, _decision, request, _transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    fence, _ = asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    original = fence.model_dump(mode="python")
    # A faithful strict Python snapshot MUST validate; otherwise every
    # forged-value test can pass for an unrelated datetime parsing error.
    assert SkillPublicationDispatchFence.model_validate(original) == fence
    for mutation in (
        {"network_dispatch_authorized": True},
        {"network_dispatch_started": True},
        {"runtime_authority": True},
        {"phase": "sent"},
        {"operator_subject": "another"},
        {"fence_fingerprint": "f" * 64},
        {"merge_request_sha256": "f" * 64},
        {"git_blob_oid": "f" * 40},
    ):
        with pytest.raises(ValidationError):
            SkillPublicationDispatchFence.model_validate({**original, **mutation})


def test_sqlite_dispatch_fence_immutable_under_update_delete_or_replace(
    database, proposal_case, tmp_path
):
    _store, claim, _decision, request, _transport, service, _idx = _setup(
        database, proposal_case, tmp_path
    )
    receipt, _ = asyncio.run(service.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE skill_publication_dispatch_fences SET phase='sent' WHERE id=:id"),
                {"id": receipt.id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_publication_dispatch_fences WHERE id=:id"),
                {"id": receipt.id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO skill_publication_dispatch_fences "
                    "SELECT * FROM skill_publication_dispatch_fences WHERE id=:id"
                ),
                {"id": receipt.id},
            )
    assert _count(database) == 1
    assert service.get(claim.id, OPERATOR) == receipt
