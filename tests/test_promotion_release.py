"""No-network tests for exact-main-SHA release tagging."""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import ROOT, Settings
from app.developer_contracts import WorkerIdentity
from app.developer_process import DeveloperError
from app.promotion_contracts import (
    CICheckSnapshot,
    MergeRequest,
    MergeRunResult,
    PromotionCIVerification,
    PromotionPublishResult,
    PromotionPublisherIdentity,
    PromotionRequest,
    PromotionReview,
    ReleaseRequest,
)
from app.promotion_release import PromotionReleaseRuntime


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        if not self.responses:
            raise AssertionError(f"Unexpected request: {method} {endpoint}")
        expected_method, expected_endpoint, response = self.responses.pop(0)
        assert method == expected_method
        assert endpoint == expected_endpoint
        return response


def fixture(tmp_path):
    (ROOT / "runtime").mkdir(exist_ok=True)
    gh_config = tmp_path / "gh"
    gh_config.mkdir(mode=0o700)
    executable = Path("/usr/bin/true")
    settings = Settings(
        _env_file=None,
        runtime_dir=ROOT / "runtime",
        promotion_repository="kANoZY341/zelo-agent",
        promotion_gh_executable=executable,
        promotion_gh_config_dir=gh_config,
    )
    publisher = PromotionPublisherIdentity(
        gateway_version="github-promotion-v1",
        repository="kANoZY341/zelo-agent",
        gh_executable=str(executable),
        gh_executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        gh_config_dir=str(gh_config),
        timeout_seconds=settings.promotion_github_timeout_seconds,
    )
    review = PromotionReview(
        developer_task_id="1" * 36,
        developer_attempt_id="2" * 36,
        source_repository="/trusted/source",
        workspace="developer/attempt/repo",
        base_sha="a" * 40,
        head_sha="a" * 40,
        worker_identity=WorkerIdentity(
            implementation="fake",
            config_version="1",
            cli_version="fake",
            capability_profile="test",
            executable="/fake",
            executable_sha256="b" * 64,
            code_mode_host="/fake-host",
            code_mode_host_sha256="c" * 64,
            auth_location="none",
        ),
        validation_identity="d" * 64,
        validation_profile="zelo_core",
        validation_version="1",
        sandbox_version="1",
        validation_passed=True,
        changed_file_count=1,
        candidate_manifest=[
            {
                "path": "feature.py",
                "operation": "add",
                "object_type": "file",
                "mode": "100644",
                "size": 1,
                "sha256": "e" * 64,
            }
        ],
        review_fingerprint="f" * 64,
    )
    promotion = PromotionRequest(
        review=review,
        publisher=publisher,
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/release-fixture",
        commit_title="feat: release fixture",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: release fixture",
    )
    publication = PromotionPublishResult(
        repository=promotion.repository,
        base_branch="main",
        base_sha=review.base_sha,
        branch=promotion.branch,
        commit_sha="9" * 40,
        pr_number=7,
        pr_url="https://github.com/kANoZY341/zelo-agent/pull/7",
        pr_head_sha="9" * 40,
        pr_base_sha=review.base_sha,
        branch_created=True,
        pr_created=True,
    )
    ci = PromotionCIVerification(
        repository=promotion.repository,
        pr_number=7,
        base_branch="main",
        base_sha=review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        required_checks=["checks"],
        checks=[
            CICheckSnapshot(
                check_run_id=10,
                name="checks",
                status="completed",
                conclusion="success",
                completed_at=datetime(2026, 9, 19, 12, 30, tzinfo=UTC),
            )
        ],
        verified_at=datetime(2026, 9, 19, 12, 31, tzinfo=UTC),
    )
    merge = MergeRequest(
        promotion_task_id="3" * 36,
        promotion_action_version=1,
        promotion_action_hash="4" * 64,
        promotion=promotion,
        publication=publication,
        ci=ci,
        merge_commit_title=promotion.pr_title,
    )
    merge_result = MergeRunResult(
        repository=promotion.repository,
        pr_number=7,
        base_branch="main",
        base_sha=review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        merge_method="squash",
        merge_commit_sha="8" * 40,
    )
    release = ReleaseRequest(
        merge_task_id="5" * 36,
        merge_action_version=1,
        merge_action_hash="6" * 64,
        merge=merge,
        merge_result=merge_result,
        publisher=publisher,
        repository=promotion.repository,
        main_sha=merge_result.merge_commit_sha,
        tag="v0.7.0",
        tag_message="v0.7.0",
        tagger_name="Zelo Agent",
        tagger_email="zelo@localhost.invalid",
        tagger_date=datetime(2026, 9, 19, 13, 0, tzinfo=UTC),
    )
    return settings, release


