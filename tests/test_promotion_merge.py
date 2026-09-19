"""No-network tests for exact-head separately approved squash merge execution."""

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
    PromotionCIVerification,
    PromotionPublisherIdentity,
    PromotionPublishResult,
    PromotionRequest,
    PromotionReview,
)
from app.promotion_merge import PromotionMergeRuntime


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


class FakeCIVerifier:
    def __init__(self, value):
        self.value = value
        self.calls = []

    async def verify(self, promotion, publication):
        self.calls.append((promotion, publication))
        return self.value


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
        promotion_required_checks=("checks",),
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
        branch="zelo/promotion/merge-fixture",
        commit_title="feat: merge fixture",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: merge fixture",
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
    check = CICheckSnapshot(
        check_run_id=10,
        name="checks",
        status="completed",
        conclusion="success",
        completed_at=datetime(2026, 9, 19, 12, 30, tzinfo=UTC),
    )
    ci = PromotionCIVerification(
        repository=promotion.repository,
        pr_number=7,
        base_branch="main",
        base_sha=review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        required_checks=["checks"],
        checks=[check],
        verified_at=datetime(2026, 9, 19, 12, 31, tzinfo=UTC),
    )
    request = MergeRequest(
        promotion_task_id="3" * 36,
        promotion_action_version=1,
        promotion_action_hash="4" * 64,
        promotion=promotion,
        publication=publication,
        ci=ci,
        merge_commit_title=promotion.pr_title,
        merge_commit_message=promotion.pr_body,
    )
    return settings, request, ci


def merged_pr(request, merge_sha):
    return {
        "number": request.publication.pr_number,
        "state": "closed",
        "merged": True,
        "merge_commit_sha": merge_sha,
        "head": {
            "ref": request.publication.branch,
            "sha": request.publication.pr_head_sha,
        },
        "base": {"ref": "main", "sha": request.publication.pr_base_sha},
    }


def test_merge_reverifies_ci_and_binds_exact_head(tmp_path):
    settings, request, ci = fixture(tmp_path)
    merge_sha = "8" * 40
    repo = request.promotion.repository
    transport = ScriptedTransport(
        [
            (
                "POST",
                f"/repos/{repo}/pulls/7/merge",
                {"merged": True, "sha": merge_sha},
            ),
            (
                "GET",
                f"/repos/{repo}/pulls/7",
                merged_pr(request, merge_sha),
            ),
        ]
    )
    verifier = FakeCIVerifier(ci)
    result = asyncio.run(
        PromotionMergeRuntime(settings, verifier, transport).run(request)
    )

    assert result.merge_commit_sha == merge_sha
    assert result.head_sha == request.publication.pr_head_sha
    assert verifier.calls == [(request.promotion, request.publication)]
    method, endpoint, body = transport.calls[0]
    assert method == "POST"
    assert endpoint.endswith("/pulls/7/merge")
    assert body == {
        "sha": request.publication.pr_head_sha,
        "merge_method": "squash",
        "commit_title": request.merge_commit_title,
        "commit_message": request.merge_commit_message,
    }


def test_merge_refuses_changed_ci_identity_before_side_effect(tmp_path):
    settings, request, ci = fixture(tmp_path)
    changed = ci.model_copy(update={"head_sha": "7" * 40})
    transport = ScriptedTransport([])

    with pytest.raises(DeveloperError, match="promotion_merge_ci_changed"):
        asyncio.run(
            PromotionMergeRuntime(settings, FakeCIVerifier(changed), transport).run(
                request
            )
        )

    assert transport.calls == []


def test_merge_rejects_unmerged_or_mismatched_result(tmp_path):
    settings, request, ci = fixture(tmp_path)
    repo = request.promotion.repository
    transport = ScriptedTransport(
        [
            (
                "POST",
                f"/repos/{repo}/pulls/7/merge",
                {"merged": False, "sha": None},
            )
        ]
    )

    with pytest.raises(DeveloperError, match="promotion_merge_rejected"):
        asyncio.run(
            PromotionMergeRuntime(settings, FakeCIVerifier(ci), transport).run(request)
        )
