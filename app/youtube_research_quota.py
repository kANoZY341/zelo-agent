"""Transactional new-Task admission quota for opted-in YouTube Research.

No additional database ledger: immutable creation records in the existing
Core Task table are the counted events. This limits unique *new task
submissions* per Project+Agent ID (including retired/renewed instances),
not Worker attempts, token spend or money.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_execution_contracts import AgentExecutionBinding
from app.agent_factory import AgentSpec
from app.models import AgentInstance, Task, utcnow
from app.policy import DomainError
from app.storage import SessionStore

ROLLOVER_HOURS = 24
MAX_NEW_TASKS = 8
SUBJECT = "youtube_topic_researcher"


class YoutubeResearchQuotaView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["zelo-youtube-research-new-task-quota-v1"] = (
        "zelo-youtube-research-new-task-quota-v1"
    )
    agent_instance_id: str
    project_id: str
    instance_status: Literal["active", "paused", "retired"]
    observed_at: datetime
    window_started_at: datetime
    rolling_window_hours: Literal[24] = ROLLOVER_HOURS
    max_new_tasks: Literal[8] = MAX_NEW_TASKS
    quota_scoped_to_project_and_agent_id: Literal[True] = True
    used_new_tasks: int = Field(ge=0)
    remaining_new_tasks: int = Field(ge=0, le=8)
    counts_failed_cancelled_and_completed_submissions: Literal[True] = True
    idempotent_exact_replays_consume_no_new_slot: Literal[True] = True
    tracks_worker_attempts_or_token_spend: Literal[False] = False
    current_execution_authority_claimed: Literal[False] = False
    has_live_spending_or_publishing_authority: Literal[False] = False
    additional_tool_or_space_grants: tuple[()] = ()


def _count(session: Session, project_id: str, now: datetime) -> int:
    """Use a persisted Task's immutable Agent execution identity, not JSON text."""

    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("youtube_research_quota_clock_invalid")
    from_time = now - timedelta(hours=ROLLOVER_HOURS)
    return (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.kind == "research_run",
                Task.created_at > from_time,
                Task.agent_execution_spec.is_not(None),
                func.json_extract(Task.agent_execution_spec, "$.project_id") == project_id,
                func.json_extract(Task.agent_execution_spec, "$.spec.agent_id") == SUBJECT,
            )
        )
        or 0
    )


def enforce_youtube_research_new_task_quota(
    session: Session, binding: AgentExecutionBinding, *, now: datetime | None = None
) -> None:
    """Run after identity validation, before any new Task is inserted.

    Call only inside TaskService's serialized SQL writer *after* idempotency
    replay resolution, so two simultaneous new submissions cannot overrun a
    budget. Never run for non-YouTube agents or non-research Tasks.
    """

    if binding.spec.agent_id != SUBJECT:
        return
    if binding.task_kind != "research_run":
        raise DomainError("YouTube Research quota does not permit this Task kind", 409)
    usage = _count(session, binding.project_id, now or utcnow())
    if usage >= MAX_NEW_TASKS:
        raise DomainError("youtube_research_new_task_quota_exhausted", 429)


class YoutubeResearchQuotaService:
    """Operator-only read of observed submissions; never grants budget."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def preview(self, instance_id: str, *, now: datetime | None = None) -> YoutubeResearchQuotaView:
        observed = now or utcnow()
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("youtube_research_quota_clock_invalid")
        with self.store.read() as session:
            instance = session.get(AgentInstance, instance_id)
            if instance is None:
                raise DomainError("Agent instance not found", 404)
            if instance.agent_id != SUBJECT:
                raise DomainError("YouTube Research quota is not available for this agent", 404)
            try:
                snapshot = AgentSpec.model_validate(instance.spec)
            except ValueError:
                raise DomainError("YouTube Research AgentSpec invalid", 409) from None
            if snapshot.agent_id != SUBJECT:
                raise DomainError("YouTube Research AgentSpec mismatch", 409)
            used = _count(session, instance.project_id, observed)
            return YoutubeResearchQuotaView(
                agent_instance_id=instance.id,
                project_id=instance.project_id,
                instance_status=instance.status,
                observed_at=observed,
                window_started_at=observed - timedelta(hours=ROLLOVER_HOURS),
                used_new_tasks=used,
                remaining_new_tasks=max(0, MAX_NEW_TASKS - used),
            )
