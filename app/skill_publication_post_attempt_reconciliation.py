"""Durable, operator-only GET reconciliation after a single possible merge send.

A successful observation is historical evidence only. It cannot retry a merge,
activate a learned Skill, or grant runtime authority.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.models import (
    SkillPublicationMergeAttemptRecord,
    SkillPublicationPostAttemptReconciliationRecord,
    new_id,
)
from app.promotion_contracts import MergeRequest
from app.promotion_github import GitHubTransport, publisher_identity
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_dispatch_fence import SkillPublicationDispatchFenceService
from app.skill_publication_intent import _digest
from app.skill_publication_one_attempt import SkillPublicationMergeAttempt
from app.skill_publication_reconciliation import SkillPublicationReconciliationService

_SCHEMA = "zelo-skill-publication-post-attempt-reconciliation-v1"
POST_ATTEMPT_RECONCILIATION_CONFIRMATION = "RECONCILE_EXACT_SKILL_PUBLICATION_ATTEMPT_GET_ONLY"
_MAX_RECENT = 50


def _observation_material(fields: dict[str, object]) -> dict[str, object]:
    checked_at = fields["checked_at"]
    if type(checked_at) is str:
        checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
        raise ValueError("skill_publication_post_attempt_reconciliation_timestamp_invalid")
    return {
        "schema": _SCHEMA,
        "id": fields["id"],
        "attempt_id": fields["attempt_id"],
        "claim_id": fields["claim_id"],
        "attempt_fingerprint": fields["attempt_fingerprint"],
        "fence_fingerprint": fields["fence_fingerprint"],
        "merge_request_sha256": fields["merge_request_sha256"],
        "repository": fields["repository"],
        "pr_number": fields["pr_number"],
        "pr_head_sha": fields["pr_head_sha"],
        "pr_base_sha": fields["pr_base_sha"],
        "status": fields["status"],
        "merge_commit_sha": fields["merge_commit_sha"],
        "checked_at": checked_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "operator_subject": fields["operator_subject"],
    }


class SkillPublicationPostAttemptReconciliation(BaseModel):
    """Immutable remote observation; never a retry or activation ticket."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    schema_version: Literal["zelo-skill-publication-post-attempt-reconciliation-v1"] = _SCHEMA
    id: CanonicalUuid
    attempt_id: CanonicalUuid
    claim_id: CanonicalUuid
    attempt_fingerprint: Sha256
    fence_fingerprint: Sha256
    merge_request_sha256: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pr_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: Literal[
        "open_unmerged",
        "closed_unmerged",
        "merged_single_parent_content_observed",
    ]
    merge_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    checked_at: datetime
    observation_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    send_may_have_occurred: Literal[True]
    automatic_retry_allowed: Literal[False]
    runtime_authority: Literal[False]
    remote_actor_identity_unproven: Literal[True]
    remote_merge_method_unproven: Literal[True]
    no_skill_promotion_or_runtime_authority: Literal[True]
    created_at: datetime

    @model_validator(mode="after")
    def verify_observation(self):
        merged = self.status == "merged_single_parent_content_observed"
        if merged != (self.merge_commit_sha is not None):
            raise ValueError("skill_publication_post_attempt_reconciliation_status_invalid")
        if self.observation_fingerprint != _digest(
            _observation_material(self.model_dump(mode="json"))
        ):
            raise ValueError("skill_publication_post_attempt_reconciliation_receipt_invalid")
        return self


