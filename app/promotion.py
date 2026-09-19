"""Approval-bound promotion orchestration: local commit first, then exact GitHub publication."""

from collections.abc import Callable
from typing import Any

from app.developer_contracts import DeveloperRequest
from app.promotion_contracts import PromotionRequest, PromotionRunResult
from app.promotion_git import PromotionCommitter
from app.promotion_github import GitHubPromotionPublisher

Emit = Callable[[str, dict[str, Any]], None]


class PromotionRuntime:
    def __init__(
        self,
        committer: PromotionCommitter,
        publisher: GitHubPromotionPublisher,
    ) -> None:
        self.committer = committer
        self.publisher = publisher

    async def run(
        self,
        task_id: str,
        attempt_id: str,
        promotion: PromotionRequest,
        developer_request: DeveloperRequest,
        emit: Emit,
    ) -> dict[str, Any]:
        emit(
            "promotion_started",
            {"review_fingerprint": promotion.review.review_fingerprint},
        )
        local = await self.committer.create(
            task_id,
            attempt_id,
            promotion,
            developer_request,
        )
        emit(
            "promotion_commit_created",
            {
                "base_sha": local.base_sha,
                "tree_sha": local.tree_sha,
                "commit_sha": local.commit_sha,
                "branch": local.branch,
                "review_fingerprint": local.review_fingerprint,
            },
        )
        published = await self.publisher.publish(
            promotion,
            local,
            developer_request,
        )
        emit(
            "promotion_branch_pushed" if published.branch_created else "promotion_branch_reused",
            {
                "repository": published.repository,
                "branch": published.branch,
                "commit_sha": published.commit_sha,
            },
        )
        emit(
            "promotion_pr_created" if published.pr_created else "promotion_pr_reused",
            {
                "repository": published.repository,
                "pr_number": published.pr_number,
                "head_sha": published.pr_head_sha,
                "base_sha": published.pr_base_sha,
            },
        )
        return PromotionRunResult(commit=local, publication=published).model_dump(mode="json")
