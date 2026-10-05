"""Inert durable proposal records; no route, review decision, or activation writer."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_factory import AgentFactory
from app.models import SkillLearningEvent, SkillLearningProposalRecord
from app.skill_learning import (
    SkillLearningEvidenceRef,
    SkillLearningProposal,
    SkillProcedureRevision,
    propose_skill_revision,
)
from app.skill_learning_records import CoreSkillLearningRecords
from app.skills import SkillRegistry
from app.storage import SessionStore

MAX_PROPOSAL_BYTES = 65_536


@dataclass(frozen=True)
class StoredSkillLearningProposal:
    proposal: SkillLearningProposal
    status: Literal["proposed"]
    created_at: datetime
    updated_at: datetime


class SkillLearningProposalStore:
    """Persist only trusted proposed identities, with no approval capability."""

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
            # There is no accepted procedure-revision store in this slice.
            prior_revision_resolver=lambda _skill, _procedure: None,
        )

    @staticmethod
    def _encode(proposal: SkillLearningProposal) -> str:
        payload = json.dumps(
            proposal.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(payload.encode("ascii")) > MAX_PROPOSAL_BYTES:
            raise ValueError("skill_learning_proposal_too_large")
        return payload

    @staticmethod
    def _stored(session: Session, row: SkillLearningProposalRecord) -> StoredSkillLearningProposal:
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
            select(SkillLearningEvent).where(SkillLearningEvent.proposal_id == row.id)
        ).all()
        if len(events) != 1 or events[0].sequence != 1 or events[0].event != "proposed":
            raise ValueError("skill_learning_history_invalid")
        return StoredSkillLearningProposal(
            proposal=proposal,
            status="proposed",
            created_at=row.created_at,
            updated_at=events[0].created_at,
        )

    def submit(
        self,
        *,
        agent_instance_id: str,
        revision: SkillProcedureRevision,
        evidence_refs: tuple[SkillLearningEvidenceRef, ...],
    ) -> tuple[StoredSkillLearningProposal, bool]:
        """Create one frozen candidate after Core-owned revalidation; retry is idempotent."""

        with self.database.transaction() as session:
            proposal = self._build(agent_instance_id, revision, evidence_refs)
            encoded = self._encode(proposal)
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
            session.add(SkillLearningEvent(proposal_id=row.id, sequence=1, event="proposed"))
            session.flush()
            return self._stored(session, row), True

    def get(self, proposal_id: str) -> StoredSkillLearningProposal | None:
        """Read one persisted candidate; caller must provide its own authorization."""

        with self.database.read() as session:
            row = session.get(SkillLearningProposalRecord, proposal_id)
            return self._stored(session, row) if row is not None else None

    def review_basis(self, proposal_id: str) -> StoredSkillLearningProposal | None:
        """Recheck current identity/evidence for a reviewer; grants no decision authority."""

        stored = self.get(proposal_id)
        if stored is None:
            return None
        proposal = stored.proposal
        try:
            rebuilt = self._build(
                proposal.agent_instance_id,
                proposal.proposed_revision,
                proposal.evidence_refs,
            )
        except ValueError:
            return None
        return stored if rebuilt == proposal else None
