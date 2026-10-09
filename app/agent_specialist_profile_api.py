"""Operator-only, read-only Specialist Dot inspection API."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent_learned_skill_history import AgentLearningHistoryView, agent_learning_history
from app.agent_specialist_profile import (
    AgentSpecialistProfile,
    AgentSpecialistProfileService,
)
from app.auth import require_operator

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


@router.get("/{instance_id}/specialist-profile", response_model=AgentSpecialistProfile)
def read_specialist_profile(instance_id: str, request: Request) -> AgentSpecialistProfile:
    """Reflect trusted registry only; do not activate skills or issue grants."""

    store = AgentSpecialistProfileService(
        request.app.state.database,
        request.app.state.agent_factory,
        request.app.state.skill_registry,
    )
    return store.get(instance_id)


@router.get(
    "/{instance_id}/specialist-profile/learning",
    response_model=AgentLearningHistoryView,
)
def read_agent_learning_history(instance_id: str, request: Request) -> AgentLearningHistoryView:
    """Inspect only bounded, verified proposal metadata; no learned action rights."""

    profile = AgentSpecialistProfileService(
        request.app.state.database,
        request.app.state.agent_factory,
        request.app.state.skill_registry,
    ).get(instance_id)
    try:
        return agent_learning_history(profile, request.app.state.skill_learning_store)
    except ValueError:
        raise HTTPException(status_code=503, detail="agent_learning_history_unavailable") from None
