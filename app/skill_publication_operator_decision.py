"""Append-only operator selection for a single approved Skill merge REVIEW.

This is a durable human decision about an exact claim/PR/CI observation,
not permission to invoke GitHub PUT or activate a skill. No model Tool or
external publication route is registered by this module.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.auth import OperatorPrincipal
from app.models import (
    SkillPublicationClaimRecord,
    SkillPublicationOperatorDecisionRecord,
    new_id,
)
from app.promotion_contracts import MergeRequest
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_intent import _digest
from app.skill_publication_remote_evidence import SkillPublicationRemoteEvidenceService

_CONFIRM = "APPROVE_EXACT_SKILL_PUBLICATION_REVIEW"
_SCHEMA = "zelo-skill-publication-operator-decision-v1"


def _decision_material(fields: dict) -> dict:
    return {
        "schema_version": _SCHEMA,
        "claim_id": fields["claim_id"],
        "proposal_id": fields["proposal_id"],
        "claim_fingerprint": fields["claim_fingerprint"],
        "repository": fields["repository"],
        "pr_number": fields["pr_number"],
        "pr_head_sha": fields["pr_head_sha"],
        "pr_base_sha": fields["pr_base_sha"],
        "merge_request_sha256": fields["merge_request_sha256"],
        "provider_verified_ci_sha256": fields["provider_verified_ci_sha256"],
        "operator_subject": fields["operator_subject"],
        "decision": fields["decision"],
    }


class SkillPublicationOperatorDecision(BaseModel):
    """Historical approval-for-review only; immutable and non-authoritative."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    schema_version: Literal["zelo-skill-publication-operator-decision-v1"] = _SCHEMA
    id: CanonicalUuid
    claim_id: CanonicalUuid
    proposal_id: CanonicalUuid
    claim_fingerprint: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pr_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_request_sha256: Sha256
    provider_verified_ci_sha256: Sha256
    decision_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    decision: Literal["approved_for_review"]
    remote_checked_at: datetime
    created_at: datetime
    publication_authority: Literal[False] = False
    external_merge_authority: Literal[False] = False
    runtime_authority: Literal[False] = False

    @model_validator(mode="after")
    def verify_frozen_decision(self):
        body = self.model_dump(mode="json")
        if self.decision_fingerprint != _digest(_decision_material(body)):
            raise ValueError("skill_publication_operator_decision_receipt_invalid")
        return self


class SkillPublicationOperatorDecisionService:
    """Persist exactly one owner-approved review after fresh read-only checks."""

    def __init__(self, remote: SkillPublicationRemoteEvidenceService) -> None:
        self.remote = remote
        self.store = remote.previews.claims.store

    @staticmethod
    def _response(row: SkillPublicationOperatorDecisionRecord) -> SkillPublicationOperatorDecision:
        return SkillPublicationOperatorDecision.model_validate(row)

    def get(
        self, claim_id: str, principal: OperatorPrincipal
    ) -> SkillPublicationOperatorDecision | None:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_operator_decision_operator_required")
        with self.store.database.read() as session:
            row = session.scalar(
                select(SkillPublicationOperatorDecisionRecord).where(
                    SkillPublicationOperatorDecisionRecord.claim_id == claim_id
                )
            )
            return self._response(row) if row is not None else None

    async def record_approval(
        self,
        claim_id: str,
        request: MergeRequest,
        confirmation: str,
        principal: OperatorPrincipal,
    ) -> tuple[SkillPublicationOperatorDecision, bool]:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_operator_decision_operator_required")
        if confirmation != _CONFIRM:
            raise ValueError("skill_publication_operator_decision_explicit_confirmation_required")
        try:
            # Revalidate nested copies, which can bypass Pydantic through model_copy.
            request = MergeRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError, ValueError):
            raise ValueError("skill_publication_operator_decision_request_invalid") from None

        # No GitHub writes. A successful read-only observation can become stale
        # during the response; it may never be used as a later merge ticket.
        observed = await self.remote.verify(claim_id, request, principal)

        # Serialized writer protects local Agent/spec/proposal/claim from
        # concurrent modification between revalidation and decision commit.
        with self.store.database.transaction() as session:
            current = self.remote.previews.preview(claim_id, request, principal)
            row_claim = session.get(SkillPublicationClaimRecord, claim_id)
            if (
                row_claim is None
                or current.claim_fingerprint != observed.claim_fingerprint
                or current.merge_request_sha256 != observed.merge_request_sha256
                or current.pr_head_sha != observed.pr_head_sha
                or current.pr_base_sha != observed.pr_base_sha
                or current.repository != observed.repository
                or current.pr_number != observed.pr_number
                or row_claim.claim_fingerprint != observed.claim_fingerprint
            ):
                raise ValueError("skill_publication_operator_decision_stale")

            material = _decision_material(
                {
                    "claim_id": claim_id,
                    "proposal_id": current.proposal_id,
                    "claim_fingerprint": observed.claim_fingerprint,
                    "repository": observed.repository,
                    "pr_number": observed.pr_number,
                    "pr_head_sha": observed.pr_head_sha,
                    "pr_base_sha": observed.pr_base_sha,
                    "merge_request_sha256": observed.merge_request_sha256,
                    "provider_verified_ci_sha256": observed.provider_verified_ci_sha256,
                    "operator_subject": principal.subject,
                    "decision": "approved_for_review",
                }
            )
            fingerprint = _digest(material)
            existing = session.scalar(
                select(SkillPublicationOperatorDecisionRecord).where(
                    SkillPublicationOperatorDecisionRecord.claim_id == claim_id
                )
            )
            if existing is not None:
                if existing.decision_fingerprint != fingerprint:
                    raise ValueError("skill_publication_operator_decision_conflict")
                return self._response(existing), False
            existing_pr = session.scalar(
                select(SkillPublicationOperatorDecisionRecord.id).where(
                    SkillPublicationOperatorDecisionRecord.repository == observed.repository,
                    SkillPublicationOperatorDecisionRecord.pr_number == observed.pr_number,
                )
            )
            if existing_pr is not None:
                raise ValueError("skill_publication_operator_decision_pr_already_selected")

            new = SkillPublicationOperatorDecisionRecord(
                id=new_id(),
                claim_id=claim_id,
                proposal_id=current.proposal_id,
                claim_fingerprint=observed.claim_fingerprint,
                repository=observed.repository,
                pr_number=observed.pr_number,
                pr_head_sha=observed.pr_head_sha,
                pr_base_sha=observed.pr_base_sha,
                merge_request_sha256=observed.merge_request_sha256,
                provider_verified_ci_sha256=observed.provider_verified_ci_sha256,
                decision_fingerprint=fingerprint,
                operator_subject=principal.subject,
                decision="approved_for_review",
                remote_checked_at=observed.checked_at,
                publication_authority=False,
                external_merge_authority=False,
                runtime_authority=False,
            )
            session.add(new)
            session.flush()
            return self._response(new), True
