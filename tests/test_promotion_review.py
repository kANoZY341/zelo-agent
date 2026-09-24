"""v0.7 immutable promotion-review identity tests; no model or GitHub calls."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.developer import DeveloperError
from app.promotion_contracts import PromotionPublisherIdentity, PromotionRequest
from app.promotion_review import PromotionReviewer, validate_manifest_path
from tests.test_developer_boundary import prepared

pytestmark = pytest.mark.developer_boundary


@pytest.fixture(autouse=True)
def linux_boundary_required():
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Linux Bubblewrap prerequisite not installed")


def owned_candidate(runtime):
    repo, request = prepared(runtime)
    task_id = str(uuid4())
    attempt_id = repo.parent.name
    (repo.parent / "owner.json").write_text(
        json.dumps({"task": task_id, "execution": attempt_id, "active": False})
    )
    return PromotionReviewer(runtime), repo, request, task_id, attempt_id


def create_review(reviewer, task_id, attempt_id, request, *, expected_diff_sha256=None):
    return asyncio.run(
        reviewer.create(
            task_id,
            attempt_id,
            request,
            validation_passed=True,
            expected_diff_sha256=expected_diff_sha256,
        )
    )


def test_review_is_deterministic_sorted_and_ignores_generated_artifacts(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    (repo / "z.txt").write_text("z\n")
    (repo / "a.txt").write_text("a\n")
    cache = repo / ".ruff_cache"
    cache.mkdir()
    (cache / "cache-entry").write_text("generated\n")

    first = create_review(reviewer, task_id, attempt_id, request)
    second = create_review(reviewer, task_id, attempt_id, request)

    assert first == second
    assert first.review_fingerprint == second.review_fingerprint
    assert [entry.path for entry in first.candidate_manifest] == ["a.txt", "z.txt"]
    assert first.changed_file_count == 2
    assert [item.path for item in first.ignored_artifact_summary] == [".ruff_cache/cache-entry"]


def test_review_rejects_candidate_changed_after_evaluation(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    target = repo / "candidate.txt"
    target.write_text("first\n")
    reviewed = asyncio.run(reviewer.runtime.review(repo, request))
    expected = hashlib.sha256(reviewed["diff"].encode("utf-8")).hexdigest()

    create_review(
        reviewer,
        task_id,
        attempt_id,
        request,
        expected_diff_sha256=expected,
    )

    target.write_text("second\n")

    with pytest.raises(DeveloperError, match="promotion_candidate_changed"):
        create_review(
            reviewer,
            task_id,
            attempt_id,
            request,
            expected_diff_sha256=expected,
        )


def test_binary_content_is_hashed_exactly(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    content = b"\x00\xffbinary\r\ncontent\x00"
    (repo / "payload.bin").write_bytes(content)

    review = create_review(reviewer, task_id, attempt_id, request)
    entry = review.candidate_manifest[0]

    assert entry.path == "payload.bin"
    assert entry.operation == "add"
    assert entry.object_type == "file"
    assert entry.mode == "100644"
    assert entry.size == len(content)
    assert entry.sha256 == hashlib.sha256(content).hexdigest()


def test_symlink_hashes_target_bytes_without_following_target(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    target = "../outside-sensitive-file"
    (repo / "link").symlink_to(target)

    review = create_review(reviewer, task_id, attempt_id, request)
    entry = review.candidate_manifest[0]

    assert entry.object_type == "symlink"
    assert entry.mode == "120000"
    assert entry.size == len(target.encode())
    assert entry.sha256 == hashlib.sha256(target.encode()).hexdigest()


def test_deleted_file_uses_base_type_and_mode_without_present_digest(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    (repo / "test_example.py").unlink()

    review = create_review(reviewer, task_id, attempt_id, request)
    entry = next(item for item in review.candidate_manifest if item.path == "test_example.py")

    assert entry.operation == "delete"
    assert entry.object_type == "file"
    assert entry.mode == "100644"
    assert entry.size is None
    assert entry.sha256 is None


def test_rename_is_normalized_to_delete_and_add(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    (repo / "test_example.py").rename(repo / "renamed.py")

    review = create_review(reviewer, task_id, attempt_id, request)
    operations = {(entry.path, entry.operation) for entry in review.candidate_manifest}

    assert operations == {("test_example.py", "delete"), ("renamed.py", "add")}


def test_ignored_artifact_drift_does_not_change_promotable_candidate(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    (repo / "candidate.txt").write_text("approved bytes\n")
    approved = create_review(reviewer, task_id, attempt_id, request)

    cache = repo / ".ruff_cache"
    cache.mkdir()
    (cache / "generated").write_text("ignored validation artifact\n")

    asyncio.run(reviewer.reverify(approved, request))


def test_mutation_after_review_invalidates_candidate(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    path = repo / "candidate.txt"
    path.write_text("approved bytes\n")
    approved = create_review(reviewer, task_id, attempt_id, request)

    path.write_text("changed after review\n")

    with pytest.raises(DeveloperError, match="promotion_candidate_changed"):
        asyncio.run(reviewer.reverify(approved, request))


def test_special_untracked_file_fails_closed(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    fifo = repo / "pipe"
    os.mkfifo(fifo)

    with pytest.raises(DeveloperError, match="promotion_special_file"):
        create_review(reviewer, task_id, attempt_id, request)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute",
        "../escape",
        "a/../escape",
        "./file",
        "a//file",
        ".git/config",
        "nested/.git/config",
        "windows\\path",
    ],
)
def test_manifest_paths_reject_ambiguous_or_git_metadata_paths(value):
    with pytest.raises(DeveloperError, match="promotion_path_invalid"):
        validate_manifest_path(value)


def test_review_requires_inactive_owned_workspace_and_successful_validation(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    (repo / "candidate.txt").write_text("candidate\n")
    owner = repo.parent / "owner.json"
    owner.write_text(json.dumps({"task": task_id, "execution": attempt_id, "active": True}))

    with pytest.raises(DeveloperError, match="promotion_workspace_active"):
        create_review(reviewer, task_id, attempt_id, request)

    owner.write_text(json.dumps({"task": task_id, "execution": attempt_id, "active": False}))
    with pytest.raises(DeveloperError, match="promotion_source_not_validated"):
        asyncio.run(
            reviewer.create(
                task_id,
                attempt_id,
                request,
                validation_passed=False,
            )
        )


def test_promotion_request_is_frozen_and_binds_review(developer_case):
    reviewer, repo, request, task_id, attempt_id = owned_candidate(developer_case)
    (repo / "candidate.txt").write_text("candidate\n")
    review = create_review(reviewer, task_id, attempt_id, request)
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
        branch="zelo/promotion/test-123",
        commit_title="feat: promote reviewed candidate",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: promote reviewed candidate",
    )

    assert promotion.review.review_fingerprint == review.review_fingerprint
    with pytest.raises(ValidationError):
        promotion.branch = "zelo/promotion/changed"
