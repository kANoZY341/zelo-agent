"""Authenticated hierarchy-aware Agent Factory child creation route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.agent_child_creation import AgentChildCreationService
from app.auth import require_operator
from app.schemas import AgentChildCreate, AgentChildCreationResponse

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


def service(request: Request) -> AgentChildCreationService:
    return request.app.state.agent_child_creation


ChildCreation = Annotated[AgentChildCreationService, Depends(service)]


@router.post(
    "/{parent_instance_id}/children",
    response_model=AgentChildCreationResponse,
    status_code=201,
)
def create_child_agent(
    parent_instance_id: str,
    body: AgentChildCreate,
    factory: ChildCreation,
) -> AgentChildCreationResponse:
    return factory.create(parent_instance_id, body)
