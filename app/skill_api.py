"""Operator-visible trusted skill registry."""

from fastapi import APIRouter, Depends, Request

from app.auth import require_operator
from app.skills import SkillRegistrySnapshot

router = APIRouter(dependencies=[Depends(require_operator)])


@router.get("/skills", response_model=SkillRegistrySnapshot)
def get_skills(request: Request) -> SkillRegistrySnapshot:
    return request.app.state.skill_registry.snapshot()
