"""Opt-in single-merge boundary: all external calls are simulated.

No live GitHub request, new Agent tool, native Media proof or real Skill
runtime activation happens in this test suite.
"""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.developer_contracts import ValidationResult
from app.developer_process import DeveloperError
from app.memory import MemoryService
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
from app.schemas import AgentInstanceTransition
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
    transport.responses.append(
        (
            "GET",
            f"/repos/{request.publication.repository}/branches/main/protection",
            {
                "required_status_checks": {
                    "strict": True,
                    "checks": [{"context": "checks", "app_id": 15368}],
                },
                "enforce_admins": {"enabled": True},
                "allow_force_pushes": {"enabled": False},
                "allow_deletions": {"enabled": False},
                "required_pull_request_reviews": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews": True,
                    "require_last_push_approval": True,
                    "bypass_pull_request_allowances": {
                        "users": [],
                        "teams": [],
                        "apps": [],
                    },
                },
            },
        )
    )
    template = deepcopy(transport.responses)
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
    assert len(transport.calls) == 22
    assert transport.responses == []
    assert store.get_promotion_link(claim.proposal_id) is None

    before = list(transport.calls)
    with pytest.raises(ValueError, match="already_recorded"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.calls == before
    assert _rows(database) == (1, 1)


def test_personal_repository_null_review_bypass_is_accepted(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    reviews = transport.responses[12][2]["required_pull_request_reviews"]
    reviews["bypass_pull_request_allowances"] = None
    _add_success_response(request, transport, template)
    receipt = asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert isinstance(receipt, SkillPublicationMergeReceipt)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1


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
    transport.responses[15][2]["parents"][0]["sha"] = "e" * 40
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


def test_agent_pause_after_attempt_commit_refuses_merge_send(database, proposal_case, tmp_path):
    store, claim, request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    original_transaction = database.transaction
    paused = False
    instances = AgentInstanceService(database, store.agent_factory, MemoryService(database))

    @contextmanager
    def pause_after_attempt_commit():
        nonlocal paused
        with original_transaction() as session:
            yield session
        if not paused and _rows(database)[0] == 1:
            paused = True
            instances.transition(claim.agent_instance_id, AgentInstanceTransition(action="pause"))

    database.transaction = pause_after_attempt_commit
    try:
        with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
            asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    finally:
        database.transaction = original_transaction

    assert paused
    assert _rows(database) == (1, 0)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 0
    instances.transition(claim.agent_instance_id, AgentInstanceTransition(action="resume"))
    with pytest.raises(ValueError, match="already_recorded"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (1, 0)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 0


def test_parallel_dispatch_requests_cannot_issue_two_external_puts(
    database, proposal_case, tmp_path
):
    _store, claim, request, _transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    from tests import test_promotion_merge as merge_tests

    class ConcurrentProvider:
        """Async provider preserves true overlap across external GET boundaries."""

        def __init__(self):
            self.calls = []
            self.put_count = 0
            self.merge_sha = "f" * 40
            self.base = f"/repos/{request.publication.repository}"
            self.pr = f"{self.base}/pulls/{request.publication.pr_number}"
            self.responses = {
                endpoint: deepcopy(response)
                for method, endpoint, response in template
                if method == "GET"
            }
            self.responses[f"{self.base}/git/commits/{self.merge_sha}"] = {
                "sha": self.merge_sha,
                "parents": [{"sha": request.publication.pr_base_sha}],
                "tree": {"sha": "a" * 40},
            }

        async def request(self, method, endpoint, body=None):
            await asyncio.sleep(0)
            self.calls.append((method, endpoint, body))
            if method == "PUT":
                assert endpoint == self.pr + "/merge"
                self.put_count += 1
                assert self.put_count == 1, "Second external merge forbidden"
                assert body["sha"] == request.publication.pr_head_sha
                assert body["merge_method"] == "squash"
                return {"merged": True, "sha": self.merge_sha}
            assert method == "GET" and body is None
            if endpoint == self.pr and self.put_count:
                return merge_tests.merged_pr(request, self.merge_sha)
            return deepcopy(self.responses[endpoint])

    provider = ConcurrentProvider()
    # The exact same provider is used for fresh CI, source/blob and dispatch.
    service.fences.blob.transport = provider
    service.fences.blob.remote.transport = provider
    service.fences.blob.remote.ci_verifier.transport = provider
    service.transport = provider

    async def compete():
        return await asyncio.gather(
            service.run_once(claim.id, request, CONFIRM, OPERATOR),
            service.run_once(claim.id, request, CONFIRM, OPERATOR),
            return_exceptions=True,
        )

    results = asyncio.run(compete())
    success = [item for item in results if isinstance(item, SkillPublicationMergeReceipt)]
    rejected = [item for item in results if isinstance(item, Exception)]
    assert len(success) == 1 and len(rejected) == 1
    assert provider.put_count == 1
    assert sum(method == "PUT" for method, _, _ in provider.calls) == 1
    assert _rows(database) == (1, 1)
    assert _failures(database) == 0
    assert service.get_receipt(claim.id, OPERATOR) == success[0]


@pytest.mark.parametrize(
    "mutation",
    (
        "strict_false",
        "wrong_app",
        "missing_check",
        "duplicate_check",
        "admins_bypass",
        "force_pushes",
        "deletions",
        "explicit_bypass",
        "review_policy_absent",
        "review_policy_null",
        "review_policy_malformed",
        "approval_count_missing",
        "approval_count_null",
        "zero_required_approvals",
        "negative_required_approvals",
        "too_many_required_approvals",
        "noninteger_required_approvals",
        "string_required_approvals",
        "stale_reviews_allowed",
        "stale_reviews_missing",
        "stale_reviews_type_confusion",
        "last_push_approval_optional",
        "last_push_approval_missing",
        "last_push_approval_type_confusion",
        "review_bypass_missing",
        "review_bypass_users_missing",
        "review_bypass_teams",
        "review_bypass_apps",
        "missing_policy",
        "empty_required_checks",
    ),
)
def test_unsafe_main_branch_protection_refuses_before_external_merge(
    database, proposal_case, tmp_path, mutation
):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    row = transport.responses[12]
    assert row[0] == "GET" and row[1].endswith("/branches/main/protection")
    policy = row[2]
    if mutation == "strict_false":
        policy["required_status_checks"]["strict"] = False
    elif mutation == "wrong_app":
        policy["required_status_checks"]["checks"][0]["app_id"] = -1
    elif mutation == "missing_check":
        policy["required_status_checks"]["checks"] = []
    elif mutation == "duplicate_check":
        policy["required_status_checks"]["checks"] *= 2
    elif mutation == "admins_bypass":
        policy["enforce_admins"]["enabled"] = False
    elif mutation == "force_pushes":
        policy["allow_force_pushes"]["enabled"] = True
    elif mutation == "deletions":
        policy["allow_deletions"]["enabled"] = True
    elif mutation == "explicit_bypass":
        policy["required_pull_request_reviews"]["bypass_pull_request_allowances"]["users"] = [
            {"login": "any"}
        ]
    elif mutation == "review_policy_absent":
        del policy["required_pull_request_reviews"]
    elif mutation == "review_policy_null":
        policy["required_pull_request_reviews"] = None
    elif mutation == "review_policy_malformed":
        policy["required_pull_request_reviews"] = []
    elif mutation == "approval_count_missing":
        del policy["required_pull_request_reviews"]["required_approving_review_count"]
    elif mutation == "approval_count_null":
        policy["required_pull_request_reviews"]["required_approving_review_count"] = None
    elif mutation == "zero_required_approvals":
        policy["required_pull_request_reviews"]["required_approving_review_count"] = 0
    elif mutation == "negative_required_approvals":
        policy["required_pull_request_reviews"]["required_approving_review_count"] = -1
    elif mutation == "too_many_required_approvals":
        policy["required_pull_request_reviews"]["required_approving_review_count"] = 7
    elif mutation == "noninteger_required_approvals":
        policy["required_pull_request_reviews"]["required_approving_review_count"] = True
    elif mutation == "string_required_approvals":
        policy["required_pull_request_reviews"]["required_approving_review_count"] = "1"
    elif mutation == "stale_reviews_allowed":
        policy["required_pull_request_reviews"]["dismiss_stale_reviews"] = False
    elif mutation == "stale_reviews_missing":
        del policy["required_pull_request_reviews"]["dismiss_stale_reviews"]
    elif mutation == "stale_reviews_type_confusion":
        policy["required_pull_request_reviews"]["dismiss_stale_reviews"] = 1
    elif mutation == "last_push_approval_optional":
        policy["required_pull_request_reviews"]["require_last_push_approval"] = False
    elif mutation == "last_push_approval_missing":
        del policy["required_pull_request_reviews"]["require_last_push_approval"]
    elif mutation == "last_push_approval_type_confusion":
        policy["required_pull_request_reviews"]["require_last_push_approval"] = 1
    elif mutation == "review_bypass_missing":
        del policy["required_pull_request_reviews"]["bypass_pull_request_allowances"]
    elif mutation == "review_bypass_null":
        policy["required_pull_request_reviews"]["bypass_pull_request_allowances"] = None
    elif mutation == "review_bypass_users_missing":
        del policy["required_pull_request_reviews"]["bypass_pull_request_allowances"]["users"]
    elif mutation == "review_bypass_teams":
        policy["required_pull_request_reviews"]["bypass_pull_request_allowances"]["teams"] = [
            {"slug": "bypass"}
        ]
    elif mutation == "review_bypass_apps":
        policy["required_pull_request_reviews"]["bypass_pull_request_allowances"]["apps"] = [
            {"slug": "bypass"}
        ]
    elif mutation == "missing_policy":
        transport.responses[12] = (row[0], row[1], {})
    else:
        service.settings = service.settings.model_copy(update={"promotion_required_checks": ()})
    with pytest.raises(DeveloperError, match="skill_publication_main_protection_unqualified"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (0, 0)
    assert _failures(database) == 0
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 0


def test_github_main_protection_api_unavailable_denies_all_publication(
    database, proposal_case, tmp_path
):
    _store, claim, request, transport, _fence, service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    original_request = transport.request

    async def protection_access_refused(method, endpoint, body=None):
        if method == "GET" and endpoint.endswith("/branches/main/protection"):
            raise DeveloperError("promotion_github_api_failed")
        return await original_request(method, endpoint, body)

    transport.request = protection_access_refused
    with pytest.raises(DeveloperError, match="skill_publication_main_protection_unavailable"):
        asyncio.run(service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert _rows(database) == (0, 0)
    assert _failures(database) == 0
    assert all(method == "GET" for method, _, _ in transport.calls)
