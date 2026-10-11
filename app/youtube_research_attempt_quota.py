"""Project-wide claim-time Attempt cap for optional YouTube Research Specialist.

Actual Worker dispatch cannot bypass this serialized TaskAttempt creation
gate by retrying the same Task, replacing an AgentInstance or restarting Core.
This is an attempt count, not actual tokens/cost.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_execution_contracts import AgentExecutionBinding
from app.models import AgentInstance, Task, TaskAttempt, utcnow
from app.policy import DomainError
from app.storage import SessionStore

WINDOW = timedelta(hours=24)
MAX_ATTEMPTS = 12
SUBJECT = "youtube_topic_researcher"


def _attempts(session: Session, project_id: str, *, now: datetime) -> int:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("youtube_research_attempt_clock_invalid")
    return (
        session.scalar(
            select(func.count())
            .select_from(TaskAttempt)
            .join(Task, TaskAttempt.task_id == Task.id)
            .where(
                Task.kind == "research_run",
                TaskAttempt.started_at > now - WINDOW,
                Task.agent_execution_spec.is_not(None),
                func.json_extract(Task.agent_execution_spec, "$.project_id") == project_id,
                func.json_extract(Task.agent_execution_spec, "$.spec.agent_id") == SUBJECT,
            )
        )
        or 0
    )


def youtube_research_attempt_claim_allowed(
    session: Session,
    binding: AgentExecutionBinding | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Call before TaskAttempt creation within TaskService's writer transaction.

    A claim has already revalidated AgentInstance, Project, Registry and
    worker identity. The model is not permitted to author a binding here.
    """

    if binding is None or binding.spec.agent_id != SUBJECT:
        return True
    if binding.task_kind != "research_run":
        return False
    return _attempts(session, binding.project_id, now=now or utcnow()) < MAX_ATTEMPTS


class YoutubeResearchAttemptQuotaView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-youtube-research-attempt-quota-v1"] = (
        "zelo-youtube-research-attempt-quota-v1"
    )
    agent_instance_id: str
    project_id: str
    observed_at: datetime
    window_started_at: datetime
    max_attempts: Literal[12] = MAX_ATTEMPTS
    used_attempts: int = Field(ge=0)
    remaining_attempts: int = Field(ge=0, le=12)
    counts_failures_retries_and_completed_attempts: Literal[True] = True
    project_scoped_across_agent_replacements: Literal[True] = True
    claim_rejection_creates_no_attempt: Literal[True] = True
    measures_tokens_or_money: Literal[False] = False
    grants_new_tasks_or_tools_or_spaces: Literal[False] = False


class YoutubeResearchAttemptQuotaService:
    """Read-only reporting; never a grant or spending guarantee."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def preview(
        self, instance_id: str, *, now: datetime | None = None
    ) -> YoutubeResearchAttemptQuotaView:
        observed = now or utcnow()
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("youtube_research_attempt_clock_invalid")
        with self.store.read() as session:
            instance = session.get(AgentInstance, instance_id)
            if instance is None:
                raise DomainError("Agent instance not found", 404)
            if instance.agent_id != SUBJECT:
                raise DomainError("Agent does not have a YouTube Research attempt quota", 404)
            count = _attempts(session, instance.project_id, now=observed)
            return YoutubeResearchAttemptQuotaView(
                agent_instance_id=instance.id,
                project_id=instance.project_id,
                observed_at=observed,
                window_started_at=observed - WINDOW,
                used_attempts=count,
                remaining_attempts=max(0, MAX_ATTEMPTS - count),
            )
