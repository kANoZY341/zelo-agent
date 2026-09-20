"""Exact-head GitHub CI verification before a merge can become approvable."""

from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.developer_process import DeveloperError
from app.promotion_contracts import (
    CICheckSnapshot,
    PromotionCIVerification,
    PromotionPublishResult,
    PromotionRequest,
)
from app.promotion_github import GhApiTransport, GitHubTransport, publisher_identity

CI_VERSION = "promotion-ci-v1"
MAX_CHECK_RUN_PAGES = 10


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeveloperError("promotion_ci_response_invalid")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise DeveloperError("promotion_ci_response_invalid")
    return value


class PromotionCIVerifier:
    def __init__(
        self,
        settings: Settings,
        transport: GitHubTransport | None = None,
    ) -> None:
        if settings.promotion_repository is None:
            raise DeveloperError("promotion_repository_unconfigured")
        self.settings = settings
        self.repository = settings.promotion_repository
        self.transport = transport or GhApiTransport(settings)

    def endpoint(self, suffix: str) -> str:
        if not suffix.startswith("/"):
            raise DeveloperError("promotion_ci_request_invalid")
        return f"/repos/{self.repository}{suffix}"

    async def verify(
        self,
        promotion: PromotionRequest,
        publication: PromotionPublishResult,
    ) -> PromotionCIVerification:
        if promotion.publisher != publisher_identity(self.settings):
            raise DeveloperError("promotion_publisher_changed")
        if (
            publication.repository != self.repository
            or publication.repository != promotion.repository
            or publication.pr_head_sha != publication.commit_sha
            or publication.pr_head_sha != publication.commit_sha
            or publication.pr_base_sha != promotion.review.base_sha
            or publication.base_branch != promotion.target_base_branch
            or publication.branch != promotion.branch
        ):
            raise DeveloperError("promotion_ci_identity_mismatch")

        pr = _dict(
            await self.transport.request("GET", self.endpoint(f"/pulls/{publication.pr_number}"))
        )
        head = _dict(pr.get("head"))
        base = _dict(pr.get("base"))
        head_repo = _dict(head.get("repo"))
        if (
            pr.get("number") != publication.pr_number
            or pr.get("state") != "open"
            or head_repo.get("full_name") != self.repository
            or head.get("ref") != promotion.branch
            or head.get("sha") != publication.pr_head_sha
            or base.get("ref") != promotion.target_base_branch
            or base.get("sha") != promotion.review.base_sha
        ):
            raise DeveloperError("promotion_ci_pr_changed")

        runs: list[Any] = []
        total_count: int | None = None
        base_checks = f"/commits/{publication.pr_head_sha}/check-runs?filter=latest&per_page=100"
        for page in range(1, MAX_CHECK_RUN_PAGES + 1):
            suffix = base_checks if page == 1 else f"{base_checks}&page={page}"
            response = _dict(await self.transport.request("GET", self.endpoint(suffix)))
            page_runs = _list(response.get("check_runs"))
            count = response.get("total_count")
            if type(count) is not int or count < 0 or len(page_runs) > 100:
                raise DeveloperError("promotion_ci_response_invalid")
            if total_count is None:
                total_count = count
                if total_count > MAX_CHECK_RUN_PAGES * 100:
                    raise DeveloperError("promotion_ci_response_truncated")
            elif count != total_count:
                raise DeveloperError("promotion_ci_response_invalid")
            runs.extend(page_runs)
            if len(runs) > total_count:
                raise DeveloperError("promotion_ci_response_invalid")
            if len(runs) == total_count:
                break
            if len(page_runs) < 100:
                raise DeveloperError("promotion_ci_response_truncated")
        if total_count is None or len(runs) != total_count:
            raise DeveloperError("promotion_ci_response_truncated")
        required = list(self.settings.promotion_required_checks)
        selected: list[CICheckSnapshot] = []
        for name in required:
            matches = [
                _dict(item) for item in runs if isinstance(item, dict) and item.get("name") == name
            ]
            if not matches:
                raise DeveloperError("promotion_ci_required_check_missing")
            for run in matches:
                app = _dict(run.get("app"))
                if (
                    app.get("id") != self.settings.promotion_ci_app_id
                    or app.get("slug") != self.settings.promotion_ci_app_slug
                ):
                    raise DeveloperError("promotion_ci_app_mismatch")
                if run.get("status") != "completed" or run.get("conclusion") != "success":
                    raise DeveloperError("promotion_ci_not_green")
                check_id = run.get("id")
                completed_at = run.get("completed_at")
                if (
                    not isinstance(check_id, int)
                    or check_id < 1
                    or not isinstance(completed_at, str)
                ):
                    raise DeveloperError("promotion_ci_response_invalid")
                try:
                    stamp = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                except ValueError:
                    raise DeveloperError("promotion_ci_response_invalid") from None
                if stamp.tzinfo is None:
                    raise DeveloperError("promotion_ci_response_invalid")
                selected.append(
                    CICheckSnapshot(
                        check_run_id=check_id,
                        name=name,
                        app_id=self.settings.promotion_ci_app_id,
                        app_slug=self.settings.promotion_ci_app_slug,
                        status="completed",
                        conclusion="success",
                        completed_at=stamp,
                    )
                )
        if len(selected) > 128:
            raise DeveloperError("promotion_ci_response_invalid")
        return PromotionCIVerification(
            verifier_version=CI_VERSION,
            repository=self.repository,
            pr_number=publication.pr_number,
            base_branch=promotion.target_base_branch,
            base_sha=promotion.review.base_sha,
            head_branch=promotion.branch,
            head_sha=publication.pr_head_sha,
            required_checks=required,
            checks=selected,
            verified_at=datetime.now(UTC),
        )
