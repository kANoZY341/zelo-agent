"""Deterministic, identity-bound learned-skill publication *intent*, no authority.

This prepares an inert signed-off candidate description from trusted Core
stores. The current generic GitHub merge refusal must remain active.
No durable publication claim or external network operation is implemented.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import OperatorPrincipal
from app.skill_learning import (
    CanonicalUuid,
    Sha256,
    skill_procedure_artifact_bytes,
    skill_procedure_artifact_path,
    skill_procedure_artifact_sha256,
)
from app.skill_learning_store import SkillLearningProposalStore
from app.skills import SkillId, SkillName

INTENT_SCHEMA = "zelo-skill-publication-intent-v1"


def _digest(material: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


class SkillPublicationIntent(BaseModel):
    """Immutable preview; never proof of an exclusive reservation or merge grant."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-skill-publication-intent-v1"] = INTENT_SCHEMA
    proposal_id: CanonicalUuid
    proposal_fingerprint: Sha256
    review_fingerprint: Sha256
    agent_instance_id: CanonicalUuid
    source_skill_id: SkillId
    source_skill_version: int = Field(ge=1, le=1_000_000)
    procedure_id: SkillName
    proposed_version: int = Field(ge=1, le=1_000_000)
    revision_sha256: Sha256
    prior_revision_sha256: Sha256 | None = None
    expected_promoted_link_fingerprint: Sha256 | None = None
    artifact_path: str = Field(min_length=1, max_length=4096)
    artifact_sha256: Sha256
    artifact_bytes: int = Field(ge=1, le=65_536)
    repository: str = Field(
        min_length=3, max_length=256, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )
    intent_fingerprint: Sha256
    publication_authority: Literal[False] = False
    runtime_authority: Literal[False] = False
    tool_authority: Literal[False] = False

    @model_validator(mode="after")
    def verify_frozen_identity(self):
        expected_path = (
            f"skills/procedures/{self.source_skill_id}/{self.procedure_id}/"
            f"v{self.proposed_version}-{self.revision_sha256}.json"
        )
        if self.artifact_path != expected_path:
            raise ValueError("skill_publication_intent_artifact_path_invalid")
        if self.proposed_version == 1:
            if (
                self.prior_revision_sha256 is not None
                or self.expected_promoted_link_fingerprint is not None
            ):
                raise ValueError("skill_publication_intent_predecessor_invalid")
        elif self.prior_revision_sha256 is None or self.expected_promoted_link_fingerprint is None:
            raise ValueError("skill_publication_intent_predecessor_invalid")
        material = self.model_dump(mode="json", exclude={"intent_fingerprint"})
        if _digest(material) != self.intent_fingerprint:
            raise ValueError("skill_publication_intent_fingerprint_invalid")
        return self


def prepare_skill_publication_intent(
    store: SkillLearningProposalStore,
    proposal_id: str,
    principal: OperatorPrincipal,
) -> SkillPublicationIntent:
    """Read current accepted review/lineage from Core; emit a bounded inert preview.

    This is a read-only snapshot and MUST NOT be used to merge a GitHub PR:
    another accepted candidate could also prepare an intent. A later durable,
    exclusive reservation and a separately approved merge action are mandatory.
    """

    if not isinstance(principal, OperatorPrincipal) or principal.subject != "ahmad":
        raise PermissionError("skill_publication_intent_operator_required")
    if store.promotion_repository is None:
        raise ValueError("skill_publication_intent_repository_unconfigured")
    stored = store.accepted_candidate_basis(proposal_id, principal)
    if stored is None or stored.review is None:
        raise ValueError("skill_publication_intent_candidate_stale_or_not_accepted")
    if store.get_promotion_link(proposal_id) is not None:
        raise ValueError("skill_publication_intent_already_promoted")

    proposal = stored.proposal
    revision = proposal.proposed_revision
    latest = store.resolve_promoted_procedure(
        proposal.agent_instance_id, revision.source_skill_id, revision.procedure_id
    )
    if latest is None:
        if revision.version != 1 or revision.prior_revision_sha256 is not None:
            raise ValueError("skill_publication_intent_predecessor_missing")
        predecessor_link = None
    else:
        if (
            revision.version != latest.revision.version + 1
            or revision.prior_revision_sha256 != latest.revision_sha256
        ):
            raise ValueError("skill_publication_intent_promoted_head_advanced")
        predecessor_link = latest.promotion_link_fingerprint

    artifact = skill_procedure_artifact_bytes(revision)
    values = {
        "schema_version": INTENT_SCHEMA,
        "proposal_id": proposal.proposal_id,
        "proposal_fingerprint": proposal.proposal_fingerprint,
        "review_fingerprint": stored.review.review_fingerprint,
        "agent_instance_id": proposal.agent_instance_id,
        "source_skill_id": revision.source_skill_id,
        "source_skill_version": revision.source_skill_version,
        "procedure_id": revision.procedure_id,
        "proposed_version": revision.version,
        "revision_sha256": revision.content_sha256,
        "prior_revision_sha256": revision.prior_revision_sha256,
        "expected_promoted_link_fingerprint": predecessor_link,
        "artifact_path": skill_procedure_artifact_path(revision),
        "artifact_sha256": skill_procedure_artifact_sha256(revision),
        "artifact_bytes": len(artifact),
        "repository": store.promotion_repository,
        "publication_authority": False,
        "runtime_authority": False,
        "tool_authority": False,
    }
    return SkillPublicationIntent(**values, intent_fingerprint=_digest(values))
