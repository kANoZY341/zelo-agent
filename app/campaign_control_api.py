"""Authenticated v0.12 Campaign lease, recovery, and budget routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.campaign_control import CampaignControlService
from app.schemas import (
    CampaignBudgetCommit,
    CampaignBudgetPage,
    CampaignBudgetReservationPage,
    CampaignBudgetReservationResponse,
    CampaignBudgetReserve,
    CampaignBudgetResponse,
    CampaignBudgetSet,
    CampaignLeaseAcquire,
    CampaignLeaseGrantResponse,
    CampaignLeaseRelease,
    CampaignLeaseRenew,
    CampaignLeaseStatusResponse,
    CampaignRecoveryResponse,
)

router = APIRouter(dependencies=[Depends(require_operator)])


def service(request: Request) -> CampaignControlService:
    return request.app.state.campaign_controls


Controls = Annotated[CampaignControlService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post(
    "/campaigns/{campaign_id}/leases/{scope}/acquire",
    response_model=CampaignLeaseGrantResponse,
    status_code=201,
)
def acquire_lease(
    campaign_id: str,
    scope: str,
    body: CampaignLeaseAcquire,
    controls: Controls,
) -> CampaignLeaseGrantResponse:
    return controls.acquire_lease(campaign_id, scope, body)


@router.get(
    "/campaigns/{campaign_id}/leases/{scope}",
    response_model=CampaignLeaseStatusResponse,
)
def lease_status(
    campaign_id: str,
    scope: str,
    controls: Controls,
) -> CampaignLeaseStatusResponse:
    return controls.lease_status(campaign_id, scope)


@router.post(
    "/campaigns/{campaign_id}/leases/{scope}/renew",
    response_model=CampaignLeaseGrantResponse,
)
def renew_lease(
    campaign_id: str,
    scope: str,
    body: CampaignLeaseRenew,
    controls: Controls,
) -> CampaignLeaseGrantResponse:
    return controls.renew_lease(campaign_id, scope, body)


@router.post(
    "/campaigns/{campaign_id}/leases/{scope}/release",
    response_model=CampaignLeaseStatusResponse,
)
def release_lease(
    campaign_id: str,
    scope: str,
    body: CampaignLeaseRelease,
    controls: Controls,
) -> CampaignLeaseStatusResponse:
    return controls.release_lease(campaign_id, scope, body)


@router.put(
    "/campaigns/{campaign_id}/budgets/{resource}",
    response_model=CampaignBudgetResponse,
)
def set_budget(
    campaign_id: str,
    resource: str,
    body: CampaignBudgetSet,
    controls: Controls,
) -> CampaignBudgetResponse:
    return controls.set_budget(campaign_id, resource, body)


@router.get(
    "/campaigns/{campaign_id}/budgets/{resource}",
    response_model=CampaignBudgetResponse,
)
def get_budget(
    campaign_id: str,
    resource: str,
    controls: Controls,
) -> CampaignBudgetResponse:
    return controls.get_budget(campaign_id, resource)


@router.get(
    "/campaigns/{campaign_id}/budgets",
    response_model=CampaignBudgetPage,
)
def list_budgets(
    campaign_id: str,
    controls: Controls,
    limit: Limit = 100,
    offset: Offset = 0,
) -> CampaignBudgetPage:
    return controls.list_budgets(campaign_id, limit, offset)


@router.post(
    "/campaigns/{campaign_id}/budget-reservations",
    response_model=CampaignBudgetReservationResponse,
    status_code=201,
)
def reserve_budget(
    campaign_id: str,
    body: CampaignBudgetReserve,
    controls: Controls,
) -> CampaignBudgetReservationResponse:
    return controls.reserve_budget(campaign_id, body)


@router.get(
    "/campaigns/{campaign_id}/budget-reservations",
    response_model=CampaignBudgetReservationPage,
)
def list_budget_reservations(
    campaign_id: str,
    controls: Controls,
    limit: Limit = 100,
    offset: Offset = 0,
) -> CampaignBudgetReservationPage:
    return controls.list_reservations(campaign_id, limit, offset)


@router.post(
    "/campaigns/{campaign_id}/budget-reservations/{reservation_id}/commit",
    response_model=CampaignBudgetReservationResponse,
)
def commit_budget(
    campaign_id: str,
    reservation_id: str,
    body: CampaignBudgetCommit,
    controls: Controls,
) -> CampaignBudgetReservationResponse:
    return controls.commit_budget(campaign_id, reservation_id, body)


@router.post(
    "/campaigns/{campaign_id}/budget-reservations/{reservation_id}/release",
    response_model=CampaignBudgetReservationResponse,
)
def release_budget(
    campaign_id: str,
    reservation_id: str,
    controls: Controls,
) -> CampaignBudgetReservationResponse:
    return controls.release_budget(campaign_id, reservation_id)


@router.post(
    "/campaigns/{campaign_id}/recover",
    response_model=CampaignRecoveryResponse,
)
def recover_campaign(
    campaign_id: str,
    controls: Controls,
) -> CampaignRecoveryResponse:
    return controls.recover_expired(campaign_id)
