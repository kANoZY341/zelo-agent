"""No-network tests for exact GitHub branch and PR publication."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.developer_contracts import ValidationResult
from app.developer_process import DeveloperError
from app.promotion_contracts import PromotionCommitResult, PromotionRequest
from app.promotion_github import GitHubPromotionPublisher
from app.promotion_review import PromotionReviewer
from tests.test_developer_boundary import prepared

pytestmark = pytest.mark.developer_boundary


@pytest.fixture(autouse=True)
def linux_boundary_required():
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Linux Bubblewrap prerequisite not installed")


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        if not self.responses:
            raise AssertionError(f"Unexpected GitHub request: {method} {endpoint}")
        expected_method, matcher, response = self.responses.pop(0)
        assert method == expected_method
        if callable(matcher):
            assert matcher(endpoint, body)
        else:
            assert endpoint == matcher
        return response


def reviewed(runtime):
    repo, request = prepared(runtime)
    task_id = str(uuid4())
    attempt_id = repo.parent.name
    (repo.parent / "owner.json").write_text(
        json.dumps({"task": task_id, "execution": attempt_id, "active": False})
    )
    (repo / "feature.py").write_text("VALUE = 7\n")
    reviewer = PromotionReviewer(runtime)
    review = asyncio.run(reviewer.create(task_id, attempt_id, request, validation_passed=True))
    promotion = PromotionRequest(
        review=review,
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/publish-fixture",
        commit_title="feat: publish reviewed candidate",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: publish reviewed candidate",
    )
    validation = [
        ValidationResult(
            identifier=name,
            exit_code=0,
            output="",
            duration_seconds=0,
            completed_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        )
        for name in ("ruff_check", "ruff_format", "pytest", "git_diff_check")
    ]
    local = PromotionCommitResult(
        base_sha=request.base_sha,
        tree_sha="1" * 40,
        commit_sha="2" * 40,
        branch=promotion.branch,
        review_fingerprint=review.review_fingerprint,
        validation=validation,
    )
    settings = runtime.settings.model_copy(update={"promotion_repository": "kANoZY341/zelo-agent"})
    return repo, request, reviewer, promotion, local, settings


def base_ref(repo, sha):
    return ("GET", f"/repos/{repo}/git/ref/heads/main", {"object": {"sha": sha}})


def base_commit(repo, sha):
    return (
        "GET",
        f"/repos/{repo}/git/commits/{sha}",
        {"sha": sha, "tree": {"sha": "3" * 40}},
    )


def blob_call(repo, endpoint_body):
    endpoint, body = endpoint_body
    return endpoint == f"/repos/{repo}/git/blobs" and body["encoding"] == "base64"


def tree_call(repo, tree_sha):
    def match(endpoint, body):
        return (
            endpoint == f"/repos/{repo}/git/trees"
            and body["base_tree"] == "3" * 40
            and len(body["tree"]) == 1
            and body["tree"][0]["path"] == "feature.py"
            and body["tree"][0]["sha"] == "4" * 40
        )

    return ("POST", match, {"sha": tree_sha})


def commit_call(repo, promotion, commit_sha):
    def match(endpoint, body):
        return (
            endpoint == f"/repos/{repo}/git/commits"
            and body["tree"] == "1" * 40
            and body["parents"] == [promotion.review.base_sha]
            and body["message"] == "feat: publish reviewed candidate\n"
            and body["author"] == body["committer"]
        )

    return ("POST", match, {"sha": commit_sha})


def matching_refs(repo, branch, value):
    return (
        "GET",
        f"/repos/{repo}/git/matching-refs/heads/{branch}",
        value,
    )


def pr_query(endpoint, body):
    return endpoint.startswith("/repos/kANoZY341/zelo-agent/pulls?") and body is None


def exact_pr(promotion, local, number=7):
    return {
        "number": number,
        "html_url": f"https://github.com/kANoZY341/zelo-agent/pull/{number}",
        "head": {"ref": promotion.branch, "sha": local.commit_sha},
        "base": {"ref": promotion.target_base_branch, "sha": promotion.review.base_sha},
    }


def happy_script(promotion, local):
    repo = promotion.repository
    ref = f"refs/heads/{promotion.branch}"
    return [
        base_ref(repo, promotion.review.base_sha),
        base_commit(repo, promotion.review.base_sha),
        ("POST", lambda endpoint, body: blob_call(repo, (endpoint, body)), {"sha": "4" * 40}),
        tree_call(repo, local.tree_sha),
        commit_call(repo, promotion, local.commit_sha),
        base_ref(repo, promotion.review.base_sha),
        matching_refs(repo, promotion.branch, []),
        (
            "POST",
            f"/repos/{repo}/git/refs",
            {"ref": ref, "object": {"sha": local.commit_sha}},
        ),
        base_ref(repo, promotion.review.base_sha),
        ("GET", pr_query, []),
        ("POST", f"/repos/{repo}/pulls", exact_pr(promotion, local)),
    ]


def test_publish_creates_exact_branch_and_pull_request_without_token_access(developer_case):
    _, request, reviewer, promotion, local, settings = reviewed(developer_case)
    transport = ScriptedTransport(happy_script(promotion, local))
    publisher = GitHubPromotionPublisher(settings, developer_case, reviewer, transport)

    result = asyncio.run(publisher.publish(promotion, local, request))

    assert result.repository == promotion.repository
    assert result.commit_sha == local.commit_sha
    assert result.pr_number == 7
    assert result.pr_head_sha == local.commit_sha
    assert result.pr_base_sha == promotion.review.base_sha
    assert transport.responses == []
    serialized = repr(transport.calls)
    assert "token" not in serialized.lower()
    assert "authorization" not in serialized.lower()


def test_publish_reuses_exact_existing_branch_and_pr(developer_case):
    _, request, reviewer, promotion, local, settings = reviewed(developer_case)
    repo = promotion.repository
    ref = f"refs/heads/{promotion.branch}"
    script = happy_script(promotion, local)
    script[6:9] = [
        matching_refs(
            repo,
            promotion.branch,
            [{"ref": ref, "object": {"sha": local.commit_sha}}],
        ),
        base_ref(repo, promotion.review.base_sha),
    ]
    script[-2:] = [("GET", pr_query, [exact_pr(promotion, local, 9)])]
    transport = ScriptedTransport(script)
    publisher = GitHubPromotionPublisher(settings, developer_case, reviewer, transport)

    result = asyncio.run(publisher.publish(promotion, local, request))

    assert result.pr_number == 9
    assert not any(
        method == "POST" and endpoint.endswith("/git/refs")
        for method, endpoint, _ in transport.calls
    )
    assert not any(
        method == "POST" and endpoint.endswith("/pulls") for method, endpoint, _ in transport.calls
    )


def test_remote_base_move_fails_before_branch_creation(developer_case):
    _, request, reviewer, promotion, local, settings = reviewed(developer_case)
    script = happy_script(promotion, local)
    script[5] = base_ref(promotion.repository, "9" * 40)
    transport = ScriptedTransport(script)
    publisher = GitHubPromotionPublisher(settings, developer_case, reviewer, transport)

    with pytest.raises(DeveloperError, match="promotion_remote_base_changed"):
        asyncio.run(publisher.publish(promotion, local, request))

    assert not any(
        method == "POST" and endpoint.endswith("/git/refs")
        for method, endpoint, _ in transport.calls
    )


def test_remote_tree_or_commit_mismatch_fails_closed(developer_case):
    _, request, reviewer, promotion, local, settings = reviewed(developer_case)
    tree_script = happy_script(promotion, local)
    tree_script[3] = tree_call(promotion.repository, "8" * 40)
    with pytest.raises(DeveloperError, match="promotion_remote_tree_mismatch"):
        asyncio.run(
            GitHubPromotionPublisher(
                settings,
                developer_case,
                reviewer,
                ScriptedTransport(tree_script),
            ).publish(promotion, local, request)
        )

    commit_script = happy_script(promotion, local)
    commit_script[4] = commit_call(promotion.repository, promotion, "8" * 40)
    with pytest.raises(DeveloperError, match="promotion_remote_commit_mismatch"):
        asyncio.run(
            GitHubPromotionPublisher(
                settings,
                developer_case,
                reviewer,
                ScriptedTransport(commit_script),
            ).publish(promotion, local, request)
        )


def test_conflicting_existing_branch_is_never_overwritten(developer_case):
    _, request, reviewer, promotion, local, settings = reviewed(developer_case)
    script = happy_script(promotion, local)
    repo = promotion.repository
    ref = f"refs/heads/{promotion.branch}"
    script[6] = matching_refs(
        repo,
        promotion.branch,
        [{"ref": ref, "object": {"sha": "8" * 40}}],
    )
    transport = ScriptedTransport(script)
    publisher = GitHubPromotionPublisher(settings, developer_case, reviewer, transport)

    with pytest.raises(DeveloperError, match="promotion_branch_conflict"):
        asyncio.run(publisher.publish(promotion, local, request))

    assert not any(
        method == "POST" and endpoint.endswith("/git/refs")
        for method, endpoint, _ in transport.calls
    )


def test_candidate_mutation_fails_before_any_github_request(developer_case):
    repo, request, reviewer, promotion, local, settings = reviewed(developer_case)
    (repo / "feature.py").write_text("VALUE = 99\n")
    transport = ScriptedTransport([])
    publisher = GitHubPromotionPublisher(settings, developer_case, reviewer, transport)

    with pytest.raises(DeveloperError, match="promotion_candidate_changed"):
        asyncio.run(publisher.publish(promotion, local, request))

    assert transport.calls == []
