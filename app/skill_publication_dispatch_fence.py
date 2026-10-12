"""Immutable, operator-selected *no-send* pre-dispatch Skill merge reservation.

Never issues GitHub writes. This is NOT a publication runner, send token,
runtime Skill activation or permission delegation. Every unknown outcome is
kept blocked until a distinct independently reviewed reconciliation stage.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.auth import OperatorPrincipal
from app.models import (
    SkillPublicationDispatchFenceRecord,
    SkillPublicationOperatorDecisionRecord,
    new_id,
)
from app.promotion_contracts import MergeRequest
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_blob_evidence import SkillPublicationBlobEvidenceService
from app.skill_publication_intent import _digest
from app.skill_publication_operator_decision import SkillPublicationOperatorDecisionService

_CONFIRM = "APPROVE_EXACT_SKILL_PUBLICATION_FENCE_NO_SEND"
_SCHEMA = "zelo-skill-publication-dispatch-fence-v1"


def _fence_material(fields: dict) -> dict:
    return {
        "schema_version": _SCHEMA,
        "decision_id": fields["decision_id"],
        "claim_id": fields["claim_id"],
        "decision_fingerprint": fields["decision_fingerprint"],
        "repository": fields["repository"],
        "pr_number": fields["pr_number"],
        "pr_head_sha": fields["pr_head_sha"],
        "pr_base_sha": fields["pr_base_sha"],
        "merge_request_sha256": fields["merge_request_sha256"],
        "artifact_sha256": fields["artifact_sha256"],
        "git_blob_oid": fields["git_blob_oid"],
        "operator_subject": fields["operator_subject"],
        "phase": fields["phase"],
    }


class SkillPublicationDispatchFence(BaseModel):
    """A durable frozen approval: there is categorically NO remote send."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    schema_version: Literal["zelo-skill-publication-dispatch-fence-v1"] = _SCHEMA
    id: CanonicalUuid
    decision_id: CanonicalUuid
    claim_id: CanonicalUuid
    decision_fingerprint: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pr_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_request_sha256: Sha256
    artifact_sha256: Sha256
    git_blob_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    fence_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    phase: Literal["reserved_no_send"]
    remote_verified_at: datetime
    created_at: datetime
    network_dispatch_authorized: Literal[False] = False
    network_dispatch_started: Literal[False] = False
    runtime_authority: Literal[False] = False

    @model_validator(mode="after")
    def _verify_fence_receipt(self):
        if self.fence_fingerprint != _digest(_fence_material(self.model_dump(mode="json"))):
            raise ValueError("skill_publication_dispatch_fence_receipt_invalid")
        return self


