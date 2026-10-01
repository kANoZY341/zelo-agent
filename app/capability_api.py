"""Operator-visible trusted capability registry."""

from fastapi import APIRouter, Depends, Request

from app.auth import require_operator
from app.capabilities import CapabilityRegistrySnapshot

router = APIRouter(dependencies=[Depends(require_operator)])


@router.get("/capabilities", response_model=CapabilityRegistrySnapshot)
def get_capabilities(request: Request) -> CapabilityRegistrySnapshot:
    return request.app.state.capability_registry.snapshot()
