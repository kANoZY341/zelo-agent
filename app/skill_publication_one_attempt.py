"""Opt-in, operator-only SINGLE external Skill merge attempt.

Never registered as a Core API/Tool/Task action. Requires a valid approved
source Promotion Task, complete reviewed Skill evidence and a durable
pre-network fence; after a single possible PUT no automatic retry exists.
No runtime Skill activation and no generic protected-file merger bypass.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.auth import OperatorPrincipal
from app.config import Settings
from app.developer_contracts import DeveloperRequest
from app.developer_process import DeveloperError
from app.models import (
    Approval,
    SkillPublicationMergeAttemptRecord,
    SkillPublicationMergeFailureRecord,
    SkillPublicationMergeReceiptRecord,
    Task,
    TaskAttempt,
    new_id,
)
from app.policy import State, action_hash
from app.promotion_contracts import MergeRequest, PromotionRequest, PromotionRunResult
from app.promotion_github import GitHubTransport, publisher_identity
from app.promotion_review import candidate_manifest_content_sha256
from app.schemas import DeveloperRunResult
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_blob_evidence import SkillPublicationBlobEvidenceService
from app.skill_publication_dispatch_fence import SkillPublicationDispatchFenceService
from app.skill_publication_intent import _digest
from app.skill_publication_review_context import skill_publication_review_instruction

_CONFIRM = "MERGE_EXACT_APPROVED_SKILL_ONCE_WITHOUT_RETRY"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SCHEMA = "zelo-skill-publication-single-attempt-v1"


class SkillPublicationMergeAttempt(BaseModel):
    """Immutable history that blocks all subsequent sends, including after crash."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    id: CanonicalUuid
    fence_id: CanonicalUuid
    claim_id: CanonicalUuid
    promotion_task_id: str = Field(min_length=36, max_length=36)
    repository: str
    pr_number: int = Field(ge=1)
    pr_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pr_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    fence_fingerprint: Sha256
    merge_request_sha256: Sha256
    attempt_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    phase: Literal["send_outcome_unresolved"]
    send_may_have_occurred: Literal[True]
    automatic_retry_allowed: Literal[False]
    runtime_authority: Literal[False]
    created_at: datetime

    @model_validator(mode="after")
    def verify_fingerprint(self):
        expected = _digest(
            {
                "schema": _SCHEMA,
                "fence_id": self.fence_id,
                "claim_id": self.claim_id,
                "promotion_task_id": self.promotion_task_id,
                "repository": self.repository,
                "pr_number": self.pr_number,
                "pr_head_sha": self.pr_head_sha,
                "pr_base_sha": self.pr_base_sha,
                "fence_fingerprint": self.fence_fingerprint,
                "merge_request_sha256": self.merge_request_sha256,
                "operator_subject": self.operator_subject,
            }
        )
        if self.attempt_fingerprint != expected:
            raise ValueError("skill_publication_single_attempt_receipt_invalid")
        return self


