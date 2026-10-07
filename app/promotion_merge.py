"""Exact-head, separately approved squash merge execution."""

import posixpath
from typing import Any

from app.config import Settings
from app.developer_process import DeveloperError
from app.promotion_ci import PromotionCIVerifier
from app.promotion_contracts import MergeRequest, MergeRunResult
from app.promotion_github import GhApiTransport, GitHubTransport, publisher_identity
from app.skill_learning import SKILL_PROCEDURE_ARTIFACT_ROOT

HEX = set("0123456789abcdef")


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeveloperError("promotion_merge_response_invalid")
    return value


def _sha(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in HEX for character in value)
    ):
        raise DeveloperError("promotion_merge_response_invalid")
    return value


def _publishes_unqualified_learned_skill(request: MergeRequest) -> bool:
    """Fail closed on the reserved learned-procedure repository subtree.

    A generic MergeRequest carries no Core-verified learned-skill proposal
    identity, promoted predecessor or exclusive publication reservation.
    Normalize unusual separator/dot spellings conservatively before checking
    the reserved path so the generic merge worker cannot publish these files.
    This is not a substitute for a later identity-bound merge authorization.
    """

    root = SKILL_PROCEDURE_ARTIFACT_ROOT
    parts = root.split("/")
    # A file/symlink replacing an ancestor directory would also remove or
    # shadow the protected subtree. Git review entries are file/symlink
    # operations, never safe recursive directory-only metadata changes.
    reserved_ancestors = {"/".join(parts[:depth]) for depth in range(1, len(parts))}
    for entry in request.promotion.review.candidate_manifest:
        normalized = posixpath.normpath(entry.path.replace("\\", "/")).lstrip("/")
        if (
            normalized in reserved_ancestors
            or normalized == root
            or normalized.startswith(root + "/")
        ):
            return True
    return False


class PromotionMergeRuntime:
    """Re-verify exact PR identity and CI immediately before a squash merge."""

    def __init__(
        self,
        settings: Settings,
        ci_verifier: PromotionCIVerifier,
        transport: GitHubTransport | None = None,
    ) -> None:
        if settings.promotion_repository is None:
            raise DeveloperError("promotion_repository_unconfigured")
        self.settings = settings
        self.repository = settings.promotion_repository
        self.ci_verifier = ci_verifier
        self.transport = transport or GhApiTransport(settings)

    def endpoint(self, suffix: str) -> str:
        if not suffix.startswith("/"):
            raise DeveloperError("promotion_merge_request_invalid")
        return f"/repos/{self.repository}{suffix}"

    def _result_from_merged_pr(
        self,
        request: MergeRequest,
        pr: dict[str, Any],
        *,
        reused: bool,
    ) -> MergeRunResult:
        head = _dict(pr.get("head"))
        base = _dict(pr.get("base"))
        head_repo = _dict(head.get("repo"))
        merge_sha = _sha(pr.get("merge_commit_sha"))
        if (
            pr.get("number") != request.publication.pr_number
            or pr.get("state") != "closed"
            or pr.get("merged") is not True
            or head_repo.get("full_name") != self.repository
            or head.get("ref") != request.publication.branch
            or _sha(head.get("sha")) != request.publication.pr_head_sha
            or base.get("ref") != request.publication.base_branch
            or _sha(base.get("sha")) != request.publication.pr_base_sha
        ):
            raise DeveloperError("promotion_merge_verification_failed")
        return MergeRunResult(
            repository=self.repository,
            pr_number=request.publication.pr_number,
            base_branch=request.publication.base_branch,
            base_sha=request.publication.pr_base_sha,
            head_branch=request.publication.branch,
            head_sha=request.publication.pr_head_sha,
            merge_method=request.merge_method,
            merge_commit_sha=merge_sha,
            reused=reused,
        )

    async def _current_pr(self, request: MergeRequest) -> dict[str, Any]:
        return _dict(
            await self.transport.request(
                "GET",
                self.endpoint(f"/pulls/{request.publication.pr_number}"),
            )
        )

    async def run(self, request: MergeRequest) -> MergeRunResult:
        if (
            request.promotion.repository != self.repository
            or request.promotion.publisher != publisher_identity(self.settings)
        ):
            raise DeveloperError("promotion_merge_publisher_changed")

        current = await self._current_pr(request)
        if current.get("state") == "closed":
            if current.get("merged") is not True:
                raise DeveloperError("promotion_merge_pr_closed")
            return self._result_from_merged_pr(request, current, reused=True)

        head = _dict(current.get("head"))
        base = _dict(current.get("base"))
        head_repo = _dict(head.get("repo"))
        if (
            current.get("number") != request.publication.pr_number
            or current.get("state") != "open"
            or current.get("merged") not in {False, None}
            or head_repo.get("full_name") != self.repository
            or head.get("ref") != request.publication.branch
            or _sha(head.get("sha")) != request.publication.pr_head_sha
            or base.get("ref") != request.publication.base_branch
            or _sha(base.get("sha")) != request.publication.pr_base_sha
        ):
            raise DeveloperError("promotion_merge_pr_changed")

        # Exact already-merged recovery above is read-only and historical.
        # New external publication of learned procedures must use a future
        # Core identity-bound and exclusive authorization path, never this
        # generic PR merge action, even if the generic CI is green.
        if _publishes_unqualified_learned_skill(request):
            raise DeveloperError("promotion_merge_skill_publication_unqualified")

        fresh_ci = await self.ci_verifier.verify(
            request.promotion,
            request.publication,
        )
        if (
            fresh_ci.repository != request.ci.repository
            or fresh_ci.pr_number != request.ci.pr_number
            or fresh_ci.base_branch != request.ci.base_branch
            or fresh_ci.base_sha != request.ci.base_sha
            or fresh_ci.head_branch != request.ci.head_branch
            or fresh_ci.head_sha != request.ci.head_sha
            or fresh_ci.required_checks != request.ci.required_checks
            or fresh_ci.checks != request.ci.checks
        ):
            raise DeveloperError("promotion_merge_ci_changed")

        body = {
            "sha": request.publication.pr_head_sha,
            "merge_method": request.merge_method,
            "commit_title": request.merge_commit_title,
            "commit_message": request.merge_commit_message,
        }
        response = _dict(
            await self.transport.request(
                "PUT",
                self.endpoint(f"/pulls/{request.publication.pr_number}/merge"),
                body,
            )
        )
        if response.get("merged") is not True:
            raise DeveloperError("promotion_merge_rejected")
        merge_sha = _sha(response.get("sha"))

        pr = await self._current_pr(request)
        result = self._result_from_merged_pr(request, pr, reused=False)
        if result.merge_commit_sha != merge_sha:
            raise DeveloperError("promotion_merge_verification_failed")
        return result
