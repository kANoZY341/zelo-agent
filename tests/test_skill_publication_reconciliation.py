"""Read-only reconciliation of a previously fenced publication: no blind retry."""

import asyncio
from copy import deepcopy

import pytest

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.memory import MemoryService
from app.schemas import AgentInstanceTransition
from app.skill_publication_reconciliation import (
    SkillPublicationReconciliationEvidence,
    SkillPublicationReconciliationService,
)
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_dispatch_fence import FENCE_CONFIRM, _setup

OPERATOR = OperatorPrincipal()


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _prepared(database, proposal_case, tmp_path):
    store, claim, decision, request, transport, fences, _index = _setup(
        database, proposal_case, tmp_path
    )
    fence, created = asyncio.run(fences.reserve(claim.id, request, FENCE_CONFIRM, OPERATOR))
    assert created
    assert len(transport.calls) == 12
    assert all(method == "GET" for method, _, _ in transport.calls)
    transport.calls = []
    transport.responses = []
    return (
        store,
        claim,
        decision,
        request,
        transport,
        fence,
        SkillPublicationReconciliationService(fences, transport=transport),
    )


def _response(request, *, state="open", merged=False, pr=None):
    original = merge_tests.open_pr(request)
    original["state"] = state
    original["merged"] = merged
    original["draft"] = False
    if pr is not None:
        original.update(pr)
    return original


def _prepare_merged_pr(request, fence, transport, *, parent=None):
    merged_sha = "9" * 40
    root_oids = [char * 40 for char in ("a", "b", "c", "d", "e")]
    path = request.promotion.review.candidate_manifest[0].path
    segments = path.split("/")
    assert len(segments) == 5
    repository = request.publication.repository
    base = f"/repos/{repository}"
    pr = _response(
        request,
        state="closed",
        merged=True,
        pr={"merge_commit_sha": merged_sha},
    )
    first_commit = {
        "sha": merged_sha,
        "parents": [{"sha": fence.pr_base_sha}] if parent is None else parent,
        "tree": {"sha": root_oids[0]},
    }
    tree_calls = []
    for i, segment in enumerate(segments):
        leaf = i == len(segments) - 1
        tree_calls.append(
            (
                "GET",
                base + f"/git/trees/{root_oids[i]}",
                {
                    "sha": root_oids[i],
                    "truncated": False,
                    "tree": [
                        {
                            "path": segment,
                            "sha": fence.git_blob_oid if leaf else root_oids[i + 1],
                            "mode": "100644" if leaf else "040000",
                            "type": "blob" if leaf else "tree",
                        }
                    ],
                },
            )
        )
    transport.responses = [
        ("GET", base + f"/pulls/{request.publication.pr_number}", pr),
        ("GET", base + f"/git/commits/{merged_sha}", first_commit),
        ("GET", base + f"/git/commits/{merged_sha}", deepcopy(first_commit)),
        *tree_calls,
    ]
    return merged_sha


def test_still_open_historical_reconciliation_makes_one_get_no_new_grant(
    database, proposal_case, tmp_path
):
    store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    endpoint = f"/repos/{fence.repository}/pulls/{fence.pr_number}"
    transport.responses = [("GET", endpoint, _response(request))]
    result = asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert isinstance(result, SkillPublicationReconciliationEvidence)
    assert result.status == "open_unmerged"
    assert result.merge_commit_sha is None
    assert result.fence_fingerprint == fence.fence_fingerprint
    assert result.source_fence_was_no_send is True
    assert result.remote_actor_identity_unproven is True
    assert result.remote_merge_method_unproven is True
    assert result.cannot_retry_or_dispatch_github_merge is True
    assert result.no_skill_promotion_or_runtime_authority is True
    assert len(transport.calls) == 1 and transport.calls[0][0] == "GET"
    assert store.get_promotion_link(claim.proposal_id) is None


def test_closed_unmerged_is_not_a_retry_ticket(database, proposal_case, tmp_path):
    _store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    pr = _response(request, state="closed", merged=False)
    transport.responses = [("GET", f"/repos/{fence.repository}/pulls/{fence.pr_number}", pr)]
    result = asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert result.status == "closed_unmerged"
    assert result.cannot_retry_or_dispatch_github_merge is True
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_historical_single_parent_content_receipt_never_proves_method(
    database, proposal_case, tmp_path
):
    store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    sha = _prepare_merged_pr(request, fence, transport)
    result = asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert result.status == "merged_single_parent_content_observed"
    assert result.merge_commit_sha == sha
    assert result.no_skill_promotion_or_runtime_authority is True
    assert result.remote_actor_identity_unproven is True
    assert result.remote_merge_method_unproven is True
    assert len(transport.calls) == 8
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert store.get_promotion_link(claim.proposal_id) is None


