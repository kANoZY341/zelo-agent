"""Authenticated bounded Campaign scheduling routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.scheduler import CampaignScheduler
from app.schemas import (
    CampaignScheduleCreate,
    CampaignSchedulePage,
    CampaignScheduleResponse,
    CampaignScheduleRunPage,
)

router = APIRouter(dependencies=[Depends(require_operator)])


def service(request: Request) -> CampaignScheduler:
    return request.app.state.scheduler


Scheduler = Annotated[CampaignScheduler, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post(
    "/campaigns/{campaign_id}/schedules",
    response_model=CampaignScheduleResponse,
    status_code=201,
)
def create_schedule(
    campaign_id: str,
    body: CampaignScheduleCreate,
    scheduler: Scheduler,
) -> CampaignScheduleResponse:
    return scheduler.create(campaign_id, body)


@router.get(
    "/campaigns/{campaign_id}/schedules",
    response_model=CampaignSchedulePage,
)
def list_schedules(
    campaign_id: str,
    scheduler: Scheduler,
    limit: Limit = 100,
    offset: Offset = 0,
) -> CampaignSchedulePage:
    return scheduler.list_for_campaign(campaign_id, limit, offset)


@router.get(
    "/schedules/{schedule_id}",
    response_model=CampaignScheduleResponse,
)
def get_schedule(
    schedule_id: str,
    scheduler: Scheduler,
) -> CampaignScheduleResponse:
    return scheduler.get(schedule_id)


@router.post(
    "/schedules/{schedule_id}/cancel",
    response_model=CampaignScheduleResponse,
)
def cancel_schedule(
    schedule_id: str,
    scheduler: Scheduler,
) -> CampaignScheduleResponse:
    return scheduler.cancel(schedule_id)


@router.get(
    "/schedules/{schedule_id}/runs",
    response_model=CampaignScheduleRunPage,
)
def list_schedule_runs(
    schedule_id: str,
    scheduler: Scheduler,
    limit: Limit = 100,
    offset: Offset = 0,
) -> CampaignScheduleRunPage:
    return scheduler.runs(schedule_id, limit, offset)
