"""No-network exact-head CI verification tests."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import ROOT
from app.developer_contracts import WorkerIdentity
from app.developer_process import DeveloperError
from app.promotion_ci import PromotionCIVerifier
from app.promotion_contracts import (
    PromotionPublisherIdentity,
    PromotionPublishResult,
    PromotionRequest,
    PromotionReview,
)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        expected_method, expected_endpoint, response = self.responses.pop(0)
        assert method == expected_method
        assert endpoint == expected_endpoint
        assert body is None
        return response


def fixture(tmp_path):
    gh_config = tmp_path / "gh"
    gh_config.mkdir(mode=0o700)
    settings_data = {
        "promotion_repository": "kANoZY341/zelo-agent",
        "promotion_gh_executable": Path("/usr/bin/true"),
        "promotion_gh_config_dir": gh_config,
        "promotion_required_checks": ("checks",),
    }
    from app.config import Settings

    (ROOT / "runtime").mkdir(exist_ok=True)
    settings = Settings(_env_file=None, runtime_dir=ROOT / "runtime", **settings_data)
    publisher = PromotionPublisherIdentity(
        gateway_version="github-promotion-v1",
        repository="kANoZY341/zelo-agent",
        gh_executable="/usr/bin/true",
        gh_executable_sha256=__import__("hashlib")
        .sha256(Path("/usr/bin/true").read_bytes())
        .hexdigest(),
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
        ignored_artifact_summary=[],
        review_fingerprint="f" * 64,
    )
    promotion = PromotionRequest(
        review=review,
        publisher=publisher,
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/ci-fixture",
        commit_title="feat: fixture",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: fixture",
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
    return settings, promotion, publication


def pr(publication, promotion, *, head_sha=None, base_sha=None, state="open"):
    return {
        "number": publication.pr_number,
        "state": state,
        "head": {
            "ref": promotion.branch,
            "sha": head_sha or publication.pr_head_sha,
            "repo": {"full_name": promotion.repository},
        },
        "base": {
            "ref": "main",
            "sha": base_sha or promotion.review.base_sha,
        },
    }


def check(
    check_id=10,
    *,
    name="checks",
    status="completed",
    conclusion="success",
    app_id=15368,
    app_slug="github-actions",
):
    return {
        "id": check_id,
        "name": name,
        "app": {"id": app_id, "slug": app_slug},
        "status": status,
        "conclusion": conclusion,
        "completed_at": "2026-09-19T12:30:00Z",
    }


def script(publication, promotion, checks):
    repo = promotion.repository
    return [
        ("GET", f"/repos/{repo}/pulls/7", pr(publication, promotion)),
        (
            "GET",
            f"/repos/{repo}/commits/{publication.pr_head_sha}/check-runs?filter=latest&per_page=100",
            {"total_count": len(checks), "check_runs": checks},
        ),
    ]


def test_exact_head_green_ci_is_snapshotted(tmp_path):
    settings, promotion, publication = fixture(tmp_path)
    transport = ScriptedTransport(script(publication, promotion, [check()]))
    result = asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))

    assert result.pr_number == 7
    assert result.head_sha == publication.pr_head_sha
    assert result.base_sha == promotion.review.base_sha
    assert result.required_checks == ["checks"]
    assert result.checks[0].name == "checks"
    assert result.checks[0].app_id == settings.promotion_ci_app_id
    assert result.checks[0].app_slug == settings.promotion_ci_app_slug
    assert result.checks[0].conclusion == "success"
    assert transport.responses == []


@pytest.mark.parametrize(
    ("checks", "code"),
    [
        ([], "promotion_ci_required_check_missing"),
        ([check(status="in_progress", conclusion=None)], "promotion_ci_not_green"),
        ([check(conclusion="failure")], "promotion_ci_not_green"),
        ([check(app_id=1)], "promotion_ci_app_mismatch"),
    ],
)
def test_missing_pending_or_failed_required_ci_fails_closed(tmp_path, checks, code):
    settings, promotion, publication = fixture(tmp_path)
    transport = ScriptedTransport(script(publication, promotion, checks))

    with pytest.raises(DeveloperError, match=code):
        asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))


@pytest.mark.parametrize(
    ("head_sha", "base_sha", "state"),
    [
        ("8" * 40, None, "open"),
        (None, "7" * 40, "open"),
        (None, None, "closed"),
    ],
)
def test_pr_identity_change_invalidates_ci_verification(tmp_path, head_sha, base_sha, state):
    settings, promotion, publication = fixture(tmp_path)
    repo = promotion.repository
    transport = ScriptedTransport(
        [
            (
                "GET",
                f"/repos/{repo}/pulls/7",
                pr(
                    publication,
                    promotion,
                    head_sha=head_sha,
                    base_sha=base_sha,
                    state=state,
                ),
            )
        ]
    )

    with pytest.raises(DeveloperError, match="promotion_ci_pr_changed"):
        asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))


def test_truncated_check_run_response_fails_closed(tmp_path):
    settings, promotion, publication = fixture(tmp_path)
    repo = promotion.repository
    transport = ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/pulls/7", pr(publication, promotion)),
            (
                "GET",
                f"/repos/{repo}/commits/{publication.pr_head_sha}/check-runs?filter=latest&per_page=100",
                {"total_count": 101, "check_runs": [check()]},
            ),
        ]
    )

    with pytest.raises(DeveloperError, match="promotion_ci_response_truncated"):
        asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))


def test_check_runs_paginate_until_total_count(tmp_path):
    settings, promotion, publication = fixture(tmp_path)
    repo = promotion.repository
    first_page = [check(check_id=index + 1, name="noise") for index in range(100)]
    transport = ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/pulls/7", pr(publication, promotion)),
            (
                "GET",
                f"/repos/{repo}/commits/{publication.pr_head_sha}/check-runs?filter=latest&per_page=100",
                {"total_count": 101, "check_runs": first_page},
            ),
            (
                "GET",
                f"/repos/{repo}/commits/{publication.pr_head_sha}/check-runs?filter=latest&per_page=100&page=2",
                {"total_count": 101, "check_runs": [check(check_id=101)]},
            ),
        ]
    )

    result = asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))

    assert [item.name for item in result.checks] == ["checks"]
    assert transport.responses == []


def test_boolean_total_count_is_rejected(tmp_path):
    settings, promotion, publication = fixture(tmp_path)
    repo = promotion.repository
    transport = ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/pulls/7", pr(publication, promotion)),
            (
                "GET",
                f"/repos/{repo}/commits/{publication.pr_head_sha}/check-runs?filter=latest&per_page=100",
                {"total_count": True, "check_runs": [check()]},
            ),
        ]
    )

    with pytest.raises(DeveloperError, match="promotion_ci_response_invalid"):
        asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))


def test_ci_rejects_changed_head_repository(tmp_path):
    settings, promotion, publication = fixture(tmp_path)
    repo = promotion.repository
    changed = pr(publication, promotion)
    changed["head"]["repo"]["full_name"] = "attacker/fork"
    transport = ScriptedTransport([("GET", f"/repos/{repo}/pulls/7", changed)])

    with pytest.raises(DeveloperError, match="promotion_ci_pr_changed"):
        asyncio.run(PromotionCIVerifier(settings, transport).verify(promotion, publication))
