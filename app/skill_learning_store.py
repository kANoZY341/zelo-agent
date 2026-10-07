"""Inert proposal persistence and current-state review behind operator authorization."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_factory import AgentFactory
from app.auth import OperatorPrincipal
from app.models import (
    AuditEvent,
    SkillLearningEvent,
    SkillLearningPromotionLinkRecord,
    SkillLearningProposalRecord,
    SkillLearningReviewRecord,
    Task,
    TaskAttempt,
    utcnow,
)
from app.policy import State, action_hash
from app.promotion_contracts import (
    CandidateEntry,
    MergeRequest,
    MergeRunResult,
    PromotionRequest,
    PromotionRunResult,
)
from app.skill_learning import (
    SkillLearningEvidenceRef,
    SkillLearningPromotedRevisionSnapshot,
    SkillLearningPromotionLink,
    SkillLearningProposal,
    SkillLearningReview,
    SkillLearningReviewRequest,
    SkillProcedureRevision,
    propose_skill_revision,
    skill_procedure_artifact_bytes,
    skill_procedure_artifact_path,
    skill_procedure_artifact_sha256,
)
from app.skill_learning_records import CoreSkillLearningRecords
from app.skills import SkillRegistry
from app.storage import SessionStore

MAX_PROPOSAL_BYTES = 65_536
MAX_REVIEW_BYTES = 16_384
MAX_PROMOTION_LINK_BYTES = 32_768


@dataclass(frozen=True)
class StoredSkillLearningProposal:
    proposal: SkillLearningProposal
    status: Literal["proposed", "accepted", "rejected"]
    created_at: datetime
    updated_at: datetime
    review: SkillLearningReview | None = None


class SkillLearningProposalStore:
    """Persist proposals and immutable human decisions; never activates content."""

    def __init__(
        self,
        database: SessionStore,
        agent_factory: AgentFactory,
        skill_registry: SkillRegistry,
        *,
        promotion_repository: str | None = None,
    ) -> None:
        self.database = database
        self.agent_factory = agent_factory
        self.skill_registry = skill_registry
        self.promotion_repository = promotion_repository
        self.records = CoreSkillLearningRecords(database, agent_factory)

    def _build(
        self,
        agent_instance_id: str,
        revision: SkillProcedureRevision,
        evidence_refs: tuple[SkillLearningEvidenceRef, ...],
    ) -> SkillLearningProposal:
        revision = SkillProcedureRevision.model_validate_json(revision.model_dump_json())
        evidence_refs = tuple(
            SkillLearningEvidenceRef.model_validate_json(item.model_dump_json())
            for item in evidence_refs
        )
        snapshot = self.records.agent_snapshot(agent_instance_id)
        if snapshot is None:
            raise ValueError("skill_learning_agent_unavailable")
        binding = self.agent_factory.bind_agent(snapshot.agent_id)

        def trusted_prior(skill_id: str, procedure_id: str) -> SkillProcedureRevision | None:
            # A historical v1 proposal must remain revalidatable after its own
            # promotion. Only later proposals depend on promoted lineage.
            if revision.version == 1:
                return None
            promoted = self.resolve_promoted_procedure(
                agent_instance_id,
                skill_id,
                procedure_id,
                target_version=revision.version - 1,
            )
            return promoted.revision if promoted is not None else None

        return propose_skill_revision(
            agent_instance=snapshot,
            agent_binding=binding,
            skill_registry=self.skill_registry,
            revision=revision,
            evidence_refs=evidence_refs,
            evidence_resolver=self.records.evidence_snapshot,
            prior_revision_resolver=trusted_prior,
        )

    @staticmethod
    def _encode(value: object, maximum: int, error: str) -> str:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(payload.encode("ascii")) > maximum:
            raise ValueError(error)
        return payload

    @classmethod
    def _stored(
        cls, session: Session, row: SkillLearningProposalRecord
    ) -> StoredSkillLearningProposal:
        if len(row.proposal_json.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ValueError("skill_learning_record_invalid")
        proposal = SkillLearningProposal.model_validate_json(row.proposal_json)
        revision = proposal.proposed_revision
        if (
            proposal.proposal_id != row.id
            or proposal.proposal_fingerprint != row.proposal_fingerprint
            or proposal.agent_instance_id != row.agent_instance_id
            or proposal.agent_id != row.agent_id
            or proposal.agent_version != row.agent_version
            or proposal.agent_registry_version != row.agent_registry_version
            or proposal.skill_registry_version != row.skill_registry_version
            or proposal.source_skill_id != row.source_skill_id
            or proposal.source_skill_version != row.source_skill_version
            or revision.procedure_id != row.procedure_id
            or revision.content_sha256 != row.revision_sha256
            or revision.prior_revision_sha256 != row.prior_revision_sha256
        ):
            raise ValueError("skill_learning_record_invalid")
        events = session.scalars(
            select(SkillLearningEvent)
            .where(SkillLearningEvent.proposal_id == row.id)
            .order_by(SkillLearningEvent.sequence)
        ).all()
        if not events or events[0].sequence != 1 or events[0].event != "proposed":
            raise ValueError("skill_learning_history_invalid")
        if len(events) == 1 and events[0].reviewer_id is None:
            return StoredSkillLearningProposal(
                proposal=proposal,
                status="proposed",
                created_at=row.created_at,
                updated_at=events[0].created_at,
            )
        if (
            len(events) != 3
            or events[1].sequence != 2
            or events[1].event != "reviewed"
            or events[1].reviewer_id != "ahmad"
            or events[2].sequence != 3
            or events[2].event not in {"accepted", "rejected"}
            or events[2].reviewer_id != "ahmad"
        ):
            raise ValueError("skill_learning_history_invalid")
        review_row = session.scalar(
            select(SkillLearningReviewRecord).where(SkillLearningReviewRecord.proposal_id == row.id)
        )
        if review_row is None or len(review_row.review_json.encode("utf-8")) > MAX_REVIEW_BYTES:
            raise ValueError("skill_learning_review_missing")
        review = SkillLearningReview.model_validate_json(review_row.review_json)
        if (
            review.review_id != review_row.id
            or review.review_fingerprint != review_row.review_fingerprint
            or review.proposal_id != row.id
            or review.proposal_fingerprint != row.proposal_fingerprint
            or review.reviewer_id != review_row.reviewer_id
            or review.reviewer_id != "ahmad"
            or review.decision != review_row.decision
            or review.decision != events[2].event
            or review.agent_instance_id != proposal.agent_instance_id
            or review.agent_id != proposal.agent_id
            or review.agent_version != proposal.agent_version
            or review.agent_registry_version != proposal.agent_registry_version
            or review.skill_registry_version != proposal.skill_registry_version
            or review.source_skill_id != proposal.source_skill_id
            or review.source_skill_version != proposal.source_skill_version
            or review.procedure_id != revision.procedure_id
            or review.revision_sha256 != revision.content_sha256
            or review.evidence_refs != proposal.evidence_refs
        ):
            raise ValueError("skill_learning_review_invalid")
        return StoredSkillLearningProposal(
            proposal=proposal,
            status=review.decision,
            created_at=row.created_at,
            updated_at=review_row.created_at,
            review=review,
        )

    def submit(
        self,
        *,
        agent_instance_id: str,
        revision: SkillProcedureRevision,
        evidence_refs: tuple[SkillLearningEvidenceRef, ...],
    ) -> tuple[StoredSkillLearningProposal, bool]:
        """Create one frozen candidate after Core-owned revalidation; retries are idempotent."""

        with self.database.transaction() as session:
            proposal = self._build(agent_instance_id, revision, evidence_refs)
            encoded = self._encode(
                proposal.model_dump(mode="json"),
                MAX_PROPOSAL_BYTES,
                "skill_learning_proposal_too_large",
            )
            row = session.get(SkillLearningProposalRecord, proposal.proposal_id)
            if row is not None:
                stored = self._stored(session, row)
                if row.proposal_json != encoded or stored.proposal != proposal:
                    raise ValueError("skill_learning_proposal_identity_conflict")
                return stored, False
            row = SkillLearningProposalRecord(
                id=proposal.proposal_id,
                proposal_fingerprint=proposal.proposal_fingerprint,
                agent_instance_id=proposal.agent_instance_id,
                agent_id=proposal.agent_id,
                agent_version=proposal.agent_version,
                agent_registry_version=proposal.agent_registry_version,
                skill_registry_version=proposal.skill_registry_version,
                source_skill_id=proposal.source_skill_id,
                source_skill_version=proposal.source_skill_version,
                procedure_id=proposal.proposed_revision.procedure_id,
                revision_sha256=proposal.proposed_revision.content_sha256,
                prior_revision_sha256=proposal.proposed_revision.prior_revision_sha256,
                proposal_json=encoded,
            )
            session.add(row)
            session.flush()
            session.add(
                SkillLearningEvent(
                    proposal_id=row.id, sequence=1, event="proposed", reviewer_id=None
                )
            )
            session.flush()
            return self._stored(session, row), True

    def _review(
        self, proposal: SkillLearningProposal, request: SkillLearningReviewRequest
    ) -> SkillLearningReview:
        binding = self.agent_factory.bind_agent(proposal.agent_id)
        skill = self.skill_registry.get(proposal.source_skill_id)
        if skill is None or skill.version != proposal.source_skill_version:
            raise ValueError("skill_learning_proposal_stale")
        authority_digest = hashlib.sha256(
            json.dumps(
                {
                    "agent_spec": binding.spec.model_dump(mode="json"),
                    "source_skill": skill.model_dump(mode="json"),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()
        material = {
            "schema_version": "zelo-skill-learning-review-v1",
            "proposal_id": proposal.proposal_id,
            "proposal_fingerprint": proposal.proposal_fingerprint,
            "reviewer_id": "ahmad",
            "decision": request.decision,
            "agent_instance_id": proposal.agent_instance_id,
            "agent_id": proposal.agent_id,
            "agent_version": proposal.agent_version,
            "agent_registry_version": proposal.agent_registry_version,
            "skill_registry_version": proposal.skill_registry_version,
            "source_skill_id": proposal.source_skill_id,
            "source_skill_version": proposal.source_skill_version,
            "procedure_id": proposal.proposed_revision.procedure_id,
            "revision_sha256": proposal.proposed_revision.content_sha256,
            "evidence_refs": [item.model_dump(mode="json") for item in proposal.evidence_refs],
            "authority_envelope_sha256": authority_digest,
            "checks": request.checks.model_dump(mode="json"),
            "rationale": request.rationale,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                material, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode("ascii")
        ).hexdigest()
        review_id = str(uuid5(NAMESPACE_URL, f"zelo-skill-learning-review-v1:{fingerprint}"))
        return SkillLearningReview(
            **material,
            review_id=review_id,
            reviewed_at=utcnow(),
            review_fingerprint=fingerprint,
        )

    def record_review(
        self,
        proposal_id: str,
        request: SkillLearningReviewRequest,
        principal: OperatorPrincipal,
    ) -> tuple[StoredSkillLearningProposal, bool]:
        """Record Ahmad's immutable decision after fresh evidence and identity checks."""

        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_learning_reviewer_forbidden")
        with self.database.transaction() as session:
            row = session.get(SkillLearningProposalRecord, proposal_id)
            if row is None:
                raise LookupError("skill_learning_proposal_not_found")
            stored = self._stored(session, row)
            if stored.review is not None:
                same = (
                    stored.review.decision == request.decision
                    and stored.review.checks == request.checks
                    and stored.review.rationale == request.rationale
                )
                if same:
                    return stored, False
                raise ValueError("skill_learning_proposal_already_reviewed")
            try:
                rebuilt = self._build(
                    stored.proposal.agent_instance_id,
                    stored.proposal.proposed_revision,
                    stored.proposal.evidence_refs,
                )
            except ValueError:
                raise ValueError("skill_learning_proposal_stale") from None
            if rebuilt != stored.proposal:
                raise ValueError("skill_learning_proposal_stale")
            review = self._review(stored.proposal, request)
            review_json = self._encode(
                review.model_dump(mode="json"), MAX_REVIEW_BYTES, "skill_learning_review_too_large"
            )
            session.add(
                SkillLearningReviewRecord(
                    id=review.review_id,
                    proposal_id=row.id,
                    reviewer_id=principal.subject,
                    decision=review.decision,
                    review_fingerprint=review.review_fingerprint,
                    review_json=review_json,
                )
            )
            session.flush()
            session.add_all(
                (
                    SkillLearningEvent(
                        proposal_id=row.id,
                        sequence=2,
                        event="reviewed",
                        reviewer_id=principal.subject,
                    ),
                    SkillLearningEvent(
                        proposal_id=row.id,
                        sequence=3,
                        event=review.decision,
                        reviewer_id=principal.subject,
                    ),
                )
            )
            session.flush()
            return self._stored(session, row), True

    def get(self, proposal_id: str) -> StoredSkillLearningProposal | None:
        with self.database.read() as session:
            row = session.get(SkillLearningProposalRecord, proposal_id)
            return self._stored(session, row) if row is not None else None

    def list(self, *, limit: int = 100) -> tuple[StoredSkillLearningProposal, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("skill_learning_list_limit_invalid")
        with self.database.read() as session:
            rows = session.scalars(
                select(SkillLearningProposalRecord)
                .order_by(
                    SkillLearningProposalRecord.created_at.desc(), SkillLearningProposalRecord.id
                )
                .limit(limit)
            ).all()
            return tuple(self._stored(session, row) for row in rows)

    @classmethod
    def _stored_promotion_link(
        cls, row: SkillLearningPromotionLinkRecord
    ) -> SkillLearningPromotionLink:
        if len(row.link_json.encode("utf-8")) > MAX_PROMOTION_LINK_BYTES:
            raise ValueError("skill_learning_promotion_link_invalid")
        link = SkillLearningPromotionLink.model_validate_json(row.link_json)
        if (
            link.link_id != row.id
            or link.proposal_id != row.proposal_id
            or link.reviewer_id != row.reviewer_id
            or link.review_fingerprint != row.review_fingerprint
            or link.revision_sha256 != row.revision_sha256
            or link.artifact_path != row.artifact_path
            or link.artifact_sha256 != row.artifact_sha256
            or link.artifact_bytes != row.artifact_bytes
            or link.repository != row.repository
            or link.promotion_task_id != row.promotion_task_id
            or link.promotion_action_version != row.promotion_action_version
            or link.promotion_action_hash != row.promotion_action_hash
            or link.promotion_review_fingerprint != row.promotion_review_fingerprint
            or link.pr_number != row.pr_number
            or link.pr_head_sha != row.pr_head_sha
            or link.merge_task_id != row.merge_task_id
            or link.merge_action_version != row.merge_action_version
            or link.merge_action_hash != row.merge_action_hash
            or link.merge_commit_sha != row.merge_commit_sha
            or link.link_fingerprint != row.link_fingerprint
            or link.linked_at != row.created_at
            or link.runtime_authority is not False
            or row.runtime_authority is not False
        ):
            raise ValueError("skill_learning_promotion_link_invalid")
        return link

    @staticmethod
    def _latest_successful_attempt(session: Session, task: Task) -> TaskAttempt:
        attempt = session.scalar(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task.id)
            .order_by(TaskAttempt.number.desc())
            .limit(1)
        )
        if (
            attempt is None
            or attempt.outcome != State.SUCCEEDED
            or attempt.error is not None
            or attempt.result != task.result
        ):
            raise ValueError("skill_learning_promotion_lineage_invalid")
        return attempt

    def _promotion_lineage(
        self,
        session: Session,
        merge_task_id: str,
        expected_entry: CandidateEntry,
    ) -> tuple[Task, PromotionRequest, PromotionRunResult, Task, MergeRequest, MergeRunResult]:
        if self.promotion_repository is None:
            raise ValueError("skill_learning_promotion_repository_unavailable")

        merge_task = session.get(Task, merge_task_id)
        if (
            merge_task is None
            or merge_task.kind != "merge_run"
            or merge_task.status != State.SUCCEEDED
            or merge_task.merge_spec is None
            or merge_task.result is None
        ):
            raise ValueError("skill_learning_merge_task_invalid")
        self._latest_successful_attempt(session, merge_task)
        try:
            merge = MergeRequest.model_validate(merge_task.merge_spec)
            merge_result = MergeRunResult.model_validate(merge_task.result)
        except ValueError:
            raise ValueError("skill_learning_merge_task_invalid") from None

        promotion_task = session.get(Task, merge.promotion_task_id)
        if (
            promotion_task is None
            or promotion_task.kind != "promotion_run"
            or promotion_task.status != State.SUCCEEDED
            or promotion_task.promotion_spec is None
            or promotion_task.result is None
        ):
            raise ValueError("skill_learning_promotion_task_invalid")
        self._latest_successful_attempt(session, promotion_task)
        try:
            promotion = PromotionRequest.model_validate(promotion_task.promotion_spec)
            promotion_result = PromotionRunResult.model_validate(promotion_task.result)
        except ValueError:
            raise ValueError("skill_learning_promotion_task_invalid") from None

        expected_promotion_hash = action_hash(
            promotion_task.kind,
            promotion_task.payload,
            promotion_task.timeout_seconds,
            promotion_task.action_version,
            promotion_task.promotion_spec,
        )
        expected_merge_hash = action_hash(
            merge_task.kind,
            merge_task.payload,
            merge_task.timeout_seconds,
            merge_task.action_version,
            merge_task.merge_spec,
        )
        if (
            promotion_task.action_hash != expected_promotion_hash
            or merge_task.action_hash != expected_merge_hash
        ):
            raise ValueError("skill_learning_promotion_lineage_invalid")

        review = promotion.review
        publication = promotion_result.publication
        commit = promotion_result.commit
        if (
            promotion.repository != self.promotion_repository
            or publication.repository != self.promotion_repository
            or merge_result.repository != self.promotion_repository
            or merge.promotion_task_id != promotion_task.id
            or merge.promotion_action_version != promotion_task.action_version
            or merge.promotion_action_hash != promotion_task.action_hash
            or merge.promotion != promotion
            or merge.publication != publication
            or merge.ci.repository != publication.repository
            or merge.ci.pr_number != publication.pr_number
            or merge.ci.head_sha != publication.pr_head_sha
            or merge.ci.base_sha != publication.pr_base_sha
            or commit.review_fingerprint != review.review_fingerprint
            or commit.branch != promotion.branch
            or commit.base_sha != review.base_sha
            or publication.branch != promotion.branch
            or publication.commit_sha != commit.commit_sha
            or publication.pr_head_sha != commit.commit_sha
            or publication.pr_base_sha != review.base_sha
            or merge_result.pr_number != publication.pr_number
            or merge_result.base_branch != publication.base_branch
            or merge_result.base_sha != review.base_sha
            or merge_result.head_branch != publication.branch
            or merge_result.head_sha != publication.pr_head_sha
            or merge_result.merge_method != merge.merge_method
            or review.changed_file_count != 1
            or review.candidate_manifest != [expected_entry]
        ):
            raise ValueError("skill_learning_promotion_lineage_invalid")
        return promotion_task, promotion, promotion_result, merge_task, merge, merge_result

    def get_promotion_link(self, proposal_id: str) -> SkillLearningPromotionLink | None:
        with self.database.read() as session:
            row = session.scalar(
                select(SkillLearningPromotionLinkRecord).where(
                    SkillLearningPromotionLinkRecord.proposal_id == proposal_id
                )
            )
            return self._stored_promotion_link(row) if row is not None else None

    def resolve_promoted_revision(
        self, proposal_id: str
    ) -> SkillLearningPromotedRevisionSnapshot | None:
        """Resolve immutable historical promoted content without runtime authority."""

        with self.database.read() as session:
            proposal_row = session.get(SkillLearningProposalRecord, proposal_id)
            if proposal_row is None:
                return None
            link_row = session.scalar(
                select(SkillLearningPromotionLinkRecord).where(
                    SkillLearningPromotionLinkRecord.proposal_id == proposal_id
                )
            )
            if link_row is None:
                return None
            stored = self._stored(session, proposal_row)
            link = self._stored_promotion_link(link_row)

        if (
            stored.status != "accepted"
            or stored.review is None
            or stored.review.decision != "accepted"
            or not stored.review.checks.all_passed()
            or link.proposal_id != stored.proposal.proposal_id
            or link.proposal_fingerprint != stored.proposal.proposal_fingerprint
            or link.review_fingerprint != stored.review.review_fingerprint
            or link.reviewer_id != "ahmad"
            or link.revision_sha256 != stored.proposal.proposed_revision.content_sha256
        ):
            raise ValueError("skill_learning_promoted_revision_invalid")
        if self.promotion_repository is None or link.repository != self.promotion_repository:
            raise ValueError("skill_learning_promoted_revision_repository_stale")

        revision = stored.proposal.proposed_revision
        artifact = skill_procedure_artifact_bytes(revision)
        if (
            link.artifact_path != skill_procedure_artifact_path(revision)
            or link.artifact_sha256 != skill_procedure_artifact_sha256(revision)
            or link.artifact_bytes != len(artifact)
        ):
            raise ValueError("skill_learning_promoted_revision_invalid")

        return SkillLearningPromotedRevisionSnapshot(
            proposal_id=stored.proposal.proposal_id,
            proposal_fingerprint=stored.proposal.proposal_fingerprint,
            review_fingerprint=stored.review.review_fingerprint,
            promotion_link_id=link.link_id,
            promotion_link_fingerprint=link.link_fingerprint,
            agent_instance_id=stored.proposal.agent_instance_id,
            source_skill_id=revision.source_skill_id,
            source_skill_version=revision.source_skill_version,
            procedure_id=revision.procedure_id,
            revision_sha256=revision.content_sha256,
            revision=revision,
            repository=link.repository,
            artifact_path=link.artifact_path,
            artifact_sha256=link.artifact_sha256,
            artifact_bytes=link.artifact_bytes,
            merge_commit_sha=link.merge_commit_sha,
            promoted_at=link.linked_at,
        )

    def resolve_promoted_procedure(
        self,
        agent_instance_id: str,
        source_skill_id: str,
        procedure_id: str,
        *,
        target_version: int | None = None,
    ) -> SkillLearningPromotedRevisionSnapshot | None:
        """Resolve the unique promoted lineage; optionally select an exact historical version."""

        if target_version is not None and (
            type(target_version) is not int or not 1 <= target_version <= 1_000_000
        ):
            raise ValueError("skill_learning_promoted_revision_version_invalid")

        with self.database.read() as session:
            proposal_ids = tuple(
                session.scalars(
                    select(SkillLearningProposalRecord.id)
                    .join(
                        SkillLearningPromotionLinkRecord,
                        SkillLearningPromotionLinkRecord.proposal_id
                        == SkillLearningProposalRecord.id,
                    )
                    .where(
                        SkillLearningProposalRecord.agent_instance_id == agent_instance_id,
                        SkillLearningProposalRecord.source_skill_id == source_skill_id,
                        SkillLearningProposalRecord.procedure_id == procedure_id,
                    )
                    .order_by(
                        SkillLearningProposalRecord.created_at, SkillLearningProposalRecord.id
                    )
                    .limit(65)
                ).all()
            )
        if not proposal_ids:
            return None
        if len(proposal_ids) > 64:
            raise ValueError("skill_learning_promoted_revision_history_limit")

        snapshots = []
        for proposal_id in proposal_ids:
            snapshot = self.resolve_promoted_revision(proposal_id)
            if snapshot is None:
                raise ValueError("skill_learning_promoted_revision_invalid")
            snapshots.append(snapshot)
        snapshots.sort(key=lambda item: (item.revision.version, item.proposal_id))

        versions: dict[int, SkillLearningPromotedRevisionSnapshot] = {}
        for snapshot in snapshots:
            version = snapshot.revision.version
            if version in versions:
                raise ValueError("skill_learning_promoted_revision_ambiguous")
            versions[version] = snapshot

        ordered = [versions[version] for version in sorted(versions)]
        first = ordered[0].revision
        if first.version != 1 or first.prior_revision_sha256 is not None:
            raise ValueError("skill_learning_promoted_revision_lineage_invalid")
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if (
                current.revision.version != previous.revision.version + 1
                or current.revision.prior_revision_sha256 != previous.revision.content_sha256
            ):
                raise ValueError("skill_learning_promoted_revision_lineage_invalid")
        if target_version is not None:
            return versions.get(target_version)
        return ordered[-1]

    def record_promotion_link(
        self,
        proposal_id: str,
        merge_task_id: str,
        principal: OperatorPrincipal,
    ) -> tuple[SkillLearningPromotionLink, bool]:
        """Bind an accepted proposal to existing successful promotion/merge evidence only."""

        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_learning_reviewer_forbidden")
        stored = self.accepted_candidate_basis(proposal_id, principal)
        if stored is None or stored.review is None:
            raise ValueError("skill_learning_candidate_not_accepted_or_stale")

        revision = stored.proposal.proposed_revision
        artifact = skill_procedure_artifact_bytes(revision)
        artifact_path = skill_procedure_artifact_path(revision)
        artifact_sha = skill_procedure_artifact_sha256(revision)
        expected_entry = CandidateEntry(
            path=artifact_path,
            operation="add",
            object_type="file",
            mode="100644",
            size=len(artifact),
            sha256=artifact_sha,
        )

        with self.database.transaction() as session:
            proposal_row = session.get(SkillLearningProposalRecord, proposal_id)
            if proposal_row is None:
                raise LookupError("skill_learning_proposal_not_found")
            current = self._stored(session, proposal_row)
            if current != stored:
                raise ValueError("skill_learning_proposal_stale")
            promotion_task, promotion, promotion_result, merge_task, _merge, merge_result = (
                self._promotion_lineage(session, merge_task_id, expected_entry)
            )

            existing = session.scalar(
                select(SkillLearningPromotionLinkRecord).where(
                    SkillLearningPromotionLinkRecord.proposal_id == proposal_id
                )
            )
            merge_existing = session.scalar(
                select(SkillLearningPromotionLinkRecord).where(
                    SkillLearningPromotionLinkRecord.merge_task_id == merge_task_id
                )
            )

            material = {
                "schema_version": "zelo-skill-learning-promotion-link-v1",
                "proposal_id": proposal_id,
                "proposal_fingerprint": stored.proposal.proposal_fingerprint,
                "review_fingerprint": stored.review.review_fingerprint,
                "reviewer_id": "ahmad",
                "revision_sha256": revision.content_sha256,
                "artifact_path": artifact_path,
                "artifact_sha256": artifact_sha,
                "artifact_bytes": len(artifact),
                "repository": promotion.repository,
                "promotion_task_id": promotion_task.id,
                "promotion_action_version": promotion_task.action_version,
                "promotion_action_hash": promotion_task.action_hash,
                "promotion_review_fingerprint": promotion.review.review_fingerprint,
                "pr_number": promotion_result.publication.pr_number,
                "pr_head_sha": promotion_result.publication.pr_head_sha,
                "merge_task_id": merge_task.id,
                "merge_action_version": merge_task.action_version,
                "merge_action_hash": merge_task.action_hash,
                "merge_commit_sha": merge_result.merge_commit_sha,
                "runtime_authority": False,
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    material,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("ascii")
            ).hexdigest()
            link_id = str(
                uuid5(NAMESPACE_URL, f"zelo-skill-learning-promotion-link-v1:{fingerprint}")
            )
            link = SkillLearningPromotionLink(
                **material,
                link_id=link_id,
                link_fingerprint=fingerprint,
                linked_at=utcnow(),
            )

            if existing is not None:
                retained = self._stored_promotion_link(existing)
                retained_material = retained.model_dump(mode="json", exclude={"linked_at"})
                link_material = link.model_dump(mode="json", exclude={"linked_at"})
                if retained_material == link_material:
                    return retained, False
                raise ValueError("skill_learning_promotion_already_linked")
            if merge_existing is not None:
                raise ValueError("skill_learning_promotion_merge_reused")

            encoded = self._encode(
                link.model_dump(mode="json"),
                MAX_PROMOTION_LINK_BYTES,
                "skill_learning_promotion_link_too_large",
            )
            row = SkillLearningPromotionLinkRecord(
                id=link.link_id,
                proposal_id=proposal_id,
                reviewer_id=link.reviewer_id,
                review_fingerprint=link.review_fingerprint,
                revision_sha256=link.revision_sha256,
                artifact_path=link.artifact_path,
                artifact_sha256=link.artifact_sha256,
                artifact_bytes=link.artifact_bytes,
                repository=link.repository,
                promotion_task_id=link.promotion_task_id,
                promotion_action_version=link.promotion_action_version,
                promotion_action_hash=link.promotion_action_hash,
                promotion_review_fingerprint=link.promotion_review_fingerprint,
                pr_number=link.pr_number,
                pr_head_sha=link.pr_head_sha,
                merge_task_id=link.merge_task_id,
                merge_action_version=link.merge_action_version,
                merge_action_hash=link.merge_action_hash,
                merge_commit_sha=link.merge_commit_sha,
                link_fingerprint=link.link_fingerprint,
                link_json=encoded,
                runtime_authority=False,
                created_at=link.linked_at,
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    task_id=merge_task.id,
                    event="skill_learning_promoted",
                    details={
                        "proposal_id": proposal_id,
                        "link_id": link.link_id,
                        "revision_sha256": link.revision_sha256,
                        "promotion_task_id": promotion_task.id,
                        "merge_task_id": merge_task.id,
                        "merge_commit_sha": merge_result.merge_commit_sha,
                        "runtime_authority": False,
                    },
                )
            )
            session.flush()
            return self._stored_promotion_link(row), True

    def list_for_agent(
        self,
        agent_instance_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[tuple[StoredSkillLearningProposal, ...], int]:
        """Read-only bounded history for one AgentInstance; does not reclassify authority."""

        if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
            raise ValueError("skill_learning_list_limit_invalid")
        with self.database.read() as session:
            total = session.scalar(
                select(func.count())
                .select_from(SkillLearningProposalRecord)
                .where(SkillLearningProposalRecord.agent_instance_id == agent_instance_id)
            )
            rows = session.scalars(
                select(SkillLearningProposalRecord)
                .where(SkillLearningProposalRecord.agent_instance_id == agent_instance_id)
                .order_by(
                    SkillLearningProposalRecord.created_at.desc(),
                    SkillLearningProposalRecord.id,
                )
                .limit(limit)
                .offset(offset)
            ).all()
            return tuple(self._stored(session, row) for row in rows), int(total or 0)

    def promotion_links_for(
        self, proposal_ids: tuple[str, ...]
    ) -> dict[str, SkillLearningPromotionLink]:
        """Return immutable historical promotion links for exact proposal IDs."""

        if not proposal_ids:
            return {}
        if len(proposal_ids) > 100 or len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("skill_learning_promotion_link_query_invalid")
        with self.database.read() as session:
            rows = session.scalars(
                select(SkillLearningPromotionLinkRecord).where(
                    SkillLearningPromotionLinkRecord.proposal_id.in_(proposal_ids)
                )
            ).all()
            return {row.proposal_id: self._stored_promotion_link(row) for row in rows}

    def review_basis(self, proposal_id: str) -> StoredSkillLearningProposal | None:
        """Recheck current identity/evidence for a reviewer; grants no decision authority."""

        stored = self.get(proposal_id)
        if stored is None or stored.status != "proposed":
            return None
        try:
            rebuilt = self._build(
                stored.proposal.agent_instance_id,
                stored.proposal.proposed_revision,
                stored.proposal.evidence_refs,
            )
        except ValueError:
            return None
        return stored if rebuilt == stored.proposal else None

    def accepted_candidate_basis(
        self, proposal_id: str, principal: OperatorPrincipal
    ) -> StoredSkillLearningProposal | None:
        """Return a fresh accepted proposal for an explicit operator candidate action."""

        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_learning_reviewer_forbidden")
        stored = self.get(proposal_id)
        if (
            stored is None
            or stored.status != "accepted"
            or stored.review is None
            or stored.review.decision != "accepted"
            or not stored.review.checks.all_passed()
            or stored.review.proposal_id != proposal_id
            or stored.review.proposal_fingerprint != stored.proposal.proposal_fingerprint
            or stored.review.revision_sha256 != stored.proposal.proposed_revision.content_sha256
        ):
            return None
        try:
            rebuilt = self._build(
                stored.proposal.agent_instance_id,
                stored.proposal.proposed_revision,
                stored.proposal.evidence_refs,
            )
        except ValueError:
            return None
        return stored if rebuilt == stored.proposal else None
