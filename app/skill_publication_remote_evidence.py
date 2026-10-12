"""Fresh, read-only GitHub PR/CI evidence for an exclusively claimed Skill artifact.

This is NEVER a merge executor. The generic learned-skill publication refusal
remains in force; remote checks can become stale immediately after observation.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.auth import OperatorPrincipal
from app.config import Settings
from app.developer_process import DeveloperError
from app.promotion_ci import PromotionCIVerifier
from app.promotion_contracts import MergeRequest
from app.promotion_github import GhApiTransport, GitHubTransport, publisher_identity
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_intent import _digest
from app.skill_publication_merge_preview import SkillPublicationMergePreviewService


class SkillPublicationRemoteEvidence(BaseModel):
    """One bounded observation of fresh provider state, not future authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-skill-publication-remote-evidence-v1"] = (
        "zelo-skill-publication-remote-evidence-v1"
    )
    claim_id: CanonicalUuid
    claim_fingerprint: Sha256
    merge_request_sha256: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pr_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    checked_at: datetime
    provider_verified_ci_sha256: Sha256
    required_check_count: int = Field(ge=1, le=32)
    exact_pr_identity_checked_twice: Literal[True] = True
    current_required_ci_checks_green: Literal[True] = True
    no_write_ever_issued_to_github: Literal[True] = True
    current_observation_not_merge_authority: Literal[True] = True
    no_publication_or_execution_authority: Literal[True] = True
    runtime_skill_activation_authority: Literal[False] = False


class SkillPublicationRemoteEvidenceService:
    """Refresh existing Core CI controls with two exact PR reads; GET only.

    This must NOT be called to authorize a later PUT without a separate
    reviewed durable operator decision, pre-side-effect state transition and
    timeout/uncertain-result reconciliation.
    """

    def __init__(
        self,
        previews: SkillPublicationMergePreviewService,
        settings: Settings,
        *,
        transport: GitHubTransport | None = None,
        ci_verifier: PromotionCIVerifier | None = None,
    ) -> None:
        if settings.promotion_repository is None:
            raise DeveloperError("skill_publication_remote_repository_unconfigured")
        self.previews = previews
        self.settings = settings
        self.transport = transport if transport is not None else GhApiTransport(settings)
        self.ci_verifier = (
            ci_verifier
            if ci_verifier is not None
            else PromotionCIVerifier(settings, self.transport)
        )

    def _pr_endpoint(self, pr_number: int) -> str:
        return f"/repos/{self.settings.promotion_repository}/pulls/{pr_number}"

    @staticmethod
    def _verify_pr_again(request: MergeRequest, pr: object) -> None:
        if type(pr) is not dict:
            raise DeveloperError("skill_publication_remote_pr_invalid")
        head = pr.get("head")
        base = pr.get("base")
        if type(head) is not dict or type(base) is not dict:
            raise DeveloperError("skill_publication_remote_pr_invalid")
        head_repo = head.get("repo")
        if type(head_repo) is not dict:
            raise DeveloperError("skill_publication_remote_pr_invalid")

        if (
            type(pr.get("number")) is not int
            or pr["number"] != request.publication.pr_number
            or pr.get("state") != "open"
            or pr.get("merged") is not False
            or pr.get("draft") is not False
            or head_repo.get("full_name") != request.promotion.repository
            or head.get("ref") != request.publication.branch
            or head.get("sha") != request.publication.pr_head_sha
            or base.get("ref") != request.publication.base_branch
            or base.get("sha") != request.publication.pr_base_sha
        ):
            raise DeveloperError("skill_publication_remote_pr_changed")

    async def verify(
        self,
        claim_id: str,
        request: MergeRequest,
        principal: OperatorPrincipal,
    ) -> SkillPublicationRemoteEvidence:
        previous = self.previews.preview(claim_id, request, principal)
        if (
            previous.repository != self.settings.promotion_repository
            or request.promotion.publisher != publisher_identity(self.settings)
            or self.ci_verifier.repository != previous.repository
        ):
            raise DeveloperError("skill_publication_remote_publisher_changed")

        # The shared independent verifier checks the actual provider PR, exact
        # head and base refs, required provider/app-signed check runs, and
        # successful completion, instead of trusting the caller's CI snapshot.
        fresh = await self.ci_verifier.verify(request.promotion, request.publication)
        fresh_material = fresh.model_dump(mode="json", exclude={"verified_at"})
        frozen_material = request.ci.model_dump(mode="json", exclude={"verified_at"})
        if fresh_material != frozen_material:
            raise DeveloperError("skill_publication_remote_ci_changed")

        # Re-read the exact PR after CI. If the head, base, identity, draft or
        # state drifted during the verifier's GETs, deny this observation.
        current = await self.transport.request(
            "GET", self._pr_endpoint(request.publication.pr_number)
        )
        self._verify_pr_again(request, current)

        # A paused/revoked Agent or locally advanced Skill predecessor during
        # network I/O must never yield a positive observation of current Core.
        after = self.previews.preview(claim_id, request, principal)
        if previous != after:
            raise DeveloperError("skill_publication_remote_local_state_changed")

        return SkillPublicationRemoteEvidence(
            claim_id=previous.claim_id,
            claim_fingerprint=previous.claim_fingerprint,
            merge_request_sha256=previous.merge_request_sha256,
            repository=previous.repository,
            pr_number=previous.pr_number,
            pr_head_sha=previous.pr_head_sha,
            pr_base_sha=previous.pr_base_sha,
            checked_at=fresh.verified_at,
            provider_verified_ci_sha256=_digest(fresh_material),
            required_check_count=len(fresh.required_checks),
        )
