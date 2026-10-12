"""Opt-in single-merge boundary: all external calls are simulated.

No live GitHub request, new Agent tool, native Media proof or real Skill
runtime activation happens in this test suite.
"""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorPrincipal
from app.developer_contracts import ValidationResult
from app.developer_process import DeveloperError
from app.models import (
    Approval,
    SkillPublicationMergeAttemptRecord,
    SkillPublicationMergeFailureRecord,
    SkillPublicationMergeReceiptRecord,
    Task,
    TaskAttempt,
)
from app.policy import State
from app.promotion_contracts import PromotionCommitResult, PromotionRunResult
from app.skill_publication_one_attempt import (
    SkillPublicationMergeAttempt,
    SkillPublicationMergeFailure,
    SkillPublicationMergeReceipt,
    SkillPublicationOneAttemptService,
)
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_dispatch_fence import FENCE_CONFIRM, _setup

OPERATOR = OperatorPrincipal()
CONFIRM = "MERGE_EXACT_APPROVED_SKILL_ONCE_WITHOUT_RETRY"


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _approved_source(database, request):
    approved = [
        ValidationResult(
            identifier=name,
            exit_code=0,
            output="",
            duration_seconds=0,
            completed_at=datetime(2026, 10, 12, tzinfo=UTC),
        )
        for name in ("ruff_check", "ruff_format", "pytest", "git_diff_check")
    ]
    commit = PromotionCommitResult(
        base_sha=request.promotion.review.base_sha,
        tree_sha="6" * 40,
        commit_sha=request.publication.pr_head_sha,
        branch=request.promotion.branch,
        review_fingerprint=request.promotion.review.review_fingerprint,
        validation=approved,
    )
    result = PromotionRunResult(commit=commit, publication=request.publication)
    value = result.model_dump(mode="json")
    with database.transaction() as session:
        session.add(
            Task(
                id=request.promotion_task_id,
                kind="promotion_run",
                payload={"synthetic": "approved exact test source"},
                timeout_seconds=300.0,
                status=State.SUCCEEDED,
                action_version=request.promotion_action_version,
                action_hash=request.promotion_action_hash,
                request_hash="a" * 64,
                promotion_spec=request.promotion.model_dump(mode="json"),
                result=value,
                cancel_requested=False,
            )
        )
        session.flush()
        session.add(
            TaskAttempt(
                task_id=request.promotion_task_id,
                number=1,
                outcome=State.SUCCEEDED,
                result=value,
                ended_at=datetime.now(UTC),
            )
        )
        session.add(
            Approval(
                task_id=request.promotion_task_id,
                action_version=request.promotion_action_version,
                action_hash=request.promotion_action_hash,
                status="approved",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                decided_at=datetime.now(UTC),
            )
        )


def _setup_one(database, proposal_case, tmp_path, *, approved_source=True):
    store, claim, _decision, request, transport, fences, _indexes = _setup(
        database, proposal_case, tmp_path
    )
    template = deepcopy(transport.responses)
    fence, created = asyncio.run(fences.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    assert created
    assert len(transport.calls) == 12
    if approved_source:
        _approved_source(database, request)
    transport.responses = deepcopy(template)
    transport.calls = []
    return (
        store,
        claim,
        request,
        transport,
        fence,
        SkillPublicationOneAttemptService(
            fences,
            fences.blob.remote.settings.model_copy(
                update={"skill_publication_external_merge_enabled": True}
            ),
            transport=transport,
        ),
        template,
    )


def _add_success_response(request, transport, template, *, merge_sha="f" * 40):
    base = f"/repos/{request.publication.repository}"
    pr = merge_tests.merged_pr(request, merge_sha)
    commit = {
        "sha": merge_sha,
        "parents": [{"sha": request.publication.pr_base_sha}],
        "tree": {"sha": "a" * 40},
    }
    transport.responses.extend(
        [
            (
                "PUT",
                base + f"/pulls/{request.publication.pr_number}/merge",
                {"merged": True, "sha": merge_sha},
            ),
            ("GET", base + f"/pulls/{request.publication.pr_number}", pr),
            ("GET", base + f"/git/commits/{merge_sha}", commit),
            ("GET", base + f"/git/commits/{merge_sha}", deepcopy(commit)),
            *deepcopy(template[5:10]),
        ]
    )


def _rows(database):
    with database.read() as session:
        return (
            session.scalar(select(func.count()).select_from(SkillPublicationMergeAttemptRecord)),
            session.scalar(select(func.count()).select_from(SkillPublicationMergeReceiptRecord)),
        )


def _failures(database):
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(SkillPublicationMergeFailureRecord))


