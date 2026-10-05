"""Inert proposal persistence and current-state review behind operator authorization."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_factory import AgentFactory
from app.auth import OperatorPrincipal
from app.models import (
    SkillLearningEvent,
    SkillLearningProposalRecord,
    SkillLearningReviewRecord,
    utcnow,
)
from app.skill_learning import (
    SkillLearningEvidenceRef,
    SkillLearningProposal,
    SkillLearningReview,
    SkillLearningReviewRequest,
    SkillProcedureRevision,
    propose_skill_revision,
)
from app.skill_learning_records import CoreSkillLearningRecords
from app.skills import SkillRegistry
from app.storage import SessionStore

MAX_PROPOSAL_BYTES = 65_536
MAX_REVIEW_BYTES = 16_384


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
    ) -> None:
        self.database = database
        self.agent_factory = agent_factory
        self.skill_registry = skill_registry
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
        return propose_skill_revision(
            agent_instance=snapshot,
            agent_binding=binding,
            skill_registry=self.skill_registry,
            revision=revision,
            evidence_refs=evidence_refs,
            evidence_resolver=self.records.evidence_snapshot,
            prior_revision_resolver=lambda _skill, _procedure: None,
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
