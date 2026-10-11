"""Exclusive local reservation of accepted learned-skill publication scopes.

A reservation freezes trusted review/predecessor identity but DOES NOT authorize
GitHub publication, merger, skill loading or any new Agent/Worker/Tool authority.
The generic protected learned-skill merge refusal remains unchanged.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth import OperatorPrincipal
from app.models import SkillPublicationClaimRecord, new_id
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_learning_store import SkillLearningProposalStore
from app.skill_publication_intent import _digest, prepare_skill_publication_intent
from app.skills import SkillId, SkillName


class SkillPublicationClaim(BaseModel):
    """Immutable Core operator reservation; intentionally not a merge token."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True, strict=True)

    schema_version: Literal["zelo-skill-publication-claim-v1"] = "zelo-skill-publication-claim-v1"
    id: CanonicalUuid
    proposal_id: CanonicalUuid
    agent_instance_id: CanonicalUuid
    source_skill_id: SkillId
    procedure_id: SkillName
    proposed_version: int = Field(ge=1, le=1_000_000)
    repository: str
    review_fingerprint: Sha256
    intent_fingerprint: Sha256
    claim_fingerprint: Sha256
    artifact_sha256: Sha256
    prior_revision_sha256: Sha256 | None
    operator_subject: Literal["ahmad"]
    created_at: datetime
    publication_authority: Literal[False] = False
    runtime_authority: Literal[False] = False


class SkillPublicationClaimService:
    """Reserve each accepted proposal's exact procedure/version under one writer.

    No grant, expiration, reassignment or deletion route is provided: uncertain
    publication outcomes must remain fenced until a later reviewed resolution.
    """

    def __init__(self, store: SkillLearningProposalStore) -> None:
        self.store = store

    @staticmethod
    def _response(row: SkillPublicationClaimRecord) -> SkillPublicationClaim:
        return SkillPublicationClaim.model_validate(row)

    def get(self, claim_id: str, principal: OperatorPrincipal) -> SkillPublicationClaim | None:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_claim_operator_required")
        with self.store.database.read() as session:
            record = session.get(SkillPublicationClaimRecord, claim_id)
            return self._response(record) if record is not None else None

    def reserve(
        self,
        proposal_id: str,
        expected_intent_fingerprint: str,
        principal: OperatorPrincipal,
    ) -> tuple[SkillPublicationClaim, bool]:
        if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
            raise PermissionError("skill_publication_claim_operator_required")

        # BEGIN IMMEDIATE serializes competing writers BEFORE recalculating
        # acceptance, registry and predecessor state from authoritative Core.
        with self.store.database.transaction() as session:
            intent = prepare_skill_publication_intent(self.store, proposal_id, principal)
            if intent.intent_fingerprint != expected_intent_fingerprint:
                raise ValueError("skill_publication_claim_intent_stale_or_mismatched")

            existing = session.scalar(
                select(SkillPublicationClaimRecord).where(
                    SkillPublicationClaimRecord.agent_instance_id == intent.agent_instance_id,
                    SkillPublicationClaimRecord.source_skill_id == intent.source_skill_id,
                    SkillPublicationClaimRecord.procedure_id == intent.procedure_id,
                    SkillPublicationClaimRecord.proposed_version == intent.proposed_version,
                )
            )
            if existing is not None:
                if (
                    existing.proposal_id != intent.proposal_id
                    or existing.intent_fingerprint != intent.intent_fingerprint
                ):
                    raise ValueError("skill_publication_claim_scope_already_reserved")
                return self._response(existing), False

            record = SkillPublicationClaimRecord(
                id=new_id(),
                proposal_id=intent.proposal_id,
                agent_instance_id=intent.agent_instance_id,
                source_skill_id=intent.source_skill_id,
                procedure_id=intent.procedure_id,
                proposed_version=intent.proposed_version,
                repository=intent.repository,
                review_fingerprint=intent.review_fingerprint,
                intent_fingerprint=intent.intent_fingerprint,
                artifact_sha256=intent.artifact_sha256,
                prior_revision_sha256=intent.prior_revision_sha256,
                operator_subject=principal.subject,
                publication_authority=False,
                runtime_authority=False,
                claim_fingerprint=_digest(
                    {
                        "schema_version": "zelo-skill-publication-claim-v1",
                        "proposal_id": intent.proposal_id,
                        "intent_fingerprint": intent.intent_fingerprint,
                        "operator_subject": principal.subject,
                    }
                ),
            )
            session.add(record)
            session.flush()
            return self._response(record), True
