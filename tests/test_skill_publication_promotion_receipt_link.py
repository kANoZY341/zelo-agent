"""Local publication receipt link: simulated provider reads, no new PUT or activation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.models import SkillPublicationPromotionReceiptLinkRecord
from app.skill_publication_promotion_receipt_link import (
    SkillPublicationPromotionReceiptLinkService,
)
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_one_attempt import (
    CONFIRM,
    _add_success_response,
    _setup_one,
)

OPERATOR = OperatorPrincipal()


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _count(database):
    with database.read() as session:
        return session.scalar(
            select(func.count()).select_from(SkillPublicationPromotionReceiptLinkRecord)
        )


def _case(database, proposal_case, tmp_path):
    store, claim, request, transport, fence, attempt_service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    receipt = asyncio.run(attempt_service.run_once(claim.id, request, CONFIRM, OPERATOR))
    assert transport.responses == []
    transport.calls = []
    repo = f"/repos/{fence.repository}"
    merge_sha = receipt.merge_commit_sha
    commit = {
        "sha": merge_sha,
        "parents": [{"sha": fence.pr_base_sha}],
        "tree": {"sha": "a" * 40},
    }
    responses = [
        ("GET", repo + f"/pulls/{fence.pr_number}", merge_tests.merged_pr(request, merge_sha)),
        ("GET", repo + f"/git/commits/{merge_sha}", deepcopy(commit)),
        ("GET", repo + f"/git/commits/{merge_sha}", deepcopy(commit)),
        *deepcopy(template[5:10]),
        ("GET", repo + "/branches/main", {"name": "main", "commit": {"sha": merge_sha}}),
    ]
    transport.responses = deepcopy(responses)
    return store, claim, request, transport, attempt_service, receipt, responses


def test_positive_link_serialized_idempotent_inert(database, proposal_case, tmp_path):
    store, claim, request, transport, attempt_service, receipt, responses = _case(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationPromotionReceiptLinkService(attempt_service)
    link, created = asyncio.run(service.record(claim.id, request, OPERATOR))
    assert created and link.receipt_id == receipt.id
    assert link.pr_head_sha == request.publication.pr_head_sha
    assert link.runtime_authority is False
    assert _count(database) == 1
    assert service.get(claim.id, OPERATOR) == link
    assert store.get_promotion_link(claim.proposal_id) is None
    assert store.resolve_promoted_revision(claim.proposal_id) is None
    assert all(method == "GET" for method, _, _ in transport.calls)
    transport.calls = []
    transport.responses = deepcopy(responses)
    again, created = asyncio.run(service.record(claim.id, request, OPERATOR))
    assert not created and again == link and _count(database) == 1


@pytest.mark.parametrize(
    "mutation",
    ("head", "head_repo", "base", "unmerged", "merge", "parent", "blob", "main"),
)
def test_provider_drift_denies_link(database, proposal_case, tmp_path, mutation):
    _store, claim, request, transport, attempt_service, _receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    if mutation == "head":
        transport.responses[0][2]["head"]["sha"] = "d" * 40
    elif mutation == "head_repo":
        transport.responses[0][2]["head"]["repo"]["full_name"] = "other/repository"
    elif mutation == "base":
        transport.responses[0][2]["base"]["ref"] = "other"
    elif mutation == "unmerged":
        transport.responses[0][2]["merged"] = False
    elif mutation == "merge":
        transport.responses[0][2]["merge_commit_sha"] = "d" * 40
    elif mutation == "parent":
        transport.responses[1][2]["parents"][0]["sha"] = "d" * 40
    elif mutation == "blob":
        transport.responses[7][2]["tree"][0]["sha"] = "d" * 40
    else:
        transport.responses[8][2]["commit"]["sha"] = "d" * 40
    with pytest.raises(DeveloperError, match="(provider_changed|blob_tree)"):
        asyncio.run(
            SkillPublicationPromotionReceiptLinkService(attempt_service).record(
                claim.id, request, OPERATOR
            )
        )
    assert _count(database) == 0
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_uncertain_attempt_or_history_only_cannot_link(database, proposal_case, tmp_path):
    _store, claim, request, transport, _fence, attempt_service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationPromotionReceiptLinkService(attempt_service)
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.record(claim.id, request, "ahmad"))
    # No affirmative receipt: neither a claim nor a history-only GET observation
    # can satisfy the link. Simulate an uncertain merge PUT.
    endpoint = (
        f"/repos/{request.publication.repository}/pulls/{request.publication.pr_number}/merge"
    )
    transport.responses.append(("PUT", endpoint, TimeoutError()))
    original = transport.request

    async def timeout_on_put(method, path, body=None):
        if method == "PUT":
            transport.calls.append((method, path, body))
            expected_method, expected_path, error = transport.responses.pop(0)
            assert (method, path) == (expected_method, expected_path)
            raise error
        return await original(method, path, body)

    transport.request = timeout_on_put
    with pytest.raises(DeveloperError, match="outcome_unresolved"):
        asyncio.run(attempt_service.run_once(claim.id, request, CONFIRM, OPERATOR))
    transport.calls = []
    with pytest.raises(ValueError, match="positive_receipt_missing"):
        asyncio.run(service.record(claim.id, request, OPERATOR))
    assert transport.calls == [] and _count(database) == 0


def test_local_revalidation_fails_after_cancellation(database, proposal_case, tmp_path):
    _store, claim, request, transport, attempt_service, _receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    from app.models import Task

    with database.transaction() as session:
        task = session.get(Task, request.promotion_task_id)
        task.cancel_requested = True
    with pytest.raises(ValueError, match="source_task_invalid"):
        asyncio.run(
            SkillPublicationPromotionReceiptLinkService(attempt_service).record(
                claim.id, request, OPERATOR
            )
        )
    assert transport.calls == [] and _count(database) == 0


def test_local_state_changed_after_provider_get_is_denied(database, proposal_case, tmp_path):
    _store, claim, request, transport, attempt_service, _receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    from app.models import Task

    service = SkillPublicationPromotionReceiptLinkService(attempt_service)
    original = service._provider

    async def provider_then_cancel(req, fence, receipt, artifact_path):
        await original(req, fence, receipt, artifact_path)
        with database.transaction() as session:
            session.get(Task, request.promotion_task_id).cancel_requested = True

    service._provider = provider_then_cancel
    with pytest.raises(ValueError, match="source_task_invalid"):
        asyncio.run(service.record(claim.id, request, OPERATOR))
    assert _count(database) == 0
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_competing_promotion_after_provider_get_denies_local_link(
    database, proposal_case, tmp_path
):
    store, claim, request, transport, attempt_service, _receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    from tests.test_skill_learning_promotion import successful_lineage

    service = SkillPublicationPromotionReceiptLinkService(attempt_service)
    original = service._provider

    async def provider_then_competing_promotion(req, fence, receipt, artifact_path):
        await original(req, fence, receipt, artifact_path)
        revision = store.get(claim.proposal_id).proposal.proposed_revision
        merge_task_id = successful_lineage(database, revision)
        store.record_promotion_link(claim.proposal_id, merge_task_id, OPERATOR)

    service._provider = provider_then_competing_promotion
    with pytest.raises(ValueError, match="already_promoted"):
        asyncio.run(service.record(claim.id, request, OPERATOR))
    assert _count(database) == 0
    assert store.get_promotion_link(claim.proposal_id) is not None
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_generic_lineage_cannot_insert_after_receipt_link(database, proposal_case, tmp_path):
    store, claim, request, _transport, attempt_service, _receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    asyncio.run(
        SkillPublicationPromotionReceiptLinkService(attempt_service).record(
            claim.id, request, OPERATOR
        )
    )
    from tests.test_skill_learning_promotion import successful_lineage

    revision = store.get(claim.proposal_id).proposal.proposed_revision
    merge_task_id = successful_lineage(database, revision)
    with pytest.raises(IntegrityError, match="lineage conflict"):
        store.record_promotion_link(claim.proposal_id, merge_task_id, OPERATOR)
    assert store.get_promotion_link(claim.proposal_id) is None
    assert _count(database) == 1


@pytest.mark.parametrize("mutation", ("claim", "receipt", "fence", "failure", "task_cancel"))
def test_raw_link_insert_rejects_unqualified_source(database, proposal_case, tmp_path, mutation):
    _store, claim, request, _transport, attempt_service, receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    from app.models import SkillPublicationMergeFailureRecord, Task, new_id
    from app.skill_publication_intent import _digest
    from app.skill_publication_promotion_receipt_link import _material

    service = SkillPublicationPromotionReceiptLinkService(attempt_service)
    with database.read() as session:
        intent, fence, _ = service._local(session, claim.id, request, OPERATOR)
    values = {
        "claim_id": claim.id,
        "proposal_id": intent.proposal_id,
        "receipt_id": receipt.id,
        "agent_instance_id": intent.agent_instance_id,
        "source_skill_id": intent.source_skill_id,
        "procedure_id": intent.procedure_id,
        "proposed_version": intent.proposed_version,
        "intent_fingerprint": intent.intent_fingerprint,
        "receipt_fingerprint": receipt.receipt_fingerprint,
        "repository": fence.repository,
        "pr_number": fence.pr_number,
        "pr_head_sha": fence.pr_head_sha,
        "merge_commit_sha": receipt.merge_commit_sha,
        "revision_sha256": intent.revision_sha256,
        "operator_subject": "ahmad",
        "runtime_authority": False,
    }
    if mutation == "claim":
        values["claim_id"] = new_id()
    elif mutation == "receipt":
        values["receipt_fingerprint"] = "e" * 64
    elif mutation == "fence":
        values["pr_number"] += 1
    elif mutation == "failure":
        with database.transaction() as session:
            material = {
                "schema": "zelo-skill-publication-merge-failure-v1",
                "attempt_id": receipt.attempt_id,
                "claim_id": claim.id,
                "phase": "post_tree",
                "reason": "unclassified",
                "operator_subject": "ahmad",
            }
            session.add(
                SkillPublicationMergeFailureRecord(
                    id=new_id(),
                    attempt_id=receipt.attempt_id,
                    claim_id=claim.id,
                    phase="post_tree",
                    reason="unclassified",
                    failure_fingerprint=_digest(material),
                    operator_subject="ahmad",
                    automatic_retry_allowed=False,
                    runtime_authority=False,
                )
            )
    else:
        with database.transaction() as session:
            session.get(Task, request.promotion_task_id).cancel_requested = True
    with pytest.raises(IntegrityError, match="receipt lineage invalid"):
        with database.transaction() as session:
            session.add(
                SkillPublicationPromotionReceiptLinkRecord(
                    id=new_id(),
                    **values,
                    link_fingerprint=_digest(_material(values)),
                )
            )
            session.flush()
    assert _count(database) == 0


def test_link_append_only(database, proposal_case, tmp_path):
    _store, claim, request, _transport, attempt_service, _receipt, _ = _case(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationPromotionReceiptLinkService(attempt_service)
    link, _ = asyncio.run(service.record(claim.id, request, OPERATOR))
    for statement in (
        "UPDATE skill_publication_promotion_receipt_links SET revision_sha256=:sha WHERE id=:id",
        "DELETE FROM skill_publication_promotion_receipt_links WHERE id=:id",
        "INSERT OR REPLACE INTO skill_publication_promotion_receipt_links SELECT * "
        "FROM skill_publication_promotion_receipt_links WHERE id=:id",
    ):
        with pytest.raises(IntegrityError, match="immutable|append-only"):
            with database.transaction() as session:
                session.execute(text(statement), {"id": link.id, "sha": "e" * 64})
    assert service.get(claim.id, OPERATOR) == link


def test_parallel_local_link_writers_only_one_insert(database, proposal_case, tmp_path):
    _store, claim, request, transport, attempt_service, _receipt, responses = _case(
        database, proposal_case, tmp_path
    )
    service = SkillPublicationPromotionReceiptLinkService(attempt_service)

    # Both external read sequences are independent; only Core's local writer
    # transaction is raced here after provider evidence is already checked.
    async def verified(_request, _fence, _receipt, _path):
        return None

    service._provider = verified
    transport.calls = []
    transport.responses = deepcopy(responses)

    def run():
        return asyncio.run(service.record(claim.id, request, OPERATOR))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert _count(database) == 1
