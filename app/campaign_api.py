"""Authenticated Campaign operational-state routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.campaigns import CampaignService
from app.schemas import (
    CampaignCheckpointCreate,
    CampaignCheckpointPage,
    CampaignCheckpointResponse,
    CampaignCreate,
    CampaignEventPage,
    CampaignPage,
    CampaignResponse,
)

router = APIRouter(dependencies=[Depends(require_operator)])


def service(request: Request) -> CampaignService:
    return request.app.state.campaigns


Campaigns = Annotated[CampaignService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post("/goals/{goal_id}/campaigns", response_model=CampaignResponse, status_code=201)
def create_campaign(
    goal_id: str,
    body: CampaignCreate,
    campaigns: Campaigns,
) -> CampaignResponse:
    return campaigns.create(goal_id, body)


@router.get("/goals/{goal_id}/campaigns", response_model=CampaignPage)
def list_campaigns(
    goal_id: str,
    campaigns: Campaigns,
    limit: Limit = 20,
    offset: Offset = 0,
) -> CampaignPage:
    return campaigns.list_for_goal(goal_id, limit, offset)


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
def get_campaign(campaign_id: str, campaigns: Campaigns) -> CampaignResponse:
    return campaigns.get(campaign_id)


@router.get("/campaigns/{campaign_id}/events", response_model=CampaignEventPage)
def campaign_events(
    campaign_id: str,
    campaigns: Campaigns,
    limit: Limit = 100,
    offset: Offset = 0,
) -> CampaignEventPage:
    return campaigns.events(campaign_id, limit, offset)


@router.get("/campaigns/{campaign_id}/events/latest", response_model=CampaignEventPage)
def latest_campaign_events(
    campaign_id: str,
    campaigns: Campaigns,
    limit: Limit = 100,
) -> CampaignEventPage:
    return campaigns.latest_events(campaign_id, limit)


@router.post(
    "/campaigns/{campaign_id}/checkpoints",
    response_model=CampaignCheckpointResponse,
    status_code=201,
)
def create_checkpoint(
    campaign_id: str,
    body: CampaignCheckpointCreate,
    campaigns: Campaigns,
) -> CampaignCheckpointResponse:
    return campaigns.checkpoint(campaign_id, body)


@router.get("/campaigns/{campaign_id}/checkpoints", response_model=CampaignCheckpointPage)
def list_checkpoints(
    campaign_id: str,
    campaigns: Campaigns,
    limit: Limit = 100,
    offset: Offset = 0,
) -> CampaignCheckpointPage:
    return campaigns.checkpoints(campaign_id, limit, offset)
