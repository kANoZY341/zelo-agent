"""Authenticated organizational hierarchy routes for v0.14."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.organization import OrganizationService
from app.schemas import (
    OrganizationAssignmentCreate,
    OrganizationAssignmentEnd,
    OrganizationAssignmentPage,
    OrganizationAssignmentResponse,
    OrganizationAssignmentStatus,
    OrganizationSnapshot,
    OrganizationUnitCreate,
    OrganizationUnitPage,
    OrganizationUnitResponse,
    OrganizationUnitStatus,
)

router = APIRouter(prefix="/organization", dependencies=[Depends(require_operator)])


def service(request: Request) -> OrganizationService:
    return request.app.state.organization


Organization = Annotated[OrganizationService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.get("/snapshot", response_model=OrganizationSnapshot)
def organization_snapshot(organization: Organization) -> OrganizationSnapshot:
    return organization.snapshot()


@router.post("/units", response_model=OrganizationUnitResponse, status_code=201)
def create_unit(
    body: OrganizationUnitCreate,
    organization: Organization,
) -> OrganizationUnitResponse:
    return organization.create_unit(body)


@router.get("/units", response_model=OrganizationUnitPage)
def list_units(
    organization: Organization,
    status: OrganizationUnitStatus | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> OrganizationUnitPage:
    return organization.list_units(status=status, limit=limit, offset=offset)


@router.post("/units/{unit_id}/retire", response_model=OrganizationUnitResponse)
def retire_unit(
    unit_id: str,
    organization: Organization,
) -> OrganizationUnitResponse:
    return organization.retire_unit(unit_id)


@router.post(
    "/assignments",
    response_model=OrganizationAssignmentResponse,
    status_code=201,
)
def create_assignment(
    body: OrganizationAssignmentCreate,
    organization: Organization,
) -> OrganizationAssignmentResponse:
    return organization.create_assignment(body)


@router.get("/assignments", response_model=OrganizationAssignmentPage)
def list_assignments(
    organization: Organization,
    unit_id: str | None = None,
    status: OrganizationAssignmentStatus | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> OrganizationAssignmentPage:
    return organization.list_assignments(
        unit_id=unit_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/assignments/{assignment_id}/end",
    response_model=OrganizationAssignmentResponse,
)
def end_assignment(
    assignment_id: str,
    body: OrganizationAssignmentEnd,
    organization: Organization,
) -> OrganizationAssignmentResponse:
    return organization.end_assignment(assignment_id, body)