def main_ref(request, sha=None):
    return {
        "ref": "refs/heads/main",
        "object": {"type": "commit", "sha": sha or request.main_sha},
    }


def tag_ref(request, tag_object_sha):
    return {
        "ref": f"refs/tags/{request.tag}",
        "object": {"type": "tag", "sha": tag_object_sha},
    }


def tag_object(request, tag_object_sha):
    return {
        "sha": tag_object_sha,
        "tag": request.tag,
        "object": {"type": "commit", "sha": request.main_sha},
    }


def test_release_creates_annotated_tag_only_on_exact_main(tmp_path):
    settings, request = fixture(tmp_path)
    repo = request.repository
    tag_sha = "7" * 40
    transport = ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/git/ref/heads/main", main_ref(request)),
            ("GET", f"/repos/{repo}/git/matching-refs/tags/{request.tag}", []),
            ("POST", f"/repos/{repo}/git/tags", tag_object(request, tag_sha)),
            ("GET", f"/repos/{repo}/git/ref/heads/main", main_ref(request)),
            ("POST", f"/repos/{repo}/git/refs", tag_ref(request, tag_sha)),
            (
                "GET",
                f"/repos/{repo}/git/matching-refs/tags/{request.tag}",
                [tag_ref(request, tag_sha)],
            ),
            ("GET", f"/repos/{repo}/git/tags/{tag_sha}", tag_object(request, tag_sha)),
        ]
    )

    result = asyncio.run(PromotionReleaseRuntime(settings, transport).run(request))

    assert result.tag == "v0.7.0"
    assert result.main_sha == request.main_sha
    assert result.annotated is True
    assert result.reused is False
    create = next(call for call in transport.calls if call[1].endswith("/git/tags"))
    assert create[2]["object"] == request.main_sha
    assert create[2]["type"] == "commit"


def test_release_reuses_existing_exact_annotated_tag(tmp_path):
    settings, request = fixture(tmp_path)
    repo = request.repository
    tag_sha = "7" * 40
    transport = ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/git/ref/heads/main", main_ref(request)),
            (
                "GET",
                f"/repos/{repo}/git/matching-refs/tags/{request.tag}",
                [tag_ref(request, tag_sha)],
            ),
            ("GET", f"/repos/{repo}/git/tags/{tag_sha}", tag_object(request, tag_sha)),
        ]
    )

    result = asyncio.run(PromotionReleaseRuntime(settings, transport).run(request))

    assert result.reused is True
    assert result.tag_object_sha == tag_sha
    assert not any(method == "POST" for method, _, _ in transport.calls)


def test_release_refuses_main_move_or_tag_conflict(tmp_path):
    settings, request = fixture(tmp_path)
    repo = request.repository
    moved = ScriptedTransport(
        [("GET", f"/repos/{repo}/git/ref/heads/main", main_ref(request, "1" * 40))]
    )
    with pytest.raises(DeveloperError, match="promotion_release_main_changed"):
        asyncio.run(PromotionReleaseRuntime(settings, moved).run(request))

    conflict = ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/git/ref/heads/main", main_ref(request)),
            (
                "GET",
                f"/repos/{repo}/git/matching-refs/tags/{request.tag}",
                [
                    {
                        "ref": f"refs/tags/{request.tag}",
                        "object": {"type": "commit", "sha": "1" * 40},
                    }
                ],
            ),
        ]
    )
    with pytest.raises(DeveloperError, match="promotion_release_tag_conflict"):
        asyncio.run(PromotionReleaseRuntime(settings, conflict).run(request))
