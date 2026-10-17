"""Authenticated owner review of attested Space roots and confirmations.

No endpoint returns file bytes, starts a Worker or changes the Computer grants.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_space_resources import (
    AgentSpaceResourceService,
    SpaceActivationPage,
    SpaceActivationReceipt,
    SpaceApprovalRevocationReceipt,
    SpaceResourcePage,
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


class SpaceProvisionRoute(APIRoute):
    """This document-input route never reflects invalid body content."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # Default errors echo input, and malformed Unicode cannot be
                # encoded safely in that response. Keep this route's denial fixed.
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Space provisioning request invalid"},
                )

        return validated


class SpaceSeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=96)
    text: str = Field(max_length=65536)


class SpaceResourceProvision(SpaceResourceCreate):
    files: tuple[SpaceSeedFile, ...] = Field(default=(), max_length=16)


class SpaceRevokeAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: bool

    @model_validator(mode="after")
    def _confirmed(self):
        if self.confirm is not True:
            raise ValueError("space_revocation_requires_explicit_confirmation")
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


@router.get("/{instance_id}/space-resources", response_model=SpaceResourcePage)
def list_space_resources(
    instance_id: str,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    space_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> SpaceResourcePage:
    return _service(request).list_resources(
        instance_id=instance_id, limit=limit, offset=offset, space_id=space_id
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


def provision_space_resource(
    instance_id: str, body: SpaceResourceProvision, request: Request
) -> SpaceResourceReceipt:
    """Owner-supplied text initializes a new resource; no Agent write grant."""
    return _service(request).provision(
        instance_id=instance_id,
        space_id=body.space_id,
        resource_id=body.resource_id,
        operations=body.operations,
        files=tuple((item.name, item.text) for item in body.files),
    )


router.add_api_route(
    "/{instance_id}/space-resources/provision",
    provision_space_resource,
    methods=["POST"],
    response_model=SpaceResourceReceipt,
    status_code=201,
    route_class_override=SpaceProvisionRoute,
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


@router.get("/{instance_id}/space-activations", response_model=SpaceActivationPage)
def list_space_activations(
    instance_id: str,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> SpaceActivationPage:
    return _service(request).list_activations(instance_id=instance_id, limit=limit, offset=offset)


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


@router.post(
    "/{instance_id}/space-approvals/revoke-all",
    response_model=SpaceApprovalRevocationReceipt,
)
def revoke_all_space_approvals(
    instance_id: str, body: SpaceRevokeAllRequest, request: Request
) -> SpaceApprovalRevocationReceipt:
    """One authenticated owner command withdraws only this Agent's old approvals."""
    return _service(request).revoke_all(instance_id=instance_id)