class SkillPublicationMergeReceipt(BaseModel):
    """Proved exact provider merge response; NOT learned-Skill activation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    id: CanonicalUuid
    attempt_id: CanonicalUuid
    claim_id: CanonicalUuid
    merge_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    parent_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    merged_blob_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    receipt_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    response_confirmed: Literal[True]
    runtime_authority: Literal[False]
    created_at: datetime

    @model_validator(mode="after")
    def verify_receipt(self):
        expected = _digest(
            {
                "schema": "zelo-skill-publication-merge-receipt-v1",
                "attempt_id": self.attempt_id,
                "claim_id": self.claim_id,
                "merge_commit_sha": self.merge_commit_sha,
                "parent_base_sha": self.parent_base_sha,
                "merged_blob_oid": self.merged_blob_oid,
                "operator_subject": self.operator_subject,
            }
        )
        if self.receipt_fingerprint != expected:
            raise ValueError("skill_publication_single_attempt_merge_receipt_invalid")
        return self


class SkillPublicationMergeFailure(BaseModel):
    """Read-only allowlisted incident evidence; never unblocks retry."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, from_attributes=True)

    id: CanonicalUuid
    attempt_id: CanonicalUuid
    claim_id: CanonicalUuid
    phase: Literal[
        "put_request", "put_response", "post_pr", "post_commit", "post_tree", "receipt_persistence"
    ]
    reason: Literal[
        "provider_exception",
        "response_invalid",
        "pr_mismatch",
        "parent_mismatch",
        "blob_mismatch",
        "storage_failure",
        "unclassified",
    ]
    failure_fingerprint: Sha256
    operator_subject: Literal["ahmad"]
    automatic_retry_allowed: Literal[False]
    runtime_authority: Literal[False]
    created_at: datetime

    @model_validator(mode="after")
    def verify_incident_digest(self):
        expected = _digest(
            {
                "schema": "zelo-skill-publication-merge-failure-v1",
                "attempt_id": self.attempt_id,
                "claim_id": self.claim_id,
                "phase": self.phase,
                "reason": self.reason,
                "operator_subject": self.operator_subject,
            }
        )
        if self.failure_fingerprint != expected:
            raise ValueError("skill_publication_single_attempt_failure_receipt_invalid")
        return self