def test_disabled_by_default_even_with_full_approval(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    service.settings = service.settings.model_copy(
        update={"skill_publication_external_merge_enabled": False}
    )
    with pytest.raises(PermissionError, match="single_attempt_disabled"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.calls == []
    assert _rows(database) == (0, 0)


def test_requires_operator_confirmed_real_promotion_task_and_approval(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path, approved_source=False
    )
    for operator, confirmation, error in (
        ("ahmad", CONFIRM, "operator_required"),
        (OPERATOR, "please merge", "confirmation_required"),
    ):
        with pytest.raises((ValueError, PermissionError), match=error):
            asyncio.run(service.run_once(claim.id, request, confirmation, operator))
    with pytest.raises(ValueError, match="source_task_invalid"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.calls == [] and _rows(database) == (0, 0)


def test_single_permitted_put_has_durable_pre_send_attempt_and_verified_receipt(
    database, proposal_case, tmp_path
):
    store, claim, request, transport, fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    receipt = asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert isinstance(receipt, SkillPublicationMergeReceipt)
    assert receipt.merge_commit_sha == "f" * 40
    assert receipt.runtime_authority is False
    attempt = service.get_attempt(claim.id, OPERATOR)
    assert isinstance(attempt, SkillPublicationMergeAttempt)
    assert attempt.fence_id == fence.id
    assert attempt.phase == "send_outcome_unresolved"
    assert attempt.send_may_have_occurred is True
    assert attempt.automatic_retry_allowed is False
    assert attempt.runtime_authority is False
    assert receipt.attempt_id == attempt.id
    assert service.get_receipt(claim.id, OPERATOR) == receipt
    assert _rows(database) == (1, 1)
    assert _failures(database) == 0
    assert service.get_failure(claim.id, OPERATOR) is None
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1
    assert len(transport.calls) == 21
    assert transport.responses == []
    assert store.get_promotion_link(claim.proposal_id) is None

    before = list(transport.calls)
    with pytest.raises(ValueError, match="already_recorded"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.calls == before
    assert _rows(database) == (1, 1)


def test_uncertain_put_timeout_preserves_attempt_and_blocks_blind_retry(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    base = f"/repos/{request.publication.repository}"
    transport.responses.append(
        ("PUT", base + f"/pulls/{request.publication.pr_number}/merge", TimeoutError())
    )
    original_request = transport.request

    async def timeout_on_put(method, endpoint, body=None):
        if method == "PUT":
            transport.calls.append((method, endpoint, body))
            _method, _endpoint, response = transport.responses.pop(0)
            assert method == _method and endpoint == _endpoint
            raise response
        return await original_request(method, endpoint, body)

    transport.request = timeout_on_put
    with pytest.raises(DeveloperError, match="outcome_unresolved"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (1, 0)
    attempt = service.get_attempt(claim.id, OPERATOR)
    assert attempt.fence_id == fence.id
    assert attempt.automatic_retry_allowed is False
    failure = service.get_failure(claim.id, OPERATOR)
    assert isinstance(failure, SkillPublicationMergeFailure)
    assert failure.attempt_id == attempt.id
    assert failure.phase == "put_request"
    assert failure.reason == "provider_exception"
    assert failure.automatic_retry_allowed is False
    assert _failures(database) == 1
    put_count = sum(method == "PUT" for method, _, _ in transport.calls)
    assert put_count == 1
    before = list(transport.calls)
    with pytest.raises(ValueError, match="already_recorded"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.calls == before
    assert _rows(database) == (1, 0)


def test_missing_source_approval_cannot_enter_sender(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    with database.transaction() as session:
        row = session.scalar(select(Approval).where(Approval.task_id == request.promotion_task_id))
        row.status = "invalidated"
    with pytest.raises(ValueError, match="source_approval_invalid"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (0, 0)
    assert transport.calls == []


def test_provider_head_change_refuses_before_durable_attempt(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    transport.responses[11][2]["head"]["sha"] = "e" * 40
    with pytest.raises(DeveloperError, match="remote_pr_changed"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (0, 0)
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_attempt_row_is_never_replaced_or_deleted(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    base = f"/repos/{request.publication.repository}"
    transport.responses.append(
        ("PUT", base + f"/pulls/{request.publication.pr_number}/merge", {"merged": False})
    )
    with pytest.raises(DeveloperError, match="outcome_unresolved"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    attempt = service.get_attempt(claim.id, OPERATOR)
    assert _rows(database) == (1, 0)
    failure = service.get_failure(claim.id, OPERATOR)
    assert failure.phase == "put_response"
    assert failure.reason == "response_invalid"
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE skill_publication_merge_attempts SET phase='done' WHERE id=:id"),
                {"id": attempt.id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_publication_merge_attempts WHERE id=:id"),
                {"id": attempt.id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO skill_publication_merge_attempts "
                    "SELECT * FROM skill_publication_merge_attempts WHERE id=:id"
                ),
                {"id": attempt.id},
            )
    assert service.get_attempt(claim.id, OPERATOR) == attempt


def test_transport_cannot_change_after_provider_evidence_binding(database, proposal_case, tmp_path):
    _store, claim, request, transport, fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    from tests.test_promotion_merge import ScriptedTransport

    other_transport = ScriptedTransport([])
    with pytest.raises(ValueError, match="single_attempt_transport_mismatch"):
        SkillPublicationOneAttemptService(
            service.fences, service.settings, transport=other_transport
        )
    assert transport.calls == []
    assert _rows(database) == (0, 0)


def test_post_put_changed_base_commit_is_unresolved_and_never_retried(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    # The provider may accept the exact head but merge against a different
    # base in the race between GET and PUT. Never record success or retry.
    transport.responses[14][2]["parents"][0]["sha"] = "e" * 40
    with pytest.raises(DeveloperError, match="single_attempt_outcome_unresolved"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (1, 0)
    failure = service.get_failure(claim.id, OPERATOR)
    assert failure.phase == "post_commit"
    assert failure.reason == "parent_mismatch"
    assert _failures(database) == 1
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1
    before = list(transport.calls)
    with pytest.raises(ValueError, match="single_attempt_already_recorded"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.calls == before


def test_merged_receipt_immutable_even_under_replace_attempts(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    receipt = asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE skill_publication_merge_receipts SET merge_commit_sha=:sha WHERE id=:id"
                ),
                {"sha": "e" * 40, "id": receipt.id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_publication_merge_receipts WHERE id=:id"),
                {"id": receipt.id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO skill_publication_merge_receipts "
                    "SELECT * FROM skill_publication_merge_receipts WHERE id=:id"
                ),
                {"id": receipt.id},
            )
    assert service.get_receipt(claim.id, OPERATOR) == receipt
    assert _rows(database) == (1, 1)


def test_terminal_failure_evidence_is_immutable_and_never_unlocks_reexecution(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    endpoint = (
        f"/repos/{request.publication.repository}/pulls/{request.publication.pr_number}/merge"
    )
    transport.responses.append(("PUT", endpoint, {"merged": False}))
    with pytest.raises(DeveloperError, match="outcome_unresolved_put_response"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    failure = service.get_failure(claim.id, OPERATOR)
    assert failure.reason == "response_invalid" and failure.phase == "put_response"
    assert failure.automatic_retry_allowed is False
    assert failure.runtime_authority is False
    assert _failures(database) == 1
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE skill_publication_merge_failures SET reason='unclassified' WHERE id=:id"
                ),
                {"id": failure.id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_publication_merge_failures WHERE id=:id"),
                {"id": failure.id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO skill_publication_merge_failures "
                    "SELECT * FROM skill_publication_merge_failures WHERE id=:id"
                ),
                {"id": failure.id},
            )
    assert service.get_failure(claim.id, OPERATOR) == failure
    with pytest.raises(ValueError, match="already_recorded"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1


def test_post_merge_provider_failure_is_not_falsely_labeled_as_policy_mismatch(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    original = transport.request

    async def fail_post_pr(method, endpoint, body=None):
        if method == "GET" and any(call[0] == "PUT" for call in transport.calls):
            transport.calls.append((method, endpoint, body))
            raise DeveloperError("promotion_github_api_failed")
        return await original(method, endpoint, body)

    transport.request = fail_post_pr
    with pytest.raises(DeveloperError, match="outcome_unresolved_post_pr"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    incident = service.get_failure(claim.id, OPERATOR)
    assert incident.phase == "post_pr"
    assert incident.reason == "provider_exception"
    assert incident.automatic_retry_allowed is False
    assert _rows(database) == (1, 0)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1
