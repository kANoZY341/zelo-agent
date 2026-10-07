"""Pure governed Agent skill-learning contracts for v0.15.

Callers must obtain instance snapshots and evidence records from trusted Core
stores. This module cannot persist, approve, promote, execute, or activate a
learned procedure.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.agent_factory import AgentBinding, Identifier
from app.capabilities import CapabilityId
from app.skills import SkillId, SkillName, SkillRegistry

SKILL_EVIDENCE_SCHEMA = "zelo-skill-learning-evidence-v1"
SKILL_PROCEDURE_SCHEMA = "zelo-skill-procedure-revision-v1"
SKILL_PROPOSAL_SCHEMA = "zelo-skill-learning-proposal-v1"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CanonicalUuid = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"),
]
EvidenceId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
StepText = Annotated[str, Field(min_length=1, max_length=512)]
EvidenceKind = Literal["task_attempt", "promotion_review", "operator_feedback"]


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("skill_learning_uuid_invalid") from None
    if str(parsed) != value:
        raise ValueError("skill_learning_uuid_invalid")
    return value


def _proposal_uuid(fingerprint: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"zelo-skill-learning-proposal-v1:{fingerprint}"))


class SkillLearningContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillLearningEvidenceRef(SkillLearningContract):
    schema_version: Literal["zelo-skill-learning-evidence-v1"] = SKILL_EVIDENCE_SCHEMA
    kind: EvidenceKind
    source_id: EvidenceId
    content_sha256: Sha256


class SkillLearningEvidenceSnapshot(SkillLearningContract):
    """One retained Core fact supplied by a trusted evidence resolver."""

    kind: EvidenceKind
    source_id: EvidenceId
    content_sha256: Sha256
    owner_agent_instance_id: CanonicalUuid
    agent_registry_version: Sha256
    visible_to_agent_instance_ids: tuple[CanonicalUuid, ...] = Field(default=(), max_length=16)
    terminal: bool
    accepted: bool

    @model_validator(mode="after")
    def _validate_identity(self):
        _canonical_uuid(self.owner_agent_instance_id)
        for instance_id in self.visible_to_agent_instance_ids:
            _canonical_uuid(instance_id)
        if self.visible_to_agent_instance_ids != tuple(
            sorted(set(self.visible_to_agent_instance_ids))
        ):
            raise ValueError("skill_learning_evidence_visibility_not_canonical")
        if self.accepted and not self.terminal:
            raise ValueError("skill_learning_evidence_not_terminal")
        return self


class SkillLearningAgentSnapshot(SkillLearningContract):
    """Persisted AgentInstance identity supplied by trusted Core state."""

    agent_instance_id: CanonicalUuid
    agent_id: Identifier
    agent_version: int = Field(ge=1, le=1_000_000)
    agent_registry_version: Sha256
    status: Literal["active", "paused", "retired"]

    @model_validator(mode="after")
    def _validate_identity(self):
        _canonical_uuid(self.agent_instance_id)
        return self


class SkillProcedureDraft(SkillLearningContract):
    """Candidate content before its independent digest is frozen."""

    schema_version: Literal["zelo-skill-procedure-revision-v1"] = SKILL_PROCEDURE_SCHEMA
    source_skill_id: SkillId
    source_skill_version: int = Field(ge=1, le=1_000_000)
    procedure_id: SkillName
    version: int = Field(ge=1, le=1_000_000)
    prior_revision_sha256: Sha256 | None = None
    title: str = Field(min_length=1, max_length=128)
    instructions: str = Field(min_length=1, max_length=16_384)
    preconditions: tuple[StepText, ...] = Field(default=(), max_length=16)
    verification_steps: tuple[StepText, ...] = Field(min_length=1, max_length=32)
    failure_conditions: tuple[StepText, ...] = Field(default=(), max_length=16)
    required_capabilities: tuple[CapabilityId, ...] = Field(min_length=1, max_length=32)
    task_kinds: tuple[SkillName, ...] = Field(default=(), max_length=32)
    tools: tuple[SkillName, ...] = Field(default=(), max_length=64)
    space_permissions: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _canonicalize(self):
        for field_name in (
            "required_capabilities",
            "task_kinds",
            "tools",
            "space_permissions",
        ):
            value = getattr(self, field_name)
            if value != tuple(sorted(set(value))):
                raise ValueError(f"{field_name}_not_canonical")

        for field_name in ("preconditions", "verification_steps", "failure_conditions"):
            value = getattr(self, field_name)
            if len(value) != len(set(value)):
                raise ValueError(f"{field_name}_duplicate")

        for value in (
            self.title,
            self.instructions,
            *self.preconditions,
            *self.verification_steps,
            *self.failure_conditions,
        ):
            if value != value.strip() or "\x00" in value:
                raise ValueError("procedure_text_not_canonical")
        if self.prior_revision_sha256 is not None and self.version == 1:
            raise ValueError("skill_learning_prior_revision_version_invalid")
        return self

    def freeze(self) -> "SkillProcedureRevision":
        material = self.model_dump(mode="json")
        return SkillProcedureRevision.model_validate(
            {**material, "content_sha256": _digest(material)}
        )


class SkillProcedureRevision(SkillProcedureDraft):
    """Content-addressed candidate; metadata here never grants authority."""

    content_sha256: Sha256

    def freeze(self) -> "SkillProcedureRevision":
        return self

    @model_validator(mode="after")
    def _verify_digest(self):
        material = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 != _digest(material):
            raise ValueError("procedure_digest_mismatch")
        return self


class SkillLearningProposal(SkillLearningContract):
    schema_version: Literal["zelo-skill-learning-proposal-v1"] = SKILL_PROPOSAL_SCHEMA
    proposal_id: CanonicalUuid
    agent_instance_id: CanonicalUuid
    agent_id: Identifier
    agent_version: int = Field(ge=1, le=1_000_000)
    agent_registry_version: Sha256
    skill_registry_version: Sha256
    source_skill_id: SkillId
    source_skill_version: int = Field(ge=1, le=1_000_000)
    proposed_revision: SkillProcedureRevision
    evidence_refs: tuple[SkillLearningEvidenceRef, ...] = Field(min_length=1, max_length=16)
    proposal_fingerprint: Sha256
    status: Literal["proposed"] = "proposed"
    authority_widening: Literal[False] = False
    runtime_authority: Literal[False] = False

    @model_validator(mode="after")
    def _canonicalize(self):
        _canonical_uuid(self.agent_instance_id)
        _canonical_uuid(self.proposal_id)
        ordered = tuple(sorted(self.evidence_refs, key=_evidence_key))
        keys = tuple(_evidence_key(item) for item in self.evidence_refs)
        if self.evidence_refs != ordered or len(keys) != len(set(keys)):
            raise ValueError("evidence_refs_not_canonical")
        material = {
            "schema": SKILL_PROPOSAL_SCHEMA,
            "agent_instance_id": self.agent_instance_id,
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "agent_registry_version": self.agent_registry_version,
            "skill_registry_version": self.skill_registry_version,
            "source_skill_id": self.source_skill_id,
            "source_skill_version": self.source_skill_version,
            "proposed_revision": self.proposed_revision.model_dump(mode="json"),
            "evidence_refs": [item.model_dump(mode="json") for item in self.evidence_refs],
            "status": self.status,
            "authority_widening": self.authority_widening,
            "runtime_authority": self.runtime_authority,
        }
        expected = _digest(material)
        if self.proposal_fingerprint != expected or self.proposal_id != _proposal_uuid(expected):
            raise ValueError("proposal_identity_mismatch")
        return self


def _evidence_key(item: SkillLearningEvidenceRef) -> tuple[str, str]:
    return item.kind, item.source_id


EvidenceResolver = Callable[[EvidenceKind, str], SkillLearningEvidenceSnapshot | None]
PriorRevisionResolver = Callable[[str, str], SkillProcedureRevision | None]


def verify_skill_evidence_refs(
    *,
    agent_instance_id: str,
    agent_registry_version: str,
    evidence_refs: tuple[SkillLearningEvidenceRef, ...],
    resolve: EvidenceResolver,
) -> tuple[SkillLearningEvidenceRef, ...]:
    """Check fixture/Core-owned evidence before proposal construction."""

    _canonical_uuid(agent_instance_id)
    evidence = tuple(sorted(evidence_refs, key=_evidence_key))
    keys = tuple(_evidence_key(item) for item in evidence)
    if not evidence or len(evidence) > 16 or len(keys) != len(set(keys)):
        raise ValueError("skill_learning_evidence_invalid")
    for item in evidence:
        recorded = resolve(item.kind, item.source_id)
        if recorded is None or (recorded.kind, recorded.source_id) != _evidence_key(item):
            raise ValueError("skill_learning_evidence_unavailable")
        if (
            recorded.content_sha256 != item.content_sha256
            or recorded.agent_registry_version != agent_registry_version
        ):
            raise ValueError("skill_learning_evidence_stale")
        if not recorded.terminal or not recorded.accepted:
            raise ValueError("skill_learning_evidence_not_accepted")
        if (
            recorded.owner_agent_instance_id != agent_instance_id
            and agent_instance_id not in recorded.visible_to_agent_instance_ids
        ):
            raise ValueError("skill_learning_evidence_out_of_scope")
    return evidence


def propose_skill_revision(
    *,
    agent_instance: SkillLearningAgentSnapshot,
    agent_binding: AgentBinding,
    skill_registry: SkillRegistry,
    revision: SkillProcedureRevision,
    evidence_refs: tuple[SkillLearningEvidenceRef, ...],
    evidence_resolver: EvidenceResolver,
    prior_revision_resolver: PriorRevisionResolver,
) -> SkillLearningProposal:
    """Build one proposal from trusted Core snapshots, without activation."""

    agent = agent_binding.spec
    if agent.lifecycle != "active" or agent.skill_learning != "propose":
        raise ValueError("skill_learning_not_allowed")
    if (
        agent_instance.status != "active"
        or agent_instance.agent_id != agent.agent_id
        or agent_instance.agent_version != agent.version
        or agent_instance.agent_registry_version != agent_binding.registry_version
    ):
        raise ValueError("skill_learning_agent_stale")

    source = skill_registry.get(revision.source_skill_id)
    if (
        source is None
        or source.lifecycle != "active"
        or source.version != revision.source_skill_version
    ):
        raise ValueError("skill_learning_source_stale")

    if revision.procedure_id not in source.procedures:
        raise ValueError("skill_learning_procedure_unavailable")
    if source.skill_id not in agent.skill_ids and revision.procedure_id not in agent.procedures:
        raise ValueError("skill_learning_agent_unbound")

    prior_revision = prior_revision_resolver(revision.source_skill_id, revision.procedure_id)
    if prior_revision is None:
        if revision.prior_revision_sha256 is not None or revision.version != 1:
            raise ValueError("skill_learning_prior_revision_stale")
    elif (
        prior_revision.content_sha256 != revision.prior_revision_sha256
        or prior_revision.source_skill_id != revision.source_skill_id
        or prior_revision.procedure_id != revision.procedure_id
        or prior_revision.version + 1 != revision.version
    ):
        raise ValueError("skill_learning_prior_revision_stale")

    if not set(revision.required_capabilities).issubset(source.required_capabilities):
        raise ValueError("skill_learning_capability_widening")
    if not set(revision.required_capabilities).issubset(agent.capabilities):
        raise ValueError("skill_learning_capability_widening")
    if not set(revision.task_kinds).issubset(source.task_kinds):
        raise ValueError("skill_learning_task_kind_widening")
    if not set(revision.task_kinds).issubset(agent.allowed_task_kinds):
        raise ValueError("skill_learning_task_kind_widening")
    if not set(revision.tools).issubset(source.tools):
        raise ValueError("skill_learning_tool_widening")
    if not set(revision.tools).issubset(agent.allowed_tools):
        raise ValueError("skill_learning_tool_widening")
    if not set(revision.space_permissions).issubset(agent.space_permissions):
        raise ValueError("skill_learning_space_widening")

    evidence = verify_skill_evidence_refs(
        agent_instance_id=agent_instance.agent_instance_id,
        agent_registry_version=agent_binding.registry_version,
        evidence_refs=evidence_refs,
        resolve=evidence_resolver,
    )
    payload = {
        "schema": SKILL_PROPOSAL_SCHEMA,
        "agent_instance_id": agent_instance.agent_instance_id,
        "agent_id": agent.agent_id,
        "agent_version": agent.version,
        "agent_registry_version": agent_binding.registry_version,
        "skill_registry_version": skill_registry.version,
        "source_skill_id": source.skill_id,
        "source_skill_version": source.version,
        "proposed_revision": revision.model_dump(mode="json"),
        "evidence_refs": [item.model_dump(mode="json") for item in evidence],
        "status": "proposed",
        "authority_widening": False,
        "runtime_authority": False,
    }
    fingerprint = _digest(payload)
    return SkillLearningProposal(
        proposal_id=_proposal_uuid(fingerprint),
        agent_instance_id=agent_instance.agent_instance_id,
        agent_id=agent.agent_id,
        agent_version=agent.version,
        agent_registry_version=agent_binding.registry_version,
        skill_registry_version=skill_registry.version,
        source_skill_id=source.skill_id,
        source_skill_version=source.version,
        proposed_revision=revision,
        evidence_refs=evidence,
        proposal_fingerprint=fingerprint,
    )


class SkillLearningReviewChecks(SkillLearningContract):
    """Human review attestations required before acceptance."""

    evidence_supports_change: StrictBool
    no_tool_escalation: StrictBool
    authority_envelope_respected: StrictBool
    verification_and_failure_steps_explicit: StrictBool
    no_secrets_or_unrelated_private_data: StrictBool
    procedure_bounded_and_deterministic: StrictBool

    def all_passed(self) -> bool:
        return all(self.model_dump().values())


class SkillLearningReviewRequest(SkillLearningContract):
    """Decision payload. Reviewer identity comes only from operator auth."""

    decision: Literal["accepted", "rejected"]
    checks: SkillLearningReviewChecks
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _validate_rationale(self):
        if self.rationale != self.rationale.strip() or "\x00" in self.rationale:
            raise ValueError("skill_learning_review_rationale_invalid")
        if self.decision == "accepted" and not self.checks.all_passed():
            raise ValueError("skill_learning_review_checks_failed")
        return self


class SkillLearningReview(SkillLearningContract):
    """Immutable review bound to the frozen candidate and live authority snapshot."""

    schema_version: Literal["zelo-skill-learning-review-v1"] = "zelo-skill-learning-review-v1"
    review_id: CanonicalUuid
    proposal_id: CanonicalUuid
    proposal_fingerprint: Sha256
    reviewer_id: Literal["ahmad"]
    decision: Literal["accepted", "rejected"]
    agent_instance_id: CanonicalUuid
    agent_id: Identifier
    agent_version: int = Field(ge=1, le=1_000_000)
    agent_registry_version: Sha256
    skill_registry_version: Sha256
    source_skill_id: SkillId
    source_skill_version: int = Field(ge=1, le=1_000_000)
    procedure_id: SkillName
    revision_sha256: Sha256
    evidence_refs: tuple[SkillLearningEvidenceRef, ...] = Field(min_length=1, max_length=16)
    authority_envelope_sha256: Sha256
    checks: SkillLearningReviewChecks
    rationale: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime
    review_fingerprint: Sha256

    @model_validator(mode="after")
    def _validate_review(self):
        _canonical_uuid(self.review_id)
        _canonical_uuid(self.proposal_id)
        _canonical_uuid(self.agent_instance_id)
        evidence = tuple(sorted(self.evidence_refs, key=_evidence_key))
        keys = tuple(_evidence_key(item) for item in self.evidence_refs)
        if evidence != self.evidence_refs or len(keys) != len(set(keys)):
            raise ValueError("skill_learning_review_evidence_not_canonical")
        if self.reviewed_at.tzinfo is None:
            raise ValueError("skill_learning_review_timestamp_invalid")
        if self.rationale != self.rationale.strip() or "\x00" in self.rationale:
            raise ValueError("skill_learning_review_rationale_invalid")
        material = self.model_dump(
            mode="json",
            exclude={"review_id", "reviewed_at", "review_fingerprint"},
        )
        expected = _digest(material)
        expected_id = str(uuid5(NAMESPACE_URL, f"zelo-skill-learning-review-v1:{expected}"))
        if self.review_fingerprint != expected or self.review_id != expected_id:
            raise ValueError("skill_learning_review_identity_mismatch")
        if self.decision == "accepted" and not self.checks.all_passed():
            raise ValueError("skill_learning_review_checks_failed")
        return self


class LearnedSkillEvidenceSummary(SkillLearningContract):
    """Bounded evidence metadata for the read-only per-Agent learned-skill view."""

    count: int = Field(ge=1, le=16)
    kinds: tuple[EvidenceKind, ...] = Field(min_length=1, max_length=3)
    fingerprint: Sha256

    @model_validator(mode="after")
    def _canonicalize(self):
        if self.kinds != tuple(sorted(set(self.kinds))):
            raise ValueError("learned_skill_evidence_kinds_not_canonical")
        if self.count < len(self.kinds):
            raise ValueError("learned_skill_evidence_count_invalid")
        return self


class LearnedSkillProposalView(SkillLearningContract):
    """Metadata-only projection of one governed proposal; never executable content."""

    proposal_id: CanonicalUuid
    proposal_fingerprint: Sha256
    source_skill_id: SkillId
    source_skill_version: int = Field(ge=1, le=1_000_000)
    procedure_id: SkillName
    proposed_version: int = Field(ge=1, le=1_000_000)
    revision_sha256: Sha256
    prior_revision_sha256: Sha256 | None = None
    title: str = Field(min_length=1, max_length=128)
    review_status: Literal["pending", "accepted", "rejected"]
    review_fingerprint: Sha256 | None = None
    current: bool
    evidence: LearnedSkillEvidenceSummary
    promotion_status: Literal["not_eligible", "not_promoted"]
    active_revision_sha256: None = None
    runtime_authority: Literal[False] = False
    created_at: datetime
    updated_at: datetime


class LearnedSkillProposalPage(SkillLearningContract):
    """Read-only Agent package projection; active promoted identity is not resolved yet."""

    agent_instance_id: CanonicalUuid
    items: tuple[LearnedSkillProposalView, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    promoted_revision_resolver_available: Literal[False] = False
