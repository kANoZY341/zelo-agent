"""Inert, serialized Skill publication link from an exact positive merge receipt.

No route, Task, Tool, GitHub write, generic merge bypass or runtime resolver uses
this service. A history-only reconciliation cannot become a positive receipt.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.models import (
    SkillLearningPromotionLinkRecord,
    SkillPublicationClaimRecord,
    SkillPublicationDispatchFenceRecord,
    SkillPublicationMergeAttemptRecord,
    SkillPublicationMergeFailureRecord,
    SkillPublicationMergeReceiptRecord,
    SkillPublicationOperatorDecisionRecord,
    SkillPublicationPromotionReceiptLinkRecord,
    new_id,
)
from app.promotion_contracts import MergeRequest
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_blob_evidence import SkillPublicationBlobEvidenceService
from app.skill_publication_claim import SkillPublicationClaim
from app.skill_publication_dispatch_fence import SkillPublicationDispatchFence
from app.skill_publication_intent import _digest, prepare_skill_publication_intent
from app.skill_publication_one_attempt import (
    SkillPublicationMergeAttempt,
    SkillPublicationMergeReceipt,
    SkillPublicationOneAttemptService,
)
from app.skill_publication_operator_decision import SkillPublicationOperatorDecision

_SCHEMA = "zelo-skill-publication-promotion-receipt-link-v1"


class SkillPublicationPromotionReceiptLink(BaseModel):
    """Immutable local history; its presence is never Skill activation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    id: CanonicalUuid
    claim_id: CanonicalUuid
    proposal_id: CanonicalUuid
    receipt_id: CanonicalUuid
    agent_instance_id: CanonicalUuid
    source_skill_id: str
    procedure_id: str
    proposed_version: int = Field(ge=1, le=1_000_000)
    intent_fingerprint: Sha256
    receipt_fingerprint: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    revision_sha256: Sha256
    link_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    runtime_authority: Literal[False]
    created_at: datetime

    @model_validator(mode="after")
    def verify_digest(self):
        if self.link_fingerprint != _digest(_material(self.model_dump(mode="json"))):
            raise ValueError("skill_publication_promotion_link_digest_invalid")
        return self


def _material(fields: dict) -> dict:
    return {
        "schema": _SCHEMA,
        "claim_id": fields["claim_id"],
        "proposal_id": fields["proposal_id"],
        "receipt_id": fields["receipt_id"],
        "agent_instance_id": fields["agent_instance_id"],
        "source_skill_id": fields["source_skill_id"],
        "procedure_id": fields["procedure_id"],
        "proposed_version": fields["proposed_version"],
        "intent_fingerprint": fields["intent_fingerprint"],
        "receipt_fingerprint": fields["receipt_fingerprint"],
        "repository": fields["repository"],
        "pr_number": fields["pr_number"],
        "pr_head_sha": fields["pr_head_sha"],
        "merge_commit_sha": fields["merge_commit_sha"],
        "revision_sha256": fields["revision_sha256"],
        "operator_subject": fields["operator_subject"],
        "runtime_authority": False,
    }


