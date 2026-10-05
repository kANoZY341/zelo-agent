"""Governed evidence-backed Agent skill-learning proposals for v0.15.

This module is deliberately pure: it cannot persist, approve, promote, execute,
or activate a learned procedure.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_factory import AgentBinding, Identifier
from app.capabilities import CapabilityId
from app.skills import SkillId, SkillName, SkillRegistry

SKILL_EVIDENCE_SCHEMA = "zelo-skill-learning-evidence-v1"
SKILL_PROCEDURE_SCHEMA = "zelo-skill-procedure-revision-v1"
SKILL_PROPOSAL_SCHEMA = "zelo-skill-learning-proposal-v1"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
InstanceId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$"),
]
EvidenceId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
StepText = Annotated[str, Field(min_length=1, max_length=512)]
EvidenceKind = Literal["task_attempt", "promotion_review", "operator_feedback"]


class SkillLearningContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillLearningEvidenceRef(SkillLearningContract):
    schema_version: Literal["zelo-skill-learning-evidence-v1"] = SKILL_EVIDENCE_SCHEMA
    kind: EvidenceKind
    source_id: EvidenceId
    content_sha256: Sha256


class SkillProcedureRevision(SkillLearningContract):
    """Candidate procedure content; metadata here never grants authority."""

    schema_version: Literal["zelo-skill-procedure-revision-v1"] = SKILL_PROCEDURE_SCHEMA
    source_skill_id: SkillId
    source_skill_version: int = Field(ge=1, le=1_000_000)
    procedure_id: SkillName
    version: int = Field(ge=1, le=1_000_000)
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
        return self


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


class SkillLearningProposal(SkillLearningContract):
    schema_version: Literal["zelo-skill-learning-proposal-v1"] = SKILL_PROPOSAL_SCHEMA
    proposal_id: Sha256
    agent_instance_id: InstanceId
    agent_id: Identifier
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
        ordered = tuple(sorted(self.evidence_refs, key=_evidence_key))
        keys = tuple(_evidence_key(item) for item in self.evidence_refs)
        if self.evidence_refs != ordered or len(keys) != len(set(keys)):
            raise ValueError("evidence_refs_not_canonical")
        material = {
            "schema": SKILL_PROPOSAL_SCHEMA,
            "agent_instance_id": self.agent_instance_id,
            "agent_id": self.agent_id,
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
        if self.proposal_id != expected or self.proposal_fingerprint != expected:
            raise ValueError("proposal_identity_mismatch")
        return self


def _evidence_key(
    item: SkillLearningEvidenceRef,
) -> tuple[str, str, str]:
    return item.kind, item.source_id, item.content_sha256


def propose_skill_revision(
    *,
    agent_instance_id: str,
    agent_binding: AgentBinding,
    skill_registry: SkillRegistry,
    revision: SkillProcedureRevision,
    evidence_refs: tuple[SkillLearningEvidenceRef, ...],
) -> SkillLearningProposal:
    """Build one immutable proposal without approving or activating it."""

    agent = agent_binding.spec
    if agent.lifecycle != "active" or agent.skill_learning != "propose":
        raise ValueError("skill_learning_not_allowed")

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

    evidence = tuple(sorted(evidence_refs, key=_evidence_key))
    evidence_keys = tuple(_evidence_key(item) for item in evidence)
    if not evidence or len(evidence) > 16 or len(evidence_keys) != len(set(evidence_keys)):
        raise ValueError("skill_learning_evidence_invalid")

    payload = {
        "schema": SKILL_PROPOSAL_SCHEMA,
        "agent_instance_id": agent_instance_id,
        "agent_id": agent.agent_id,
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
        proposal_id=fingerprint,
        agent_instance_id=agent_instance_id,
        agent_id=agent.agent_id,
        agent_registry_version=agent_binding.registry_version,
        skill_registry_version=skill_registry.version,
        source_skill_id=source.skill_id,
        source_skill_version=source.version,
        proposed_revision=revision,
        evidence_refs=evidence,
        proposal_fingerprint=fingerprint,
    )
