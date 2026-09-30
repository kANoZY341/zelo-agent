"""Authenticated v0.14 agent delegation contract routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.agent_delegations import AgentDelegationService
from app.auth import require_operator
from app.schemas import (
    AgentDelegationCreate,
    AgentDelegationPage,
    AgentDelegationResponse,
    AgentDelegationRevoke,
    AgentDelegationStatus,
)

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


def service(request: Request) -> AgentDelegationService:
    return request.app.state.agent_delegations


Delegations = Annotated[AgentDelegationService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post(
    "/{parent_instance_id}/delegations",
    response_model=AgentDelegationResponse,
    status_code=201,
)
def create_delegation(
    parent_instance_id: str,
    body: AgentDelegationCreate,
    delegations: Delegations,
) -> AgentDelegationResponse:
    return delegations.create(parent_instance_id, body)


@router.get(
    "/{parent_instance_id}/delegations",
    response_model=AgentDelegationPage,
)
def list_delegations(
    parent_instance_id: str,
    delegations: Delegations,
    status: AgentDelegationStatus | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> AgentDelegationPage:
    return delegations.list(
        parent_instance_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/delegations/{delegation_id}", response_model=AgentDelegationResponse)
def get_delegation(
    delegation_id: str,
    delegations: Delegations,
) -> AgentDelegationResponse:
    return delegations.get(delegation_id)


@router.post(
    "/delegations/{delegation_id}/revoke",
    response_model=AgentDelegationResponse,
)
def revoke_delegation(
    delegation_id: str,
    body: AgentDelegationRevoke,
    delegations: Delegations,
) -> AgentDelegationResponse:
    return delegations.revoke(delegation_id, body)
