"""OpenDots-inspired read-only per-AgentInstance learning proposal visibility.

Historical proposals are not learned executable skills. A review decision
never grants runtime procedures, tools, Spaces, task authority or publication.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_specialist_profile import AgentSpecialistProfile
from app.skill_learning_store import SkillLearningProposalStore


class LearningHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    proposal_id: str
    source_skill_id: str
    source_skill_version: int
    proposed_procedure_id: str
    proposed_procedure_version: int
    proposed_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_status: Literal["proposed", "accepted", "rejected"]
    review_recorded: bool
    promotion_evidence_linked: bool
    created_at: datetime
    origin: Literal["core_governed_skill_learning_store"] = "core_governed_skill_learning_store"
    execution_enabled: Literal[False] = False
    skill_registry_promoted_by_this_view: Literal[False] = False
    procedure_content_included: Literal[False] = False


class AgentLearningHistoryView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-opendots-agent-learning-history-v1"] = (
        "zelo-opendots-agent-learning-history-v1"
    )
    agent_instance_id: str
    registry_binding_current: bool
    inspection_state: Literal["current", "instance_inactive", "project_inactive", "stale"]
    items: tuple[LearningHistoryEntry, ...] = Field(max_length=32)
    total_count: int = Field(ge=0)
    more_available: bool
    automatically_learned_or_activated: Literal[False] = False
    permission_widening: Literal[False] = False
    model_context_admission: Literal[False] = False


def agent_learning_history(
    profile: AgentSpecialistProfile, store: SkillLearningProposalStore
) -> AgentLearningHistoryView:
    """Project persisted, integrity-checked metadata for only this instance."""

    if type(profile) is not AgentSpecialistProfile or not isinstance(
        store, SkillLearningProposalStore
    ):
        raise ValueError("agent_learning_history_source_invalid")
    records, total = store.list_for_agent(profile.agent_instance_id, limit=32, offset=0)
    promotion_links = store.promotion_links_for(
        tuple(item.proposal.proposal_id for item in records)
    )
    entries = tuple(
        LearningHistoryEntry(
            proposal_id=item.proposal.proposal_id,
            source_skill_id=item.proposal.source_skill_id,
            source_skill_version=item.proposal.source_skill_version,
            proposed_procedure_id=item.proposal.proposed_revision.procedure_id,
            proposed_procedure_version=item.proposal.proposed_revision.version,
            proposed_revision_sha256=item.proposal.proposed_revision.content_sha256,
            proposal_status=item.status,
            review_recorded=item.review is not None,
            promotion_evidence_linked=item.proposal.proposal_id in promotion_links,
            created_at=item.created_at,
        )
        for item in records
    )
    return AgentLearningHistoryView(
        agent_instance_id=profile.agent_instance_id,
        registry_binding_current=profile.registry_binding_current,
        inspection_state=profile.inspection_state,
        items=entries,
        total_count=total,
        more_available=total > len(records),
    )
