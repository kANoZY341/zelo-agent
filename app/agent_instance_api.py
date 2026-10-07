"""Authenticated v0.14 project-bound agent instance routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal, require_operator
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceEventPage,
    AgentInstancePage,
    AgentInstanceResponse,
    AgentInstanceStatus,
    AgentInstanceTransition,
    AgentLearnedSkillItem,
    AgentLearnedSkillPage,
    AgentMemoryContextRequest,
    AgentMemoryContextResponse,
)
from app.skill_learning_store import SkillLearningProposalStore

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


def service(request: Request) -> AgentInstanceService:
    return request.app.state.agent_instances


def learning_store(request: Request) -> SkillLearningProposalStore:
    return request.app.state.skill_learning_store


Agents = Annotated[AgentInstanceService, Depends(service)]
LearningStore = Annotated[SkillLearningProposalStore, Depends(learning_store)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
ProjectId = Annotated[
    str | None,
    Query(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


@router.post("", response_model=AgentInstanceResponse, status_code=201)
def create_agent_instance(
    body: AgentInstanceCreate,
    agents: Agents,
) -> AgentInstanceResponse:
    return agents.create(body)


@router.get("", response_model=AgentInstancePage)
def list_agent_instances(
    agents: Agents,
    project_id: ProjectId = None,
    status: AgentInstanceStatus | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> AgentInstancePage:
    return agents.list(
        project_id=project_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{instance_id}", response_model=AgentInstanceResponse)
def get_agent_instance(
    instance_id: str,
    agents: Agents,
) -> AgentInstanceResponse:
    return agents.get(instance_id)


@router.get("/{instance_id}/learned-skills", response_model=AgentLearnedSkillPage)
def list_agent_learned_skills(
    instance_id: str,
    _principal: Annotated[OperatorPrincipal, Depends(require_operator)],
    agents: Agents,
    store: LearningStore,
    limit: Limit = 20,
    offset: Offset = 0,
) -> AgentLearnedSkillPage:
    agents.get(instance_id)
    rows, total = store.list_for_agent(instance_id, limit=limit, offset=offset)
    items = []
    for stored in rows:
        proposal = stored.proposal
        revision = proposal.proposed_revision
        review = stored.review
        items.append(
            AgentLearnedSkillItem(
                proposal_id=proposal.proposal_id,
                source_skill_id=proposal.source_skill_id,
                source_skill_version=proposal.source_skill_version,
                procedure_id=revision.procedure_id,
                proposed_version=revision.version,
                revision_sha256=revision.content_sha256,
                prior_revision_sha256=revision.prior_revision_sha256,
                status=stored.status,
                evidence_count=len(proposal.evidence_refs),
                review_id=review.review_id if review is not None else None,
                review_fingerprint=review.review_fingerprint if review is not None else None,
                created_at=stored.created_at,
                updated_at=stored.updated_at,
            )
        )
    return AgentLearnedSkillPage(items=items, limit=limit, offset=offset, total=total)


@router.post("/{instance_id}/transition", response_model=AgentInstanceResponse)
def transition_agent_instance(
    instance_id: str,
    body: AgentInstanceTransition,
    agents: Agents,
) -> AgentInstanceResponse:
    return agents.transition(instance_id, body)


@router.get("/{instance_id}/events", response_model=AgentInstanceEventPage)
def list_agent_instance_events(
    instance_id: str,
    agents: Agents,
    limit: Limit = 100,
    offset: Offset = 0,
) -> AgentInstanceEventPage:
    return agents.events(instance_id, limit=limit, offset=offset)


@router.post("/{instance_id}/memory-context", response_model=AgentMemoryContextResponse)
def resolve_agent_memory_context(
    instance_id: str,
    body: AgentMemoryContextRequest,
    agents: Agents,
) -> AgentMemoryContextResponse:
    return agents.memory_context(instance_id, body)
