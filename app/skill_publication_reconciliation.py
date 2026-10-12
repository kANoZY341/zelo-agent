"""Read-only reconciliation of a fenced Skill publication PR with GitHub.

This does NOT initiate, retry, approve, unlock, or attest authorship of any
external merge. One remotely merged commit is historical observation only.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.promotion_contracts import MergeRequest
from app.promotion_github import GhApiTransport, GitHubTransport, publisher_identity
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_blob_evidence import SkillPublicationBlobEvidenceService
from app.skill_publication_dispatch_fence import SkillPublicationDispatchFenceService
from app.skill_publication_intent import _digest

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _is_sha(value: object) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


class SkillPublicationReconciliationEvidence(BaseModel):
    """Historical PR status, never a new permission or dispatch ticket."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-skill-publication-reconciliation-v1"] = (
        "zelo-skill-publication-reconciliation-v1"
    )
    claim_id: CanonicalUuid
    fence_fingerprint: Sha256
    merge_request_sha256: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    reviewed_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewed_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: Literal["open_unmerged", "closed_unmerged", "merged_single_parent_content_observed"]
    merge_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    checked_at: datetime
    source_fence_was_no_send: Literal[True] = True
    remote_actor_identity_unproven: Literal[True] = True
    remote_merge_method_unproven: Literal[True] = True
    cannot_retry_or_dispatch_github_merge: Literal[True] = True
    no_skill_promotion_or_runtime_authority: Literal[True] = True


class SkillPublicationReconciliationService:
    """Reconcile without modifying Core state, with only GitHub GET calls."""

    def __init__(
        self,
        fences: SkillPublicationDispatchFenceService,
        transport: GitHubTransport | None = None,
    ):
        self.fences = fences
        self.transport = (
            transport if transport is not None else GhApiTransport(fences.blob.remote.settings)
        )

    async def reconcile(
        self,
        claim_id: str,
        request: MergeRequest,
        principal: OperatorPrincipal,
    ) -> SkillPublicationReconciliationEvidence:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_reconciliation_operator_required")
        try:
            request = MergeRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError, ValueError):
            raise ValueError("skill_publication_reconciliation_request_invalid") from None

        # Historical evidence deliberately remains readable after Agent pause
        # or revocation. No current Agent/spec state or runtime authority is
        # inferred, and a fence explicitly records that NO send was started.
        fence = self.fences.get(claim_id, principal)
        if fence is None:
            raise ValueError("skill_publication_reconciliation_fence_missing")
        if (
            fence.claim_id != claim_id
            or fence.network_dispatch_authorized
            or fence.network_dispatch_started
            or fence.runtime_authority
            or fence.phase != "reserved_no_send"
            or request.promotion.repository != fence.repository
            or request.publication.repository != fence.repository
            or request.publication.pr_number != fence.pr_number
            or request.publication.pr_head_sha != fence.pr_head_sha
            or request.publication.pr_base_sha != fence.pr_base_sha
            or request.merge_method != "squash"
            or _digest(request.model_dump(mode="json")) != fence.merge_request_sha256
        ):
            raise ValueError("skill_publication_reconciliation_fence_identity_changed")

        # The transport does not attest its CLI or auth directory. Compare
        # the exact approved publisher before any GitHub request; historical
        # operator review remains possible after an Agent was revoked.
        settings = self.fences.blob.remote.settings
        if (
            settings.promotion_repository != fence.repository
            or publisher_identity(settings) != request.promotion.publisher
        ):
            raise DeveloperError("skill_publication_reconciliation_publisher_changed")

        endpoint = f"/repos/{fence.repository}/pulls/{fence.pr_number}"
        pr = await self.transport.request("GET", endpoint)
        if type(pr) is not dict or type(pr.get("head")) is not dict:
            raise DeveloperError("skill_publication_reconciliation_pr_invalid")
        head = pr["head"]
        base = pr.get("base")
        source_repo = head.get("repo")
        if (
            type(base) is not dict
            or type(source_repo) is not dict
            or type(pr.get("number")) is not int
            or pr["number"] != fence.pr_number
            or source_repo.get("full_name") != fence.repository
            or head.get("sha") != fence.pr_head_sha
            or head.get("ref") != request.publication.branch
            or base.get("ref") != request.publication.base_branch
        ):
            raise DeveloperError("skill_publication_reconciliation_pr_identity_changed")

        status = None
        merge_sha = None
        if pr.get("state") == "open" and pr.get("merged") is False:
            if pr.get("draft") is not False or base.get("sha") != fence.pr_base_sha:
                raise DeveloperError("skill_publication_reconciliation_pr_changed")
            status = "open_unmerged"
        elif pr.get("state") == "closed" and pr.get("merged") is False:
            status = "closed_unmerged"
        elif pr.get("state") == "closed" and pr.get("merged") is True:
            merge_sha = pr.get("merge_commit_sha")
            if not _is_sha(merge_sha):
                raise DeveloperError("skill_publication_reconciliation_merge_sha_invalid")
            # One parent establishes exact base ancestry, but cannot
            # distinguish squash from a one-commit rebase or fast-forward.
            # This is historical content/topology evidence ONLY.
            commit = await self.transport.request(
                "GET", f"/repos/{fence.repository}/git/commits/{merge_sha}"
            )
            if type(commit) is not dict or commit.get("sha") != merge_sha:
                raise DeveloperError("skill_publication_reconciliation_merge_commit_invalid")
            parents = commit.get("parents")
            if (
                type(parents) is not list
                or len(parents) != 1
                or type(parents[0]) is not dict
                or parents[0].get("sha") != fence.pr_base_sha
            ):
                raise DeveloperError("skill_publication_reconciliation_not_exact_squash")

            # An exact PR+parent SHA is not enough: the merged commit must
            # contain the same reviewed regular file and immutable blob OID.
            oid = await SkillPublicationBlobEvidenceService(
                self.fences.blob.remote, transport=self.transport
            )._tree_blob_oid(
                fence.repository,
                merge_sha,
                request.promotion.review.candidate_manifest[0].path,
            )
            if oid != fence.git_blob_oid:
                raise DeveloperError("skill_publication_reconciliation_merged_blob_changed")
            status = "merged_single_parent_content_observed"
        else:
            raise DeveloperError("skill_publication_reconciliation_pr_invalid")

        return SkillPublicationReconciliationEvidence(
            claim_id=claim_id,
            fence_fingerprint=fence.fence_fingerprint,
            merge_request_sha256=fence.merge_request_sha256,
            repository=fence.repository,
            pr_number=fence.pr_number,
            reviewed_head_sha=fence.pr_head_sha,
            reviewed_base_sha=fence.pr_base_sha,
            status=status,
            merge_commit_sha=merge_sha,
            checked_at=datetime.now(UTC),
        )
