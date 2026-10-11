"""Durable 24-hour Project-wide Research Worker attempt ceiling for real YouTube teams.

Unlike the stand-alone pilot's per-Specialist budget, this cap is shared by the
real VP, Director, Manager and Specialist. Only existing persisted TaskAttempt
records count. It is neither a monetary limit nor an LLM token measure, and
adds zero Skills/permissions/Task admission.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_execution_contracts import AgentExecutionBinding
from app.models import Project, Task, TaskAttempt, utcnow
from app.policy import DomainError
from app.storage import SessionStore
from app.youtube_company_team import TEAM_ROLE_IDS

MAX_TEAM_ATTEMPTS = 24
ROLLING_HOURS = 24
_ROLES = frozenset(TEAM_ROLE_IDS)


def _count(
    session: Session,
    project_id: str,
    *,
    now: datetime,
) -> int:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("youtube_company_attempt_clock_invalid")
    cutoff = now - timedelta(hours=ROLLING_HOURS)
    return (
        session.scalar(
            select(func.count())
            .select_from(TaskAttempt)
            .join(Task, TaskAttempt.task_id == Task.id)
            .where(
                Task.kind == "research_run",
                TaskAttempt.started_at > cutoff,
                Task.agent_execution_spec.is_not(None),
                func.json_extract(Task.agent_execution_spec, "$.project_id") == project_id,
                func.json_extract(Task.agent_execution_spec, "$.spec.agent_id").in_(sorted(_ROLES)),
            )
        )
        or 0
    )


def youtube_company_attempt_claim_allowed(
    session: Session,
    binding: AgentExecutionBinding | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Fail closed *before* TaskAttempt insert under TaskService's writer lock.

    TaskService has already revalidated the exact Registry, AgentSpec, Project,
    Campaign, Organization assignment, full parent chain and live budget
    reservation before passing its claim here.
    """

    if binding is None or binding.spec.agent_id not in _ROLES:
        return True
    if binding.task_kind != "research_run":
        return False
    return _count(session, binding.project_id, now=now or utcnow()) < MAX_TEAM_ATTEMPTS


class YoutubeCompanyAttemptBudgetView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-youtube-company-attempt-budget-v1"] = (
        "zelo-youtube-company-attempt-budget-v1"
    )
    project_id: str
    project_status: Literal["active", "paused", "completed", "archived"]
    observed_at: datetime
    window_started_at: datetime
    rolling_window_hours: Literal[24] = ROLLING_HOURS
    max_research_worker_attempts: Literal[24] = MAX_TEAM_ATTEMPTS
    counted_research_worker_attempts: int = Field(ge=0)
    remaining_research_worker_attempts: int = Field(ge=0, le=24)
    trusted_team_roles: tuple[
        Literal["youtube"],
        Literal["youtube_content"],
        Literal["youtube_research"],
        Literal["youtube_script_writer"],
    ] = TEAM_ROLE_IDS
    current_company_registry_enabled: bool
    counts_failed_retries_and_completed_attempts: Literal[True] = True
    shared_across_roles_and_retired_instances: Literal[True] = True
    rejects_claim_before_worker_dispatch: Literal[True] = True
    grants_new_tools_spaces_delegations_or_hires: Literal[False] = False
    measures_money_tokens_or_provider_costs: Literal[False] = False


class YoutubeCompanyAttemptBudgetService:
    """Authenticated, observation-only; never a substitute for Campaign budget."""

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    def preview(
        self,
        project_id: str,
        *,
        current_registry_version: str,
        now: datetime | None = None,
    ) -> YoutubeCompanyAttemptBudgetView:
        observed = now or utcnow()
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("youtube_company_attempt_clock_invalid")
        with self.database.read() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            count = _count(session, project_id, now=observed)
            from app.youtube_company_team import youtube_company_registry_digest

            return YoutubeCompanyAttemptBudgetView(
                project_id=project.id,
                project_status=project.status,
                observed_at=observed,
                window_started_at=observed - timedelta(hours=ROLLING_HOURS),
                counted_research_worker_attempts=count,
                remaining_research_worker_attempts=max(0, MAX_TEAM_ATTEMPTS - count),
                trusted_team_roles=TEAM_ROLE_IDS,
                current_company_registry_enabled=(
                    current_registry_version == youtube_company_registry_digest()
                ),
            )