class SkillPublicationPromotionReceiptLinkService:
    """Link only an affirmative single-attempt receipt under Core's one writer."""

    def __init__(self, one_attempt: SkillPublicationOneAttemptService) -> None:
        self.one_attempt = one_attempt
        self.store = one_attempt.store
        self.transport = one_attempt.transport

    @staticmethod
    def _owner(principal: OperatorPrincipal) -> None:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_promotion_link_operator_required")

    def get(self, claim_id: str, principal: OperatorPrincipal):
        self._owner(principal)
        with self.store.database.read() as session:
            row = session.scalar(
                select(SkillPublicationPromotionReceiptLinkRecord).where(
                    SkillPublicationPromotionReceiptLinkRecord.claim_id == claim_id
                )
            )
            return SkillPublicationPromotionReceiptLink.model_validate(row) if row else None

    def _local(self, session, claim_id, request, principal):
        preview = self.one_attempt.fences.blob.remote.previews.preview(claim_id, request, principal)
        claim_row = session.get(SkillPublicationClaimRecord, claim_id)
        if claim_row is None:
            raise ValueError("skill_publication_promotion_link_claim_missing")
        claim = SkillPublicationClaim.model_validate(claim_row)
        intent = prepare_skill_publication_intent(self.store, claim.proposal_id, principal)
        decision_row = session.scalar(
            select(SkillPublicationOperatorDecisionRecord).where(
                SkillPublicationOperatorDecisionRecord.claim_id == claim_id
            )
        )
        fence_row = session.scalar(
            select(SkillPublicationDispatchFenceRecord).where(
                SkillPublicationDispatchFenceRecord.claim_id == claim_id
            )
        )
        attempt_row = session.scalar(
            select(SkillPublicationMergeAttemptRecord).where(
                SkillPublicationMergeAttemptRecord.claim_id == claim_id
            )
        )
        receipt_row = session.scalar(
            select(SkillPublicationMergeReceiptRecord).where(
                SkillPublicationMergeReceiptRecord.claim_id == claim_id
            )
        )
        if any(row is None for row in (decision_row, fence_row, attempt_row, receipt_row)):
            raise ValueError("skill_publication_promotion_link_positive_receipt_missing")
        if (
            session.scalar(
                select(SkillPublicationMergeFailureRecord.id).where(
                    SkillPublicationMergeFailureRecord.claim_id == claim_id
                )
            )
            is not None
        ):
            raise ValueError("skill_publication_promotion_link_uncertain_outcome")
        decision = SkillPublicationOperatorDecision.model_validate(decision_row)
        fence = SkillPublicationDispatchFence.model_validate(fence_row)
        attempt = SkillPublicationMergeAttempt.model_validate(attempt_row)
        receipt = SkillPublicationMergeReceipt.model_validate(receipt_row)
        self.one_attempt._source_task(session, request)
        digest = _digest(request.model_dump(mode="json"))
        if (
            claim.intent_fingerprint != intent.intent_fingerprint
            or preview.claim_fingerprint != claim.claim_fingerprint
            or decision.claim_id != claim.id
            or decision.proposal_id != claim.proposal_id
            or decision.claim_fingerprint != claim.claim_fingerprint
            or fence.decision_id != decision.id
            or fence.decision_fingerprint != decision.decision_fingerprint
            or fence.claim_id != claim.id
            or attempt.fence_id != fence.id
            or attempt.claim_id != claim.id
            or attempt.fence_fingerprint != fence.fence_fingerprint
            or attempt.promotion_task_id != request.promotion_task_id
            or receipt.attempt_id != attempt.id
            or receipt.claim_id != claim.id
            or receipt.parent_base_sha != fence.pr_base_sha
            or receipt.merged_blob_oid != fence.git_blob_oid
            or fence.artifact_sha256 != intent.artifact_sha256
            or any(
                item != digest
                for item in (
                    preview.merge_request_sha256,
                    decision.merge_request_sha256,
                    fence.merge_request_sha256,
                    attempt.merge_request_sha256,
                )
            )
            or any(
                item != fence.repository
                for item in (claim.repository, decision.repository, attempt.repository)
            )
            or any(item != fence.pr_number for item in (decision.pr_number, attempt.pr_number))
            or any(
                item != fence.pr_head_sha for item in (decision.pr_head_sha, attempt.pr_head_sha)
            )
            or any(
                item != fence.pr_base_sha for item in (decision.pr_base_sha, attempt.pr_base_sha)
            )
            or request.publication.pr_head_sha != fence.pr_head_sha
            or request.publication.pr_base_sha != fence.pr_base_sha
        ):
            raise ValueError("skill_publication_promotion_link_lineage_changed")
        return intent, fence, receipt

    async def _provider(self, request, fence, receipt, artifact_path):
        repo = fence.repository
        prefix = f"/repos/{repo}"
        try:
            pr = await self.transport.request("GET", prefix + f"/pulls/{fence.pr_number}")
            head = pr.get("head") if type(pr) is dict else None
            base = pr.get("base") if type(pr) is dict else None
            if (
                type(pr) is not dict
                or pr.get("number") != fence.pr_number
                or pr.get("merged") is not True
                or pr.get("state") != "closed"
                or pr.get("merge_commit_sha") != receipt.merge_commit_sha
                or type(head) is not dict
                or head.get("sha") != fence.pr_head_sha
                or head.get("ref") != request.publication.branch
                or type(head.get("repo")) is not dict
                or head["repo"].get("full_name") != repo
                or type(base) is not dict
                or base.get("ref") != request.publication.base_branch
            ):
                raise DeveloperError("skill_publication_promotion_link_provider_changed")
            commit = await self.transport.request(
                "GET", prefix + f"/git/commits/{receipt.merge_commit_sha}"
            )
            parents = commit.get("parents") if type(commit) is dict else None
            if (
                type(commit) is not dict
                or commit.get("sha") != receipt.merge_commit_sha
                or type(parents) is not list
                or len(parents) != 1
                or type(parents[0]) is not dict
                or parents[0].get("sha") != fence.pr_base_sha
            ):
                raise DeveloperError("skill_publication_promotion_link_provider_changed")
            oid = await SkillPublicationBlobEvidenceService(
                self.one_attempt.fences.blob.remote, transport=self.transport
            )._tree_blob_oid(repo, receipt.merge_commit_sha, artifact_path)
            main = await self.transport.request("GET", prefix + "/branches/main")
            current = main.get("commit") if type(main) is dict else None
            if (
                oid != receipt.merged_blob_oid
                or type(main) is not dict
                or main.get("name") != "main"
                or type(current) is not dict
                or current.get("sha") != receipt.merge_commit_sha
            ):
                raise DeveloperError("skill_publication_promotion_link_provider_changed")
        except DeveloperError:
            raise
        except Exception:
            raise DeveloperError("skill_publication_promotion_link_provider_unavailable") from None

    async def record(self, claim_id: str, request: MergeRequest, principal: OperatorPrincipal):
        self._owner(principal)
        try:
            request = MergeRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError, ValueError):
            raise ValueError("skill_publication_promotion_link_request_invalid") from None
        with self.store.database.read() as session:
            intent, fence, receipt = self._local(session, claim_id, request, principal)
        await self._provider(request, fence, receipt, intent.artifact_path)

        # The remote read is immutable-commit evidence, not a remote lock.
        # BEGIN IMMEDIATE serializes competing local promotions and rechecks
        # live Agent, accepted review, predecessor and all receipt bindings.
        with self.store.database.transaction() as session:
            current_intent, current_fence, current_receipt = self._local(
                session, claim_id, request, principal
            )
            if (current_intent, current_fence, current_receipt) != (intent, fence, receipt):
                raise ValueError("skill_publication_promotion_link_stale")
            if (
                session.scalar(
                    select(SkillLearningPromotionLinkRecord.id).where(
                        SkillLearningPromotionLinkRecord.proposal_id == intent.proposal_id
                    )
                )
                is not None
            ):
                raise ValueError("skill_publication_promotion_link_other_lineage")
            values = {
                "claim_id": claim_id,
                "proposal_id": intent.proposal_id,
                "receipt_id": receipt.id,
                "agent_instance_id": intent.agent_instance_id,
                "source_skill_id": intent.source_skill_id,
                "procedure_id": intent.procedure_id,
                "proposed_version": intent.proposed_version,
                "intent_fingerprint": intent.intent_fingerprint,
                "receipt_fingerprint": receipt.receipt_fingerprint,
                "repository": fence.repository,
                "pr_number": fence.pr_number,
                "pr_head_sha": fence.pr_head_sha,
                "merge_commit_sha": receipt.merge_commit_sha,
                "revision_sha256": intent.revision_sha256,
                "operator_subject": principal.subject,
                "runtime_authority": False,
            }
            digest = _digest(_material(values))
            existing = session.scalar(
                select(SkillPublicationPromotionReceiptLinkRecord).where(
                    SkillPublicationPromotionReceiptLinkRecord.claim_id == claim_id
                )
            )
            if existing is not None:
                prior = SkillPublicationPromotionReceiptLink.model_validate(existing)
                if prior.link_fingerprint != digest:
                    raise ValueError("skill_publication_promotion_link_conflict")
                return prior, False
            # A prior publication link is deliberately not a trusted promoted
            # predecessor until a separate independently reviewed resolver
            # implementation can consume its complete provider receipt.
            if (
                session.scalar(
                    select(SkillPublicationPromotionReceiptLinkRecord.id).where(
                        SkillPublicationPromotionReceiptLinkRecord.agent_instance_id
                        == intent.agent_instance_id,
                        SkillPublicationPromotionReceiptLinkRecord.source_skill_id
                        == intent.source_skill_id,
                        SkillPublicationPromotionReceiptLinkRecord.procedure_id
                        == intent.procedure_id,
                    )
                )
                is not None
            ):
                raise ValueError("skill_publication_promotion_link_prior_unqualified")
            row = SkillPublicationPromotionReceiptLinkRecord(
                id=new_id(), **values, link_fingerprint=digest
            )
            session.add(row)
            session.flush()
            return SkillPublicationPromotionReceiptLink.model_validate(row), True