@pytest.mark.parametrize(
    "wrong_parent",
    (
        [{"sha": "f" * 40}],
        [{"sha": "a" * 40}, {"sha": "b" * 40}],
        [],
        [{"sha": "not-a-sha"}],
    ),
)
def test_unapproved_merge_parent_or_regular_two_parent_merge_refused(
    database, proposal_case, tmp_path, wrong_parent
):
    _store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    _prepare_merged_pr(request, fence, transport, parent=wrong_parent)
    with pytest.raises(DeveloperError, match="not_exact_squash"):
        asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert len(transport.calls) == 2
    assert all(method == "GET" for method, _, _ in transport.calls)


@pytest.mark.parametrize("mutation", ("head", "repo", "number", "base_branch"))
def test_untrusted_pr_identity_cannot_reconcile(database, proposal_case, tmp_path, mutation):
    _store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    original = _response(request)
    if mutation == "head":
        original["head"]["sha"] = "e" * 40
    elif mutation == "repo":
        original["head"]["repo"]["full_name"] = "attacker/other"
    elif mutation == "number":
        original["number"] = 999
    else:
        original["base"]["ref"] = "other"
    transport.responses = [("GET", f"/repos/{fence.repository}/pulls/{fence.pr_number}", original)]
    with pytest.raises(DeveloperError, match="pr_identity_changed"):
        asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert len(transport.calls) == 1
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_merged_blob_or_file_mode_mutation_fails_closed(database, proposal_case, tmp_path):
    _store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    _prepare_merged_pr(request, fence, transport)
    transport.responses[-1][2]["tree"][0]["mode"] = "120000"
    with pytest.raises(DeveloperError, match="skill_publication_blob_tree_mode_invalid"):
        asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_historical_reconciliation_allowed_after_agent_revocation(
    database, settings, proposal_case, tmp_path
):
    store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    AgentInstanceService(database, store.agent_factory, MemoryService(database)).transition(
        claim.agent_instance_id, AgentInstanceTransition(action="pause")
    )
    transport.responses = [
        ("GET", f"/repos/{fence.repository}/pulls/{fence.pr_number}", _response(request))
    ]
    result = asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert result.status == "open_unmerged"
    assert result.no_skill_promotion_or_runtime_authority is True


def test_missing_fence_wrong_request_or_unauthorized_user_refused_before_any_get(
    database, proposal_case, tmp_path
):
    _store, claim, _decision, request, transport, _fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    wrong = request.model_copy(update={"merge_commit_message": "forged"})
    with pytest.raises(ValueError, match="fence_identity_changed"):
        asyncio.run(service.reconcile(claim.id, wrong, OPERATOR))
    with pytest.raises(ValueError, match="fence_missing"):
        asyncio.run(service.reconcile("00000000-0000-4000-8000-000000000001", request, OPERATOR))
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.reconcile(claim.id, request, "ahmad"))
    assert transport.calls == []


def test_changed_configured_publisher_is_refused_before_github_get(
    database, proposal_case, tmp_path
):
    _store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    original = service.fences.blob.remote.settings
    service.fences.blob.remote.settings = original.model_copy(
        update={"promotion_repository": "unapproved/other"}
    )
    transport.responses = [
        ("GET", f"/repos/{fence.repository}/pulls/{fence.pr_number}", _response(request))
    ]
    with pytest.raises(DeveloperError, match="reconciliation_publisher_changed"):
        asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert transport.calls == []


def test_one_commit_rebase_topology_not_misreported_as_verified_squash(
    database, proposal_case, tmp_path
):
    _store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    prior_merge = _prepare_merged_pr(request, fence, transport)
    # A different single-parent commit with the same tree is compatible with
    # either squash or GitHub rebase; PR topology alone cannot distinguish.
    rebased_head = "8" * 40
    assert prior_merge != rebased_head
    transport.responses[0][2]["merge_commit_sha"] = rebased_head
    for i in (1, 2):
        method, endpoint, commit = transport.responses[i]
        commit["sha"] = rebased_head
        transport.responses[i] = (method, endpoint.replace(prior_merge, rebased_head), commit)
    result = asyncio.run(service.reconcile(claim.id, request, OPERATOR))
    assert result.status == "merged_single_parent_content_observed"
    assert result.remote_merge_method_unproven is True
    assert result.remote_actor_identity_unproven is True
    assert result.no_skill_promotion_or_runtime_authority is True
    assert all(method == "GET" for method, _, _ in transport.calls)
