"""Authenticated owner review of attested Space roots and confirmations.

No endpoint returns file bytes, starts a Worker or changes the Computer grants.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_space_resources import (
    AgentSpaceResourceService,
    SpaceActivationReceipt,
    SpaceResourceReceipt,
)
from app.auth import require_operator

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


class SpaceResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_id: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=64)
    resource_kind: Literal["files"] = "files"
    operations: tuple[Literal["read", "list"], ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _canonical(self):
        if self.operations != tuple(sorted(set(self.operations))):
            raise ValueError("space_resource_operations_not_canonical")
        return self


class SpaceActivationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_record_id: str = Field(min_length=36, max_length=36)


def _service(request: Request) -> AgentSpaceResourceService:
    return AgentSpaceResourceService(
        request.app.state.storage,
        request.app.state.agent_factory,
        request.app.state.settings.runtime_dir,
    )


@router.post("/{instance_id}/space-resources", response_model=SpaceResourceReceipt, status_code=201)
def register_space_resource(
    instance_id: str, body: SpaceResourceCreate, request: Request
) -> SpaceResourceReceipt:
    return _service(request).register(
        instance_id=instance_id,
        space_id=body.space_id,
        resource_id=body.resource_id,
        operations=body.operations,
    )


@router.get(
    "/{instance_id}/space-resources/{resource_record_id}", response_model=SpaceResourceReceipt
)
def get_space_resource(
    instance_id: str, resource_record_id: str, request: Request
) -> SpaceResourceReceipt:
    return _service(request).get_resource(
        instance_id=instance_id, resource_record_id=resource_record_id
    )


@router.post(
    "/{instance_id}/space-resources/{resource_record_id}/retire",
    response_model=SpaceResourceReceipt,
)
def retire_space_resource(
    instance_id: str, resource_record_id: str, request: Request
) -> SpaceResourceReceipt:
    return _service(request).retire(instance_id=instance_id, resource_record_id=resource_record_id)


@router.post(
    "/{instance_id}/space-intents/{intent_id}/activate",
    response_model=SpaceActivationReceipt,
    status_code=201,
)
def confirm_space_intent(
    instance_id: str, intent_id: str, body: SpaceActivationCreate, request: Request
) -> SpaceActivationReceipt:
    return _service(request).activate(
        instance_id=instance_id,
        intent_id=intent_id,
        resource_record_id=body.resource_record_id,
    )


@router.get(
    "/{instance_id}/space-activations/{activation_id}", response_model=SpaceActivationReceipt
)
def get_space_activation(
    instance_id: str, activation_id: str, request: Request
) -> SpaceActivationReceipt:
    return _service(request).get_activation(instance_id=instance_id, activation_id=activation_id)


@router.post(
    "/{instance_id}/space-activations/{activation_id}/revoke",
    response_model=SpaceActivationReceipt,
)
def revoke_space_activation(
    instance_id: str, activation_id: str, request: Request
) -> SpaceActivationReceipt:
    return _service(request).revoke(instance_id=instance_id, activation_id=activation_id)
