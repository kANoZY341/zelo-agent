"""Durable v0.12 Campaign ownership, events, and checkpoints."""

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import Database
from app.models import (
    Campaign,
    CampaignCheckpoint,
    CampaignEvent,
    CampaignTaskLink,
    Goal,
    Project,
    utcnow,
)
from app.policy import DomainError
from app.schemas import (
    CampaignCheckpointCreate,
    CampaignCheckpointPage,
    CampaignCheckpointResponse,
    CampaignCreate,
    CampaignEventPage,
    CampaignEventResponse,
    CampaignPage,
    CampaignResponse,
)

CHECKPOINT_MAX_CHARS = 65536


def append_campaign_event(
    session: Session,
    campaign_id: str,
    event: str,
    *,
    task_id: str | None = None,
    **details: object,
) -> CampaignEvent:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise DomainError("Campaign not found", 404)
    campaign.updated_at = utcnow()
    latest = (
        session.scalar(
            select(func.max(CampaignEvent.sequence)).where(CampaignEvent.campaign_id == campaign_id)
        )
        or 0
    )
    record = CampaignEvent(
        campaign_id=campaign_id,
        sequence=latest + 1,
        task_id=task_id,
        event=event,
        details=details,
    )
    session.add(record)
    session.flush()
    return record


class CampaignService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _require(session: Session, campaign_id: str) -> Campaign:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise DomainError("Campaign not found", 404)
        return campaign

    @staticmethod
    def _response(session: Session, campaign: Campaign) -> CampaignResponse:
        task_count = (
            session.scalar(
                select(func.count())
                .select_from(CampaignTaskLink)
                .where(CampaignTaskLink.campaign_id == campaign.id)
            )
            or 0
        )
        return CampaignResponse(
            id=campaign.id,
            goal_id=campaign.goal_id,
            title=campaign.title,
            summary=campaign.summary,
            status=campaign.status,
            task_count=task_count,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )

    def create(self, goal_id: str, body: CampaignCreate) -> CampaignResponse:
        title = " ".join(body.title.split())
        if not title:
            raise DomainError("Campaign title cannot be blank", 422)
        summary = body.summary.strip()
        with self.database.transaction() as session:
            goal = session.get(Goal, goal_id)
            if goal is None:
                raise DomainError("Goal not found", 404)
            if goal.status != "active":
                raise DomainError("Goal is not active", 409)
            project = session.get(Project, goal.project_id)
            if project is None or project.status != "active":
                raise DomainError("Project is not active", 409)

            campaign = Campaign(
                goal_id=goal.id,
                title=title,
                summary=summary,
                status="active",
            )
            session.add(campaign)
            goal.updated_at = utcnow()
            project.updated_at = utcnow()
            session.flush()
            append_campaign_event(
                session,
                campaign.id,
                "campaign_created",
                goal_id=goal.id,
            )
            return self._response(session, campaign)

    def get(self, campaign_id: str) -> CampaignResponse:
        with self.database.read() as session:
            return self._response(session, self._require(session, campaign_id))

    def list_for_goal(
        self,
        goal_id: str,
        limit: int,
        offset: int,
    ) -> CampaignPage:
        with self.database.read() as session:
            if session.get(Goal, goal_id) is None:
                raise DomainError("Goal not found", 404)
            total = (
                session.scalar(
                    select(func.count()).select_from(Campaign).where(Campaign.goal_id == goal_id)
                )
                or 0
            )
            campaigns = list(
                session.scalars(
                    select(Campaign)
                    .where(Campaign.goal_id == goal_id)
                    .order_by(Campaign.updated_at.desc(), Campaign.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return CampaignPage(
                items=[self._response(session, campaign) for campaign in campaigns],
                limit=limit,
                offset=offset,
                total=total,
            )

    def events(self, campaign_id: str, limit: int, offset: int) -> CampaignEventPage:
        with self.database.read() as session:
            self._require(session, campaign_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignEvent)
                    .where(CampaignEvent.campaign_id == campaign_id)
                )
                or 0
            )
            events = list(
                session.scalars(
                    select(CampaignEvent)
                    .where(CampaignEvent.campaign_id == campaign_id)
                    .order_by(CampaignEvent.sequence)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return CampaignEventPage(
                items=[CampaignEventResponse.model_validate(event) for event in events],
                limit=limit,
                offset=offset,
                total=total,
            )

    def latest_events(self, campaign_id: str, limit: int) -> CampaignEventPage:
        with self.database.read() as session:
            self._require(session, campaign_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignEvent)
                    .where(CampaignEvent.campaign_id == campaign_id)
                )
                or 0
            )
            newest_first = list(
                session.scalars(
                    select(CampaignEvent)
                    .where(CampaignEvent.campaign_id == campaign_id)
                    .order_by(CampaignEvent.sequence.desc())
                    .limit(limit)
                )
            )
            events = list(reversed(newest_first))
            return CampaignEventPage(
                items=[CampaignEventResponse.model_validate(event) for event in events],
                limit=limit,
                offset=max(total - len(events), 0),
                total=total,
            )

    def checkpoint(
        self,
        campaign_id: str,
        body: CampaignCheckpointCreate,
    ) -> CampaignCheckpointResponse:
        try:
            encoded = json.dumps(
                body.state,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            raise DomainError("Campaign checkpoint state must be JSON serializable", 422) from None
        if len(encoded) > CHECKPOINT_MAX_CHARS:
            raise DomainError("Campaign checkpoint exceeds size limit", 422)

        with self.database.transaction() as session:
            campaign = self._require(session, campaign_id)
            if campaign.status not in {"active", "paused"}:
                raise DomainError("Campaign cannot accept checkpoints in its current state", 409)
            latest = (
                session.scalar(
                    select(func.max(CampaignCheckpoint.version)).where(
                        CampaignCheckpoint.campaign_id == campaign.id
                    )
                )
                or 0
            )
            checkpoint = CampaignCheckpoint(
                campaign_id=campaign.id,
                version=latest + 1,
                state=body.state,
            )
            session.add(checkpoint)
            campaign.updated_at = utcnow()
            session.flush()
            append_campaign_event(
                session,
                campaign.id,
                "checkpoint_recorded",
                checkpoint_id=checkpoint.id,
                version=checkpoint.version,
            )
            return CampaignCheckpointResponse.model_validate(checkpoint)

    def checkpoints(
        self,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> CampaignCheckpointPage:
        with self.database.read() as session:
            self._require(session, campaign_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignCheckpoint)
                    .where(CampaignCheckpoint.campaign_id == campaign_id)
                )
                or 0
            )
            checkpoints = list(
                session.scalars(
                    select(CampaignCheckpoint)
                    .where(CampaignCheckpoint.campaign_id == campaign_id)
                    .order_by(CampaignCheckpoint.version)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return CampaignCheckpointPage(
                items=[
                    CampaignCheckpointResponse.model_validate(checkpoint)
                    for checkpoint in checkpoints
                ],
                limit=limit,
                offset=offset,
                total=total,
            )
