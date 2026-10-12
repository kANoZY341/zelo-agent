"""Non-authoritative evidence check for a skill-specific reviewed GitHub PR.

An exclusive local publication claim by itself is NOT a merge grant. This
validates one exact candidate artifact and existing CI identity offline,
without touching GitHub or altering any Core state.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.auth import OperatorPrincipal
from app.promotion_contracts import MergeRequest
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_claim import SkillPublicationClaimService
from app.skill_publication_intent import _digest, prepare_skill_publication_intent


class SkillPublicationMergePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-skill-publication-merge-preview-v1"] = (
        "zelo-skill-publication-merge-preview-v1"
    )
    claim_id: CanonicalUuid
    claim_fingerprint: Sha256
    proposal_id: CanonicalUuid
    intent_fingerprint: Sha256
    repository: str = Field(
        min_length=3, max_length=256, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pr_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    promotion_action_hash: Sha256
    merge_request_sha256: Sha256
    artifact_path: str
    artifact_sha256: Sha256
    artifact_bytes: int = Field(ge=1, le=65_536)
    candidate_contains_exactly_one_reviewed_regular_file: Literal[True] = True
    local_core_registry_and_review_revalidated: Literal[True] = True
    github_state_and_checks_not_refreshed: Literal[True] = True
    no_external_merge_or_execution_authority: Literal[True] = True
    runtime_skill_activation_authority: Literal[False] = False


class SkillPublicationMergePreviewService:
    """Require exact trusted local claim, reviewed PR and artifact identity."""

    def __init__(self, claims: SkillPublicationClaimService) -> None:
        self.claims = claims

    def preview(
        self,
        claim_id: str,
        request: MergeRequest,
        principal: OperatorPrincipal,
    ) -> SkillPublicationMergePreview:
        # The claim service owns and authenticates read access. Historical
        # claim presence is not itself permission to merge; rederive the
        # current accepted live intent under Core's independent policies.
        claim = self.claims.get(claim_id, principal)
        if claim is None:
            raise ValueError("skill_publication_merge_claim_not_found")
        intent = prepare_skill_publication_intent(self.claims.store, claim.proposal_id, principal)
        if (
            claim.intent_fingerprint != intent.intent_fingerprint
            or claim.review_fingerprint != intent.review_fingerprint
            or claim.artifact_sha256 != intent.artifact_sha256
            or claim.repository != intent.repository
            or claim.agent_instance_id != intent.agent_instance_id
            or claim.source_skill_id != intent.source_skill_id
            or claim.procedure_id != intent.procedure_id
            or claim.proposed_version != intent.proposed_version
            or claim.prior_revision_sha256 != intent.prior_revision_sha256
        ):
            raise ValueError("skill_publication_merge_claim_stale")

        reviewed = request.promotion.review
        manifest = reviewed.candidate_manifest
        if (
            len(manifest) != 1
            or reviewed.changed_file_count != 1
            or reviewed.ignored_artifact_summary
        ):
            raise ValueError("skill_publication_merge_manifest_not_exclusive")
        artifact = manifest[0]
        if (
            artifact.path != intent.artifact_path
            or artifact.operation != "add"
            or artifact.object_type != "file"
            or artifact.mode != "100644"
            or artifact.size != intent.artifact_bytes
            or artifact.sha256 != intent.artifact_sha256
        ):
            raise ValueError("skill_publication_merge_artifact_changed")

        # MergeRequest's own Pydantic validator binds publication + CI heads,
        # but this additional comparison binds that entire action to the one
        # reviewed, exclusively claimed learned procedure.
        if (
            request.promotion.repository != intent.repository
            or request.publication.repository != intent.repository
            or request.ci.repository != intent.repository
            or request.promotion.publisher.repository != intent.repository
            or request.publication.pr_base_sha != reviewed.base_sha
            or request.publication.pr_head_sha != request.ci.head_sha
            or request.ci.pr_number != request.publication.pr_number
            or request.merge_method != "squash"
        ):
            raise ValueError("skill_publication_merge_identity_changed")
        if (
            sorted(set(request.ci.required_checks)) != sorted(request.ci.required_checks)
            or not request.ci.required_checks
            or len({c.name for c in request.ci.checks}) != len(request.ci.checks)
            or any(
                not any(c.name == name for c in request.ci.checks)
                for name in request.ci.required_checks
            )
        ):
            raise ValueError("skill_publication_merge_ci_evidence_invalid")

        return SkillPublicationMergePreview(
            claim_id=claim.id,
            claim_fingerprint=claim.claim_fingerprint,
            proposal_id=claim.proposal_id,
            intent_fingerprint=claim.intent_fingerprint,
            repository=intent.repository,
            pr_number=request.publication.pr_number,
            pr_head_sha=request.publication.pr_head_sha,
            pr_base_sha=request.publication.pr_base_sha,
            promotion_action_hash=request.promotion_action_hash,
            merge_request_sha256=_digest(request.model_dump(mode="json")),
            artifact_path=intent.artifact_path,
            artifact_sha256=intent.artifact_sha256,
            artifact_bytes=intent.artifact_bytes,
        )