class SkillPublicationPostAttemptReconciliationService:
    """Record exact historical PR state after an existing one-shot attempt."""

    def __init__(
        self,
        fences: SkillPublicationDispatchFenceService,
        *,
        transport: GitHubTransport | None = None,
    ) -> None:
        self.fences = fences
        self.store = fences.store
        if transport is not None and transport is not fences.blob.transport:
            raise ValueError("skill_publication_post_attempt_reconciliation_transport_mismatch")
        self.history = SkillPublicationReconciliationService(
            fences, transport=transport if transport is not None else fences.blob.transport
        )

    @staticmethod
    def _owner(principal: OperatorPrincipal) -> None:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_post_attempt_reconciliation_operator_required")

    @classmethod
    def list_recent_for_store(
        cls, store, claim_id: str, principal: OperatorPrincipal
    ) -> list[SkillPublicationPostAttemptReconciliation]:
        cls._owner(principal)
        with store.database.read() as session:
            rows = session.scalars(
                select(SkillPublicationPostAttemptReconciliationRecord)
                .where(SkillPublicationPostAttemptReconciliationRecord.claim_id == claim_id)
                .order_by(
                    SkillPublicationPostAttemptReconciliationRecord.checked_at.desc(),
                    SkillPublicationPostAttemptReconciliationRecord.id.desc(),
                )
                .limit(_MAX_RECENT)
            ).all()
            return [SkillPublicationPostAttemptReconciliation.model_validate(row) for row in rows]

    def list_recent(
        self, claim_id: str, principal: OperatorPrincipal
    ) -> list[SkillPublicationPostAttemptReconciliation]:
        return self.list_recent_for_store(self.store, claim_id, principal)

    async def reconcile(
        self,
        claim_id: str,
        request: MergeRequest,
        confirmation: str,
        principal: OperatorPrincipal,
    ) -> SkillPublicationPostAttemptReconciliation:
        self._owner(principal)
        if confirmation != POST_ATTEMPT_RECONCILIATION_CONFIRMATION:
            raise ValueError("skill_publication_post_attempt_reconciliation_confirmation_required")
        try:
            request = MergeRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError, ValueError):
            raise ValueError(
                "skill_publication_post_attempt_reconciliation_request_invalid"
            ) from None

        # The durable one-shot record must exist before the first remote GET.
        with self.store.database.read() as session:
            attempt_row = session.scalar(
                select(SkillPublicationMergeAttemptRecord).where(
                    SkillPublicationMergeAttemptRecord.claim_id == claim_id
                )
            )
            if attempt_row is None:
                raise ValueError("skill_publication_post_attempt_reconciliation_attempt_missing")
            try:
                attempt = SkillPublicationMergeAttempt.model_validate(attempt_row)
            except ValidationError:
                raise ValueError(
                    "skill_publication_post_attempt_reconciliation_attempt_invalid"
                ) from None

        fence = self.fences.get(claim_id, principal)
        if fence is None:
            raise ValueError("skill_publication_post_attempt_reconciliation_fence_missing")
        if (
            attempt.claim_id != claim_id
            or attempt.fence_id != fence.id
            or attempt.fence_fingerprint != fence.fence_fingerprint
            or attempt.merge_request_sha256 != fence.merge_request_sha256
            or attempt.repository != fence.repository
            or attempt.pr_number != fence.pr_number
            or attempt.pr_head_sha != fence.pr_head_sha
            or attempt.pr_base_sha != fence.pr_base_sha
            or request.promotion_task_id != attempt.promotion_task_id
            or request.promotion.repository != attempt.repository
            or request.publication.repository != attempt.repository
            or request.publication.pr_number != attempt.pr_number
            or request.publication.pr_head_sha != attempt.pr_head_sha
            or request.publication.pr_base_sha != attempt.pr_base_sha
            or request.merge_method != "squash"
            or _digest(request.model_dump(mode="json")) != attempt.merge_request_sha256
        ):
            raise ValueError("skill_publication_post_attempt_reconciliation_request_changed")

        settings = self.fences.blob.remote.settings
        if (
            settings.promotion_repository != attempt.repository
            or publisher_identity(settings) != request.promotion.publisher
        ):
            raise DeveloperError("skill_publication_post_attempt_reconciliation_publisher_changed")

        # The existing verifier performs only GETs and proves the exact PR,
        # parent and reviewed blob. Its no-send record remains distinct from
        # this post-attempt observation.
        observed = await self.history.reconcile(claim_id, request, principal)
        observation_id = new_id()
        checked_at = observed.checked_at
        fields: dict[str, object] = {
            "id": observation_id,
            "attempt_id": attempt.id,
            "claim_id": attempt.claim_id,
            "attempt_fingerprint": attempt.attempt_fingerprint,
            "fence_fingerprint": attempt.fence_fingerprint,
            "merge_request_sha256": attempt.merge_request_sha256,
            "repository": attempt.repository,
            "pr_number": attempt.pr_number,
            "pr_head_sha": attempt.pr_head_sha,
            "pr_base_sha": attempt.pr_base_sha,
            "status": observed.status,
            "merge_commit_sha": observed.merge_commit_sha,
            "checked_at": checked_at,
            "operator_subject": principal.subject,
        }
        row = SkillPublicationPostAttemptReconciliationRecord(
            **fields,
            observation_fingerprint=_digest(_observation_material(fields)),
            send_may_have_occurred=True,
            automatic_retry_allowed=False,
            runtime_authority=False,
            remote_actor_identity_unproven=True,
            remote_merge_method_unproven=True,
            no_skill_promotion_or_runtime_authority=True,
        )
        with self.store.database.transaction() as session:
            session.add(row)
            session.flush()
            return SkillPublicationPostAttemptReconciliation.model_validate(row)
