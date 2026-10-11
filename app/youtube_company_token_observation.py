"""Bounded, *non-authoritative* provider-reported token evidence for YouTube company.

Only settled persisted Research TaskAttempt results are inspected. Missing
provider tokens or unsuccessful attempts are NOT inferred as zero usage.
Never use this partial evidence to grant further runtime/financial authority.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.models import Project, Task, TaskAttempt, utcnow
from app.policy import DomainError, State
from app.storage import SessionStore
from app.youtube_company_team import TEAM_ROLE_IDS, youtube_company_registry_digest

_ROLES = frozenset(TEAM_ROLE_IDS)
_ROLLING_HOURS = 24
_MAX_ROWS = 256
_MAX_STEPS = 64
_MAX_TOKENS_PER_FIELD = 10_000_000


def _read_reported_tokens(attempt: TaskAttempt) -> tuple[int, int, int] | None:
    """Conservative parse of optional untrusted local model-provider metrics.

    Null and malformed/negative/duplicate/mismatched metrics are unmetered;
    no partial figure is silently represented as a full attempt total.
    """

    if attempt.outcome != State.SUCCEEDED:
        return None
    result = attempt.result
    if type(result) is not dict:
        return None
    steps = result.get("usage")
    if type(steps) is not list or not (1 <= len(steps) <= _MAX_STEPS):
        return None
    declared = result.get("model_steps")
    if type(declared) is not int or declared != len(steps):
        return None
    model = result.get("actual_model")
    if type(model) is not str or not (1 <= len(model) <= 128):
        return None

    prompt = completion = total = 0
    for ordinal, step in enumerate(steps, 1):
        if type(step) is not dict or type(step.get("step")) is not int or step["step"] != ordinal:
            return None
        values = tuple(
            step.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        if any(
            type(value) is not int or not (0 <= value <= _MAX_TOKENS_PER_FIELD) for value in values
        ):
            return None
        p, c, t = values
        if p + c != t:
            return None
        prompt += p
        completion += c
        total += t
    return prompt, completion, total


class YoutubeCompanyTokenObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-youtube-company-token-observation-v1"] = (
        "zelo-youtube-company-token-observation-v1"
    )
    project_id: str
    project_status: Literal["active", "paused", "completed", "archived"]
    observed_at: datetime
    window_started_at: datetime
    rolling_window_hours: Literal[24] = _ROLLING_HOURS
    attempted_research_workers: int = Field(ge=0, le=_MAX_ROWS)
    completed_research_workers: int = Field(ge=0, le=_MAX_ROWS)
    fully_reported_successes: int = Field(ge=0, le=_MAX_ROWS)
    unmetered_attempts: int = Field(ge=0, le=_MAX_ROWS)
    provider_reported_prompt_tokens: int = Field(ge=0)
    provider_reported_completion_tokens: int = Field(ge=0)
    provider_reported_total_tokens: int = Field(ge=0)
    trusted_team_roles: tuple[
        Literal["youtube"],
        Literal["youtube_content"],
        Literal["youtube_research"],
        Literal["youtube_script_writer"],
    ] = TEAM_ROLE_IDS
    active_company_registry_on_this_process: bool
    provider_metrics_are_unverified: Literal[True] = True
    does_not_assume_unmetered_attempts_are_zero: Literal[True] = True
    does_not_include_failed_or_cancelled_model_work: Literal[True] = True
    no_money_or_cost_guarantee: Literal[True] = True
    no_new_execution_or_approval_authority: Literal[True] = True


class YoutubeCompanyTokenObservationService:
    """Operator-only read-only projection; no live provider calls."""

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    def preview(
        self,
        project_id: str,
        *,
        active_registry_version: str,
        now: datetime | None = None,
    ) -> YoutubeCompanyTokenObservation:
        observed = now or utcnow()
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("youtube_company_tokens_clock_invalid")
        with self.database.read() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .join(Task, TaskAttempt.task_id == Task.id)
                    .where(
                        Task.kind == "research_run",
                        TaskAttempt.started_at > observed - timedelta(hours=_ROLLING_HOURS),
                        Task.agent_execution_spec.is_not(None),
                        func.json_extract(Task.agent_execution_spec, "$.project_id") == project_id,
                        func.json_extract(Task.agent_execution_spec, "$.spec.agent_id").in_(
                            sorted(_ROLES)
                        ),
                    )
                    .order_by(TaskAttempt.started_at, TaskAttempt.id)
                    .limit(_MAX_ROWS + 1)
                )
            )
            if len(attempts) > _MAX_ROWS:
                # The actual running company cannot lawfully create >24 new
                # attempts/24h after enforcement. Never show truncated sums.
                raise DomainError("youtube_company_token_evidence_volume_exceeded", 409)

            successes = 0
            full = 0
            prompt = completion = total = 0
            for attempt in attempts:
                if attempt.outcome == State.SUCCEEDED:
                    successes += 1
                observed_tokens = _read_reported_tokens(attempt)
                if observed_tokens is None:
                    continue
                full += 1
                p, c, t = observed_tokens
                prompt += p
                completion += c
                total += t
            return YoutubeCompanyTokenObservation(
                project_id=project.id,
                project_status=project.status,
                observed_at=observed,
                window_started_at=observed - timedelta(hours=_ROLLING_HOURS),
                attempted_research_workers=len(attempts),
                completed_research_workers=successes,
                fully_reported_successes=full,
                unmetered_attempts=len(attempts) - full,
                provider_reported_prompt_tokens=prompt,
                provider_reported_completion_tokens=completion,
                provider_reported_total_tokens=total,
                active_company_registry_on_this_process=(
                    active_registry_version == youtube_company_registry_digest()
                ),
            )
