"""Explicit owner-approved inert Space intention routes: no resource access."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_space_intents import AgentSpaceIntentService, SpaceIntentPage, SpaceIntentReceipt
from app.auth import require_operator

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


class SpaceIntentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_id: str = Field(min_length=1, max_length=64)
    resource_kind: Literal["files", "browser", "terminal", "memory"]
    resource_id: str = Field(min_length=1, max_length=64)
    operations: tuple[Literal["read", "list"], ...] = Field(min_length=1, max_length=2)
    ttl_seconds: int = Field(ge=60, le=3600, strict=True)

    @model_validator(mode="after")
    def _canonical(self):
        if self.operations != tuple(sorted(set(self.operations))):
            raise ValueError("space_intent_operations_not_canonical")
        return self


def _service(request: Request) -> AgentSpaceIntentService:
    return AgentSpaceIntentService(request.app.state.storage, request.app.state.agent_factory)


@router.get("/{instance_id}/space-intents", response_model=SpaceIntentPage)
def list_space_intents(
    instance_id: str,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> SpaceIntentPage:
    return _service(request).list_intents(instance_id=instance_id, limit=limit, offset=offset)


@router.post("/{instance_id}/space-intents", response_model=SpaceIntentReceipt, status_code=201)
def create_space_intent(
    instance_id: str, body: SpaceIntentCreate, request: Request
) -> SpaceIntentReceipt:
    return _service(request).create(
        instance_id=instance_id,
        space_id=body.space_id,
        resource_kind=body.resource_kind,
        resource_id=body.resource_id,
        operations=body.operations,
        ttl_seconds=body.ttl_seconds,
    )


@router.get("/{instance_id}/space-intents/{intent_id}", response_model=SpaceIntentReceipt)
def get_space_intent(instance_id: str, intent_id: str, request: Request) -> SpaceIntentReceipt:
    return _service(request).get(instance_id=instance_id, intent_id=intent_id)


@router.post(
    "/{instance_id}/space-intents/{intent_id}/revoke",
    response_model=SpaceIntentReceipt,
)
def revoke_space_intent(instance_id: str, intent_id: str, request: Request) -> SpaceIntentReceipt:
    return _service(request).revoke(instance_id=instance_id, intent_id=intent_id)