class SkillPublicationDispatchFenceService:
    """Fence one exact, approved PR without sending anything to GitHub."""

    def __init__(self, blob: SkillPublicationBlobEvidenceService):
        self.blob = blob
        self.decisions = SkillPublicationOperatorDecisionService(blob.remote)
        self.store = self.decisions.store

    @staticmethod
    def _response(row: SkillPublicationDispatchFenceRecord) -> SkillPublicationDispatchFence:
        return SkillPublicationDispatchFence.model_validate(row)

    def get(
        self, claim_id: str, principal: OperatorPrincipal
    ) -> SkillPublicationDispatchFence | None:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_dispatch_fence_operator_required")
        with self.store.database.read() as session:
            row = session.scalar(
                select(SkillPublicationDispatchFenceRecord).where(
                    SkillPublicationDispatchFenceRecord.claim_id == claim_id
                )
            )
            return self._response(row) if row is not None else None

    async def reserve(
        self,
        claim_id: str,
        request: MergeRequest,
        confirmation: str,
        principal: OperatorPrincipal,
    ) -> tuple[SkillPublicationDispatchFence, bool]:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_dispatch_fence_operator_required")
        if confirmation != _CONFIRM:
            raise ValueError("skill_publication_dispatch_fence_explicit_confirmation_required")
        try:
            request = MergeRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError, ValueError):
            raise ValueError("skill_publication_dispatch_fence_request_invalid") from None

        decision = self.decisions.get(claim_id, principal)
        if decision is None:
            raise ValueError("skill_publication_dispatch_fence_operator_decision_missing")
        observed = await self.blob.verify(claim_id, request, principal)
        if (
            decision.claim_id != observed.claim_id
            or decision.claim_fingerprint != observed.claim_fingerprint
            or decision.merge_request_sha256 != observed.merge_request_sha256
            or decision.repository != observed.repository
            or decision.pr_number != observed.pr_number
            or decision.pr_head_sha != observed.head_sha
            or decision.pr_base_sha != request.publication.pr_base_sha
            or decision.operator_subject != principal.subject
            or not observed.exact_head_tree_regular_file_mode_verified
            or not observed.git_blob_oid_and_sha256_verified
        ):
            raise ValueError("skill_publication_dispatch_fence_approval_mismatch")

        with self.store.database.transaction() as session:
            # Recheck the Core Agent, predecessor and immutable reviewed PR
            # under the one SQLite writer. A successful remote GET is never
            # assumed to remain fresh for a later external write.
            current = self.blob.remote.previews.preview(claim_id, request, principal)
            stored = session.get(SkillPublicationOperatorDecisionRecord, decision.id)
            if (
                stored is None
                or stored.claim_id != claim_id
                or stored.decision_fingerprint != decision.decision_fingerprint
                or current.merge_request_sha256 != observed.merge_request_sha256
                or current.claim_fingerprint != observed.claim_fingerprint
                or current.artifact_sha256 != observed.artifact_sha256
            ):
                raise ValueError("skill_publication_dispatch_fence_stale")

            material = _fence_material(
                {
                    "decision_id": decision.id,
                    "claim_id": claim_id,
                    "decision_fingerprint": decision.decision_fingerprint,
                    "repository": observed.repository,
                    "pr_number": observed.pr_number,
                    "pr_head_sha": observed.head_sha,
                    "pr_base_sha": request.publication.pr_base_sha,
                    "merge_request_sha256": observed.merge_request_sha256,
                    "artifact_sha256": observed.artifact_sha256,
                    "git_blob_oid": observed.blob_git_oid,
                    "operator_subject": principal.subject,
                    "phase": "reserved_no_send",
                }
            )
            digest = _digest(material)
            previous = session.scalar(
                select(SkillPublicationDispatchFenceRecord).where(
                    SkillPublicationDispatchFenceRecord.claim_id == claim_id
                )
            )
            if previous is not None:
                if previous.fence_fingerprint != digest:
                    raise ValueError("skill_publication_dispatch_fence_conflict")
                return self._response(previous), False
            if (
                session.scalar(
                    select(SkillPublicationDispatchFenceRecord.id).where(
                        SkillPublicationDispatchFenceRecord.repository == observed.repository,
                        SkillPublicationDispatchFenceRecord.pr_number == observed.pr_number,
                    )
                )
                is not None
            ):
                raise ValueError("skill_publication_dispatch_fence_pr_already_reserved")

            row = SkillPublicationDispatchFenceRecord(
                id=new_id(),
                decision_id=decision.id,
                claim_id=claim_id,
                decision_fingerprint=decision.decision_fingerprint,
                repository=observed.repository,
                pr_number=observed.pr_number,
                pr_head_sha=observed.head_sha,
                pr_base_sha=request.publication.pr_base_sha,
                merge_request_sha256=observed.merge_request_sha256,
                artifact_sha256=observed.artifact_sha256,
                git_blob_oid=observed.blob_git_oid,
                fence_fingerprint=digest,
                operator_subject=principal.subject,
                phase="reserved_no_send",
                remote_verified_at=observed.verified_at,
                network_dispatch_authorized=False,
                network_dispatch_started=False,
                runtime_authority=False,
            )
            session.add(row)
            session.flush()
            return self._response(row), True