class SkillPublicationOneAttemptService:
    """Never added to FastAPI routers or the model ToolRegistry."""

    def __init__(
        self,
        fences: SkillPublicationDispatchFenceService,
        settings: Settings,
        *,
        transport: GitHubTransport | None = None,
    ) -> None:
        self.fences = fences
        self.store = fences.store
        self.settings = settings
        # Use the same attested provider path for GET evidence and the one PUT;
        # never let a separately injected transport silently change credentials
        # or target after the source evidence has been reviewed.
        if transport is not None and transport is not fences.blob.transport:
            raise ValueError("skill_publication_single_attempt_transport_mismatch")
        self.transport = fences.blob.transport

    async def _require_protected_main(self, repository: str) -> Literal["team", "solo"]:
        """Require provider-enforced strict checks before any merge side effect.

        A caller-supplied config flag is not a substitute for an attested
        GitHub branch policy. A private repository without protection API
        access (HTTP 403) must remain unpublishable through this service.
        """
        endpoint = f"/repos/{repository}/branches/main/protection"
        try:
            policy = await self.transport.request("GET", endpoint)
        except Exception:
            raise DeveloperError("skill_publication_main_protection_unavailable") from None
        if type(policy) is not dict:
            raise DeveloperError("skill_publication_main_protection_unqualified")
        status = policy.get("required_status_checks")
        admin = policy.get("enforce_admins")
        force = policy.get("allow_force_pushes")
        deletion = policy.get("allow_deletions")
        if (
            not self.settings.promotion_required_checks
            or len(self.settings.promotion_required_checks) > 32
            or len(set(self.settings.promotion_required_checks))
            != len(self.settings.promotion_required_checks)
            or type(status) is not dict
            or status.get("strict") is not True
            or type(status.get("checks")) is not list
            or len(status["checks"]) > 32
            or type(admin) is not dict
            or admin.get("enabled") is not True
            or type(force) is not dict
            or force.get("enabled") is not False
            or type(deletion) is not dict
            or deletion.get("enabled") is not False
        ):
            raise DeveloperError("skill_publication_main_protection_unqualified")
        for name in self.settings.promotion_required_checks:
            matches = [
                check
                for check in status["checks"]
                if type(check) is dict and check.get("context") == name
            ]
            if (
                len(matches) != 1
                or type(matches[0].get("app_id")) is not int
                or matches[0]["app_id"] != self.settings.promotion_ci_app_id
            ):
                raise DeveloperError("skill_publication_main_protection_unqualified")
        reviews = policy.get("required_pull_request_reviews")
        if (
            type(reviews) is not dict
            or type(reviews.get("required_approving_review_count")) is not int
            or reviews.get("dismiss_stale_reviews") is not True
        ):
            raise DeveloperError("skill_publication_main_protection_unqualified")
        count = reviews["required_approving_review_count"]
        if 1 <= count <= 6 and reviews.get("require_last_push_approval") is True:
            mode: Literal["team", "solo"] = "team"
            # GitHub returns null for an empty bypass list on personal repositories.
            # An omitted or partial bypass policy cannot qualify this team route.
            if "bypass_pull_request_allowances" not in reviews:
                raise DeveloperError("skill_publication_main_protection_unqualified")
        elif (
            count == 0
            and reviews.get("require_last_push_approval") is False
            and self.settings.skill_publication_solo_owner_mode_enabled is True
        ):
            # A solo owner cannot approve their own GitHub PR. This separate
            # policy requires a clean, independently executed Coding review of
            # the exact Developer candidate at each local send use point.
            mode = "solo"
        else:
            raise DeveloperError("skill_publication_main_protection_unqualified")
        bypass = reviews.get("bypass_pull_request_allowances")
        if bypass is not None and (
            type(bypass) is not dict
            or any(
                type(bypass.get(field)) is not list or bypass[field]
                for field in ("users", "teams", "apps")
            )
        ):
            raise DeveloperError("skill_publication_main_protection_unqualified")
        return mode

    def get_attempt(
        self, claim_id: str, principal: OperatorPrincipal
    ) -> SkillPublicationMergeAttempt | None:
        self._owner(principal)
        with self.store.database.read() as session:
            row = session.scalar(
                select(SkillPublicationMergeAttemptRecord).where(
                    SkillPublicationMergeAttemptRecord.claim_id == claim_id
                )
            )
            return SkillPublicationMergeAttempt.model_validate(row) if row else None

    def get_receipt(
        self, claim_id: str, principal: OperatorPrincipal
    ) -> SkillPublicationMergeReceipt | None:
        self._owner(principal)
        with self.store.database.read() as session:
            row = session.scalar(
                select(SkillPublicationMergeReceiptRecord).where(
                    SkillPublicationMergeReceiptRecord.claim_id == claim_id
                )
            )
            return SkillPublicationMergeReceipt.model_validate(row) if row else None

    def get_failure(
        self, claim_id: str, principal: OperatorPrincipal
    ) -> SkillPublicationMergeFailure | None:
        self._owner(principal)
        with self.store.database.read() as session:
            row = session.scalar(
                select(SkillPublicationMergeFailureRecord).where(
                    SkillPublicationMergeFailureRecord.claim_id == claim_id
                )
            )
            return SkillPublicationMergeFailure.model_validate(row) if row else None

    @staticmethod
    def _owner(principal: OperatorPrincipal) -> None:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_single_attempt_operator_required")

    @staticmethod
    def _source_task(
        session, request: MergeRequest, *, solo: bool = False, review_context: str | None = None
    ) -> None:
        """Reuse the existing Promotion -> Merge chain, not an invented source."""
        task = session.get(Task, request.promotion_task_id)
        if (
            task is None
            or task.kind != "promotion_run"
            or task.status != State.SUCCEEDED
            or task.cancel_requested is not False
            or task.action_version != request.promotion_action_version
            or task.action_hash != request.promotion_action_hash
            or task.promotion_spec is None
            or task.result is None
        ):
            raise ValueError("skill_publication_single_attempt_source_task_invalid")
        attempt = session.scalar(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task.id)
            .order_by(TaskAttempt.number.desc())
            .limit(1)
        )
        approval = session.scalar(
            select(Approval)
            .where(Approval.task_id == task.id)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        if (
            attempt is None
            or attempt.outcome != State.SUCCEEDED
            or attempt.error is not None
            or attempt.result != task.result
            or approval is None
            or approval.status != "approved"
            or approval.action_hash != task.action_hash
            or approval.action_version != task.action_version
        ):
            raise ValueError("skill_publication_single_attempt_source_approval_invalid")
        try:
            recorded = PromotionRequest.model_validate(task.promotion_spec)
            result = PromotionRunResult.model_validate(task.result)
        except (ValidationError, ValueError):
            raise ValueError("skill_publication_single_attempt_source_result_invalid") from None
        if (
            recorded != request.promotion
            or result.publication != request.publication
            or result.commit.commit_sha != request.publication.pr_head_sha
            or result.commit.base_sha != request.publication.pr_base_sha
        ):
            raise ValueError("skill_publication_single_attempt_source_identity_changed")
        if solo:
            SkillPublicationOneAttemptService._solo_review(session, request, review_context)

    @staticmethod
    def _solo_review(session, request: MergeRequest, review_context: str | None) -> None:
        """Require a successful distinct Coding review of these exact candidate bytes."""
        review = request.promotion.review
        task = session.get(Task, review.developer_task_id)
        if (
            task is None
            or task.kind != "developer_run"
            or task.status != State.SUCCEEDED
            or task.cancel_requested is not False
            or task.developer_spec is None
            or task.result is None
        ):
            raise ValueError("skill_publication_solo_review_invalid")
        approval = session.scalar(
            select(Approval)
            .where(Approval.task_id == task.id)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        if (
            approval is None
            or approval.status != "approved"
            or approval.action_version != task.action_version
            or approval.action_hash != task.action_hash
            or task.action_hash
            != action_hash(
                "developer_run",
                task.payload,
                task.timeout_seconds,
                task.action_version,
                task.developer_spec,
            )
        ):
            raise ValueError("skill_publication_solo_review_invalid")
        latest = session.scalar(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task.id)
            .order_by(TaskAttempt.number.desc())
            .limit(1)
        )
        if (
            latest is None
            or latest.id != review.developer_attempt_id
            or latest.outcome != State.SUCCEEDED
            or latest.error is not None
            or latest.result != task.result
        ):
            raise ValueError("skill_publication_solo_review_invalid")
        try:
            spec = DeveloperRequest.model_validate(task.developer_spec)
            result = DeveloperRunResult.model_validate(task.result)
        except (ValidationError, ValueError):
            raise ValueError("skill_publication_solo_review_invalid") from None
        binding = spec.reviewer
        independent = result.independent_review
        producer = spec.worker_selection.descriptor
        digest = candidate_manifest_content_sha256(review.candidate_manifest)
        changed_paths = set(result.changed_files)
        ignored_paths = {item["path"] for item in result.ignored_generated_files if "path" in item}
        manifest_paths = {item.path for item in review.candidate_manifest}
        if (
            binding is None
            or independent is None
            or task.payload.get("instruction") != spec.instruction
            or spec.review_instruction is None
            or spec.review_instruction != review_context
            or spec.source_repository != review.source_repository
            or spec.base_sha != review.base_sha
            or spec.worker != review.worker_identity
            or spec.validation_identity != review.validation_identity
            or spec.validation_profile != review.validation_profile
            or spec.validation_version != review.validation_version
            or spec.sandbox_version != review.sandbox_version
            or producer.role != "developer"
            or producer.implementation != spec.worker.implementation
            or binding.worker_selection.requested_role != "reviewer"
            or binding.worker_selection.descriptor.role != "reviewer"
            or binding.worker_selection.descriptor.implementation != binding.worker.implementation
            or "code_review" not in binding.worker_selection.descriptor.capabilities
            or binding.worker_selection.authority_profile != "workspace-read-no-network-v1"
            or producer.worker_id == binding.worker_selection.descriptor.worker_id
            or producer.worker_id not in binding.worker_selection.excluded_worker_ids
            or spec.worker == binding.worker
            or result.worker != spec.worker.implementation
            or result.worker_version != spec.worker.cli_version
            or result.worker_config_version != spec.worker.config_version
            or result.base_sha != review.base_sha
            or result.head != review.head_sha
            or result.worktree != review.workspace
            or result.worker_exit_code != 0
            or result.head_changed is not False
            or result.nonfatal_error_items != 0
            or tuple(item.identifier for item in result.validation)
            != ("ruff_check", "ruff_format", "pytest", "git_diff_check")
            or any(item.exit_code != 0 or item.error is not None for item in result.validation)
            or result.cancelled
            or result.error is not None
            or result.boundary_error is not None
            or result.protocol_diagnostic is not None
            or result.original_error is not None
            or result.validation_passed is not True
            or result.candidate_content_sha256 != digest
            or len(changed_paths) != len(result.changed_files)
            or len(ignored_paths) != len(result.ignored_generated_files)
            or not ignored_paths.issubset(changed_paths)
            or manifest_paths & ignored_paths
            or manifest_paths != changed_paths - ignored_paths
            or independent.worker_id != binding.worker_selection.descriptor.worker_id
            or independent.implementation != binding.worker.implementation
            or independent.worker_version != binding.worker.cli_version
            or independent.candidate_content_sha256 != digest
            or independent.report.findings
        ):
            raise ValueError("skill_publication_solo_review_invalid")

    async def run_once(
        self,
        claim_id: str,
        request: MergeRequest,
        confirmation: str,
        principal: OperatorPrincipal,
    ) -> SkillPublicationMergeReceipt:
        self._owner(principal)
        if self.settings.skill_publication_external_merge_enabled is not True:
            raise PermissionError("skill_publication_single_attempt_disabled")
        if confirmation != _CONFIRM:
            raise ValueError("skill_publication_single_attempt_confirmation_required")
        try:
            request = MergeRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, ValidationError, ValueError):
            raise ValueError("skill_publication_single_attempt_request_invalid") from None

        fence = self.fences.get(claim_id, principal)
        if fence is None or fence.phase != "reserved_no_send":
            raise ValueError("skill_publication_single_attempt_fence_missing")
        if (
            request.promotion.repository != fence.repository
            or request.publication.repository != fence.repository
            or request.publication.pr_number != fence.pr_number
            or request.publication.pr_head_sha != fence.pr_head_sha
            or request.publication.pr_base_sha != fence.pr_base_sha
            or _digest(request.model_dump(mode="json")) != fence.merge_request_sha256
            or request.merge_method != "squash"
        ):
            raise ValueError("skill_publication_single_attempt_request_changed")
        if (
            publisher_identity(self.settings) != request.promotion.publisher
            or self.settings.promotion_repository != fence.repository
        ):
            raise DeveloperError("skill_publication_single_attempt_publisher_changed")
        with self.store.database.read() as session:
            self._source_task(session, request)
            if (
                session.scalar(
                    select(SkillPublicationMergeAttemptRecord.id).where(
                        SkillPublicationMergeAttemptRecord.claim_id == claim_id
                    )
                )
                is not None
            ):
                raise ValueError("skill_publication_single_attempt_already_recorded")

        # Bounded immutable source/CI/PR checks through GET only.
        # No sender authority is inferred from a successful observation.
        observed = await self.fences.blob.verify(claim_id, request, principal)
        if (
            observed.claim_id != claim_id
            or observed.claim_fingerprint
            != self.fences.blob.remote.previews.preview(
                claim_id, request, principal
            ).claim_fingerprint
            or observed.merge_request_sha256 != fence.merge_request_sha256
            or observed.repository != fence.repository
            or observed.pr_number != fence.pr_number
            or observed.head_sha != fence.pr_head_sha
            or observed.blob_git_oid != fence.git_blob_oid
            or observed.artifact_sha256 != fence.artifact_sha256
        ):
            raise ValueError("skill_publication_single_attempt_preflight_changed")

        # Provider-side strict CI enforcement is mandatory: a configured
        # switch and approved PR/head alone cannot serialize base advancement.
        # No branch protection API access means zero GitHub merge attempts.
        policy_mode = await self._require_protected_main(fence.repository)

        # This is the irreversible retry fence. Commit before the first PUT.
        # If a crash happens after commit but before sending, treat its
        # outcome as UNKNOWN and *never* send a second request.
        with self.store.database.transaction() as session:
            current = self.fences.blob.remote.previews.preview(claim_id, request, principal)
            self._source_task(
                session,
                request,
                solo=policy_mode == "solo",
                review_context=skill_publication_review_instruction(current)
                if policy_mode == "solo"
                else None,
            )
            if (
                current.claim_fingerprint != observed.claim_fingerprint
                or current.merge_request_sha256 != observed.merge_request_sha256
                or current.artifact_sha256 != observed.artifact_sha256
            ):
                raise ValueError("skill_publication_single_attempt_stale")
            if (
                session.scalar(
                    select(SkillPublicationMergeAttemptRecord.id).where(
                        SkillPublicationMergeAttemptRecord.claim_id == claim_id
                    )
                )
                is not None
            ):
                raise ValueError("skill_publication_single_attempt_already_recorded")
            material = {
                "schema": _SCHEMA,
                "fence_id": fence.id,
                "claim_id": claim_id,
                "promotion_task_id": request.promotion_task_id,
                "repository": fence.repository,
                "pr_number": fence.pr_number,
                "pr_head_sha": fence.pr_head_sha,
                "pr_base_sha": fence.pr_base_sha,
                "fence_fingerprint": fence.fence_fingerprint,
                "merge_request_sha256": fence.merge_request_sha256,
                "operator_subject": principal.subject,
            }
            row = SkillPublicationMergeAttemptRecord(
                id=new_id(),
                fence_id=fence.id,
                claim_id=claim_id,
                promotion_task_id=request.promotion_task_id,
                repository=fence.repository,
                pr_number=fence.pr_number,
                pr_head_sha=fence.pr_head_sha,
                pr_base_sha=fence.pr_base_sha,
                fence_fingerprint=fence.fence_fingerprint,
                merge_request_sha256=fence.merge_request_sha256,
                attempt_fingerprint=_digest(material),
                operator_subject=principal.subject,
                phase="send_outcome_unresolved",
                send_may_have_occurred=True,
                automatic_retry_allowed=False,
                runtime_authority=False,
            )
            session.add(row)
            session.flush()
            attempt_id = row.id

        # Agent lifecycle or source authority may change after the durable
        # attempt commits. Recheck before dispatch; the attempt stays terminal
        # even when this later check refuses the only possible send.
        current = self.fences.blob.remote.previews.preview(claim_id, request, principal)
        with self.store.database.read() as session:
            self._source_task(
                session,
                request,
                solo=policy_mode == "solo",
                review_context=skill_publication_review_instruction(current)
                if policy_mode == "solo"
                else None,
            )
        if (
            current.claim_fingerprint != observed.claim_fingerprint
            or current.merge_request_sha256 != observed.merge_request_sha256
            or current.artifact_sha256 != observed.artifact_sha256
        ):
            raise ValueError("skill_publication_single_attempt_stale")

        # EXACTLY ONE possible externally visible write after durable commit.
        # There is intentionally no retry loop, public API or Tool registration.
        endpoint = f"/repos/{fence.repository}/pulls/{fence.pr_number}"
        phase = "put_request"
        try:
            response = await self.transport.request(
                "PUT",
                endpoint + "/merge",
                {
                    "sha": fence.pr_head_sha,
                    "merge_method": "squash",
                    "commit_title": request.merge_commit_title,
                    "commit_message": request.merge_commit_message,
                },
            )
            phase = "put_response"
            if (
                type(response) is not dict
                or response.get("merged") is not True
                or type(response.get("sha")) is not str
                or _SHA.fullmatch(response["sha"]) is None
            ):
                raise DeveloperError("skill_publication_single_attempt_merge_response_unverified")
            merge_sha = response["sha"]
            phase = "post_pr"
            current = await self.transport.request("GET", endpoint)
            if (
                type(current) is not dict
                or current.get("state") != "closed"
                or current.get("merged") is not True
                or current.get("merge_commit_sha") != merge_sha
                or type(current.get("head")) is not dict
                or current["head"].get("sha") != fence.pr_head_sha
                or current["head"].get("ref") != request.publication.branch
                or type(current["head"].get("repo")) is not dict
                or current["head"]["repo"].get("full_name") != fence.repository
                or type(current.get("base")) is not dict
                or current["base"].get("ref") != request.publication.base_branch
            ):
                raise DeveloperError("skill_publication_single_attempt_postmerge_pr_invalid")
            phase = "post_commit"
            commit = await self.transport.request(
                "GET", f"/repos/{fence.repository}/git/commits/{merge_sha}"
            )
            parents = commit.get("parents") if type(commit) is dict else None
            if (
                type(commit) is not dict
                or commit.get("sha") != merge_sha
                or type(parents) is not list
                or len(parents) != 1
                or type(parents[0]) is not dict
                or parents[0].get("sha") != fence.pr_base_sha
            ):
                raise DeveloperError("skill_publication_single_attempt_postmerge_parent_invalid")
            phase = "post_tree"
            merged_oid = await SkillPublicationBlobEvidenceService(
                self.fences.blob.remote, transport=self.transport
            )._tree_blob_oid(
                fence.repository, merge_sha, request.promotion.review.candidate_manifest[0].path
            )
            if merged_oid != fence.git_blob_oid:
                raise DeveloperError("skill_publication_single_attempt_postmerge_blob_invalid")
            receipt_material = {
                "schema": "zelo-skill-publication-merge-receipt-v1",
                "attempt_id": attempt_id,
                "claim_id": claim_id,
                "merge_commit_sha": merge_sha,
                "parent_base_sha": fence.pr_base_sha,
                "merged_blob_oid": merged_oid,
                "operator_subject": principal.subject,
            }
            phase = "receipt_persistence"
            with self.store.database.transaction() as session:
                receipt = SkillPublicationMergeReceiptRecord(
                    id=new_id(),
                    attempt_id=attempt_id,
                    claim_id=claim_id,
                    merge_commit_sha=merge_sha,
                    parent_base_sha=fence.pr_base_sha,
                    merged_blob_oid=merged_oid,
                    receipt_fingerprint=_digest(receipt_material),
                    operator_subject=principal.subject,
                    response_confirmed=True,
                    runtime_authority=False,
                )
                session.add(receipt)
                session.flush()
                return SkillPublicationMergeReceipt.model_validate(receipt)
        except Exception as error:
            # Stable and bounded incident categories. Never persist raw
            # provider responses, paths, exception text or credentials.
            # Phase is set BEFORE the fallible action so even a crash/timeout
            # has a meaningful location, without claiming GitHub outcome.
            validation_reasons = {
                "skill_publication_single_attempt_merge_response_unverified": "response_invalid",
                "skill_publication_single_attempt_postmerge_pr_invalid": "pr_mismatch",
                "skill_publication_single_attempt_postmerge_parent_invalid": "parent_mismatch",
                "skill_publication_single_attempt_postmerge_blob_invalid": "blob_mismatch",
            }
            code = str(error) if isinstance(error, DeveloperError) else ""
            if phase == "receipt_persistence":
                reason = "storage_failure"
            elif code in validation_reasons:
                reason = validation_reasons[code]
            elif phase == "post_tree" and code.startswith("skill_publication_blob_tree_"):
                reason = "blob_mismatch"
            else:
                # A provider transport failure is never mislabeled as an
                # explicit policy mismatch merely because it happened later.
                reason = "provider_exception"
            evidence = {
                "schema": "zelo-skill-publication-merge-failure-v1",
                "attempt_id": attempt_id,
                "claim_id": claim_id,
                "phase": phase,
                "reason": reason,
                "operator_subject": principal.subject,
            }
            try:
                with self.store.database.transaction() as session:
                    session.add(
                        SkillPublicationMergeFailureRecord(
                            id=new_id(),
                            attempt_id=attempt_id,
                            claim_id=claim_id,
                            phase=phase,
                            reason=reason,
                            failure_fingerprint=_digest(evidence),
                            operator_subject=principal.subject,
                            automatic_retry_allowed=False,
                            runtime_authority=False,
                        )
                    )
                    session.flush()
            except Exception:
                # A local persistence outage must not erase the committed
                # attempt or convert an uncertain remote action into a retry.
                # Phase still appears in this bounded error code.
                pass
            raise DeveloperError(
                "skill_publication_single_attempt_outcome_unresolved_" + phase
            ) from None
