"""Owner-only Core routes for persistent Agent computers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.agent_computer_service import AgentComputerService
from app.auth import require_operator
from app.community_computer_panel import ComputerPanelContract, project_computer_panel
from app.schemas import (
    AgentComputerEventPage,
    AgentComputerPermissionPatch,
    AgentComputerStatusResponse,
)

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


def service(request: Request) -> AgentComputerService:
    return request.app.state.agent_computer_service


Computers = Annotated[AgentComputerService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.get(
    "/{instance_id}/computer",
    response_model=AgentComputerStatusResponse,
)
async def get_agent_computer(
    instance_id: str,
    computers: Computers,
) -> AgentComputerStatusResponse:
    return await computers.status(instance_id)


@router.get("/{instance_id}/computer/panel", response_model=ComputerPanelContract)
async def get_computer_panel_preview(
    instance_id: str,
    computers: Computers,
) -> ComputerPanelContract:
    """Read-only OpenDots-compatible panel metadata; no new Computer authority."""

    return await project_computer_panel(computers, instance_id)


@router.patch(
    "/{instance_id}/computer/permissions",
    response_model=AgentComputerStatusResponse,
)
async def patch_agent_computer_permissions(
    instance_id: str,
    body: AgentComputerPermissionPatch,
    computers: Computers,
) -> AgentComputerStatusResponse:
    return await computers.patch_permissions(instance_id, body)


@router.post(
    "/{instance_id}/computer/start",
    response_model=AgentComputerStatusResponse,
)
async def start_agent_computer(
    instance_id: str,
    computers: Computers,
) -> AgentComputerStatusResponse:
    return await computers.start(instance_id)


@router.post(
    "/{instance_id}/computer/stop",
    response_model=AgentComputerStatusResponse,
)
async def stop_agent_computer(
    instance_id: str,
    computers: Computers,
) -> AgentComputerStatusResponse:
    return await computers.stop(instance_id)


@router.post(
    "/{instance_id}/computer/take",
    response_model=AgentComputerStatusResponse,
)
async def take_agent_computer(
    instance_id: str,
    computers: Computers,
) -> AgentComputerStatusResponse:
    return await computers.take(instance_id)


@router.post(
    "/{instance_id}/computer/release",
    response_model=AgentComputerStatusResponse,
)
async def release_agent_computer(
    instance_id: str,
    computers: Computers,
) -> AgentComputerStatusResponse:
    return await computers.release(instance_id)


@router.get(
    "/{instance_id}/computer/events",
    response_model=AgentComputerEventPage,
)
def list_agent_computer_events(
    instance_id: str,
    computers: Computers,
    limit: Limit = 100,
    offset: Offset = 0,
) -> AgentComputerEventPage:
    return computers.events(instance_id, limit=limit, offset=offset)
