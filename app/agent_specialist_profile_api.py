"""Operator-only, read-only Specialist Dot inspection API."""

from fastapi import APIRouter, Depends, Request

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
