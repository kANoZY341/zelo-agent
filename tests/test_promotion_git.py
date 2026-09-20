"""Trusted local promotion commit construction; no network or GitHub credentials."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.developer_process import DeveloperError
from app.promotion_contracts import PromotionPublisherIdentity, PromotionRequest
from app.promotion_git import PromotionCommitter
from app.promotion_review import PromotionReviewer
from tests.test_developer_boundary import prepared

pytestmark = pytest.mark.developer_boundary


@pytest.fixture(autouse=True)
def linux_boundary_required():
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Linux Bubblewrap prerequisite not installed")


def reviewed_candidate(runtime):
    repo, request = prepared(runtime)
    developer_task_id = str(uuid4())
    developer_attempt_id = repo.parent.name
    (repo.parent / "owner.json").write_text(
        json.dumps(
            {
                "task": developer_task_id,
                "execution": developer_attempt_id,
                "active": False,
            }
        )
    )
    (repo / "feature.py").write_text("VALUE = 7\n")
    reviewer = PromotionReviewer(runtime)
    review = asyncio.run(
        reviewer.create(
            developer_task_id,
            developer_attempt_id,
            request,
            validation_passed=True,
        )
    )
    promotion = PromotionRequest(
        review=review,
        publisher=PromotionPublisherIdentity(
            gateway_version="github-promotion-v1",
            repository="kANoZY341/zelo-agent",
            gh_executable="/usr/bin/gh",
            gh_executable_sha256="c" * 64,
            gh_config_dir="/home/zelo/.config/gh",
            timeout_seconds=30,
        ),
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/commit-fixture",
        commit_title="feat: promote reviewed candidate",
        commit_message="Bound to the exact reviewed bytes.",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: promote reviewed candidate",
    )
    return repo, request, reviewer, promotion


def run_commit(runtime, request, reviewer, promotion):
    task_id = str(uuid4())
    attempt_id = str(uuid4())
    result = asyncio.run(
        PromotionCommitter(runtime, reviewer).create(task_id, attempt_id, promotion, request)
    )
    return result, runtime.settings.workspace_root / "promotion" / attempt_id / "repo"


def trusted_git(repo, *args):
    from tests.developer_helpers import git

    return git(repo, *args)


def test_promotion_commit_is_exact_single_parent_and_has_no_remote(developer_case):
    developer_repo, request, reviewer, promotion = reviewed_candidate(developer_case)
    primary_before = asyncio.run(developer_case.primary_snapshot(request))

    result, promotion_repo = run_commit(developer_case, request, reviewer, promotion)

    assert result.base_sha == request.base_sha
    assert result.review_fingerprint == promotion.review.review_fingerprint
    assert trusted_git(promotion_repo, "rev-parse", result.commit_sha + "^") == request.base_sha
    assert (
        trusted_git(promotion_repo, "rev-parse", result.commit_sha + "^{tree}") == result.tree_sha
    )
    assert (
        trusted_git(promotion_repo, "rev-parse", "refs/heads/" + promotion.branch)
        == result.commit_sha
    )
    assert trusted_git(promotion_repo, "remote") == ""
    assert trusted_git(promotion_repo, "show", f"{result.commit_sha}:feature.py") == "VALUE = 7"
    assert trusted_git(developer_repo, "rev-parse", "HEAD") == request.base_sha
    assert asyncio.run(developer_case.primary_snapshot(request)) == primary_before
    assert all(item.exit_code == 0 for item in result.validation)
    owner = json.loads((promotion_repo.parent / "owner.json").read_text())
    assert owner == {
        "task": owner["task"],
        "execution": promotion_repo.parent.name,
        "active": False,
    }


def test_same_approved_spec_produces_same_commit_sha(developer_case):
    _, request, reviewer, promotion = reviewed_candidate(developer_case)

    first, _ = run_commit(developer_case, request, reviewer, promotion)
    second, _ = run_commit(developer_case, request, reviewer, promotion)

    assert first.tree_sha == second.tree_sha
    assert first.commit_sha == second.commit_sha


def test_mutation_after_review_refuses_commit(developer_case):
    developer_repo, request, reviewer, promotion = reviewed_candidate(developer_case)
    (developer_repo / "feature.py").write_text("VALUE = 8\n")

    with pytest.raises(DeveloperError, match="promotion_candidate_changed"):
        run_commit(developer_case, request, reviewer, promotion)


def test_symlink_bytes_are_promoted_without_following_target(developer_case):
    repo, request = prepared(developer_case)
    task_id = str(uuid4())
    attempt_id = repo.parent.name
    (repo.parent / "owner.json").write_text(
        json.dumps({"task": task_id, "execution": attempt_id, "active": False})
    )
    (repo / "link").symlink_to("../outside")
    reviewer = PromotionReviewer(developer_case)
    review = asyncio.run(reviewer.create(task_id, attempt_id, request, validation_passed=True))
    promotion = PromotionRequest(
        review=review,
        publisher=PromotionPublisherIdentity(
            gateway_version="github-promotion-v1",
            repository="kANoZY341/zelo-agent",
            gh_executable="/usr/bin/gh",
            gh_executable_sha256="c" * 64,
            gh_config_dir="/home/zelo/.config/gh",
            timeout_seconds=30,
        ),
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/symlink-fixture",
        commit_title="test: promote symlink",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="test: promote symlink",
    )

    result, promotion_repo = run_commit(developer_case, request, reviewer, promotion)

    assert (
        trusted_git(promotion_repo, "cat-file", "-p", f"{result.commit_sha}:link") == "../outside"
    )
    assert trusted_git(promotion_repo, "ls-tree", result.commit_sha, "--", "link").startswith(
        "120000 blob "
    )


def test_deleted_file_is_absent_from_promoted_tree(developer_case):
    repo, request = prepared(developer_case)
    task_id = str(uuid4())
    attempt_id = repo.parent.name
    (repo.parent / "owner.json").write_text(
        json.dumps({"task": task_id, "execution": attempt_id, "active": False})
    )
    (repo / ".gitignore").unlink()
    reviewer = PromotionReviewer(developer_case)
    review = asyncio.run(reviewer.create(task_id, attempt_id, request, validation_passed=True))
    promotion = PromotionRequest(
        review=review,
        publisher=PromotionPublisherIdentity(
            gateway_version="github-promotion-v1",
            repository="kANoZY341/zelo-agent",
            gh_executable="/usr/bin/gh",
            gh_executable_sha256="c" * 64,
            gh_config_dir="/home/zelo/.config/gh",
            timeout_seconds=30,
        ),
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/delete-fixture",
        commit_title="test: promote deletion",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="test: promote deletion",
    )

    result, promotion_repo = run_commit(developer_case, request, reviewer, promotion)

    assert trusted_git(promotion_repo, "ls-tree", result.commit_sha, "--", ".gitignore") == ""
