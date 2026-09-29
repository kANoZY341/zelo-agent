"""Read-only worker/model performance and reported-cost evidence for v0.13."""

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from app.database import Database
from app.models import ConversationTurn, Task, TaskAttempt, utcnow
from app.schemas import (
    ModelPerformanceEvidence,
    ModelPerformanceSummary,
    PerformanceCostEvidence,
    PerformanceTotals,
    ReportedCostEvidence,
    WorkerPerformanceEvidence,
    WorkerPerformanceSummary,
)

PERFORMANCE_EVIDENCE_SCHEMA = "zelo-performance-evidence-v1"
MAX_USAGE_COUNTER = 1_000_000_000_000_000
MODEL_TASK_KINDS = frozenset({"ai_prompt", "agent_run", "research_run", "self_improvement_run"})


def _nonnegative_int(value: Any) -> int | None:
    if type(value) is int and 0 <= value <= MAX_USAGE_COUNTER:
        return value
    return None


def _nonnegative_float(value: Any) -> float | None:
    if type(value) not in {int, float}:
        return None
    normalized = float(value)
    if normalized < 0 or not math.isfinite(normalized):
        return None
    return normalized


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _model_usage(
    result: dict[str, Any] | None,
    kind: str,
) -> tuple[int | None, int | None, int | None]:
    if not isinstance(result, dict):
        return None, None, None

    if kind == "ai_prompt":
        usage = result.get("usage")
        if not isinstance(usage, dict):
            return None, None, None
        return (
            _nonnegative_int(usage.get("prompt_tokens")),
            _nonnegative_int(usage.get("completion_tokens")),
            _nonnegative_int(usage.get("total_tokens")),
        )

    if kind in {"agent_run", "research_run"}:
        raw = result.get("usage")
        if not isinstance(raw, list):
            return None, None, None
        prompt: list[int | None] = []
        completion: list[int | None] = []
        total: list[int | None] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            prompt.append(_nonnegative_int(item.get("prompt_tokens")))
            completion.append(_nonnegative_int(item.get("completion_tokens")))
            total.append(_nonnegative_int(item.get("total_tokens")))
        return _sum_optional(prompt), _sum_optional(completion), _sum_optional(total)

    if kind == "self_improvement_run":
        return (
            _nonnegative_int(result.get("prompt_tokens")),
            _nonnegative_int(result.get("completion_tokens")),
            _nonnegative_int(result.get("total_tokens")),
        )

    return None, None, None


def _usage_status(*values: int | None) -> str:
    reported = sum(value is not None for value in values)
    if reported == len(values):
        return "reported"
    if reported:
        return "partial"
    return "unreported"


def _attempt_duration(attempt: TaskAttempt) -> float | None:
    if attempt.ended_at is None:
        return None
    duration = (attempt.ended_at - attempt.started_at).total_seconds()
    return max(0.0, duration)


def _worker_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    allowed = {}
    for key in (
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ):
        value = _nonnegative_int(raw.get(key))
        if value is not None:
            allowed[key] = value
    return allowed


class PerformanceEvidenceService:
    """Derive bounded evidence only from already-persisted Core records."""

    def __init__(self, database: Database, *, clock=utcnow) -> None:
        self.database = database
        self.clock = clock

    @staticmethod
    def _model_from_turn(turn: ConversationTurn) -> ModelPerformanceEvidence:
        usage = turn.usage if isinstance(turn.usage, dict) else {}
        prompt = _nonnegative_int(usage.get("prompt_tokens"))
        completion = _nonnegative_int(usage.get("completion_tokens"))
        total = _nonnegative_int(usage.get("total_tokens"))
        return ModelPerformanceEvidence(
            source_kind="mind_turn",
            source_id=turn.id,
            task_kind=None,
            attempt_number=None,
            alias=turn.selected_alias,
            model=turn.selected_model,
            outcome="succeeded",
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            usage_status=_usage_status(prompt, completion, total),
            duration_seconds=None,
            observed_at=turn.created_at,
        )

    @staticmethod
    def _model_from_attempt(task: Task, attempt: TaskAttempt) -> ModelPerformanceEvidence:
        result = attempt.result if isinstance(attempt.result, dict) else None
        prompt, completion, total = _model_usage(result, task.kind)
        alias = task.selected_alias or (
            result.get("alias")
            if isinstance(result, dict) and isinstance(result.get("alias"), str)
            else None
        )
        model = task.selected_model or (
            result.get("actual_model")
            if isinstance(result, dict) and isinstance(result.get("actual_model"), str)
            else None
        )
        return ModelPerformanceEvidence(
            source_kind="task_attempt",
            source_id=attempt.id,
            task_kind=task.kind,
            attempt_number=attempt.number,
            alias=alias,
            model=model,
            outcome=attempt.outcome,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            usage_status=_usage_status(prompt, completion, total),
            duration_seconds=_attempt_duration(attempt),
            observed_at=attempt.ended_at or attempt.started_at,
        )

    @staticmethod
    def _worker_from_attempt(task: Task, attempt: TaskAttempt) -> list[WorkerPerformanceEvidence]:
        if task.kind != "developer_run" or not isinstance(attempt.result, dict):
            return []
        result = attempt.result
        worker = result.get("worker")
        version = result.get("worker_version")
        if not isinstance(worker, str) or not worker or not isinstance(version, str) or not version:
            return []

        duration = _nonnegative_float(result.get("duration_seconds"))
        primary = WorkerPerformanceEvidence(
            source_id=attempt.id,
            task_id=task.id,
            attempt_number=attempt.number,
            role="developer",
            worker=worker,
            worker_version=version,
            worker_config_version=(
                result.get("worker_config_version")
                if isinstance(result.get("worker_config_version"), str)
                else None
            ),
            outcome=attempt.outcome,
            usage=_worker_usage(result.get("usage")),
            duration_seconds=duration if duration is not None else _attempt_duration(attempt),
            validation_passed=(
                result.get("validation_passed")
                if type(result.get("validation_passed")) is bool
                else None
            ),
            observed_at=attempt.ended_at or attempt.started_at,
        )
        evidence = [primary]

        review = result.get("independent_review")
        if isinstance(review, dict):
            reviewer = review.get("implementation")
            reviewer_version = review.get("worker_version")
            if isinstance(reviewer, str) and reviewer and isinstance(reviewer_version, str):
                evidence.append(
                    WorkerPerformanceEvidence(
                        source_id=attempt.id,
                        task_id=task.id,
                        attempt_number=attempt.number,
                        role="reviewer",
                        worker=reviewer,
                        worker_version=reviewer_version,
                        worker_config_version=None,
                        outcome="succeeded",
                        usage=_worker_usage(review.get("usage")),
                        duration_seconds=_nonnegative_float(review.get("duration_seconds")),
                        validation_passed=None,
                        observed_at=attempt.ended_at or attempt.started_at,
                    )
                )
        return evidence

    @staticmethod
    def _model_summaries(
        evidence: list[ModelPerformanceEvidence],
    ) -> list[ModelPerformanceSummary]:
        groups: dict[tuple[str | None, str | None], list[ModelPerformanceEvidence]] = defaultdict(
            list
        )
        for item in evidence:
            groups[(item.alias, item.model)].append(item)

        summaries = []
        for (alias, model), items in sorted(
            groups.items(), key=lambda pair: ((pair[0][0] or ""), (pair[0][1] or ""))
        ):
            durations = [
                item.duration_seconds for item in items if item.duration_seconds is not None
            ]
            summaries.append(
                ModelPerformanceSummary(
                    alias=alias,
                    model=model,
                    observations=len(items),
                    succeeded=sum(item.outcome == "succeeded" for item in items),
                    failed=sum(item.outcome == "failed" for item in items),
                    other_outcomes=sum(
                        item.outcome not in {"succeeded", "failed"} for item in items
                    ),
                    prompt_tokens=sum(item.prompt_tokens or 0 for item in items),
                    completion_tokens=sum(item.completion_tokens or 0 for item in items),
                    total_tokens=sum(item.total_tokens or 0 for item in items),
                    usage_observations=sum(item.usage_status != "unreported" for item in items),
                    duration_observations=len(durations),
                    duration_seconds=sum(durations),
                )
            )
        return summaries

    @staticmethod
    def _worker_summaries(
        evidence: list[WorkerPerformanceEvidence],
    ) -> list[WorkerPerformanceSummary]:
        groups: dict[tuple[str, str, str], list[WorkerPerformanceEvidence]] = defaultdict(list)
        for item in evidence:
            groups[(item.role, item.worker, item.worker_version)].append(item)

        summaries = []
        for (role, worker, version), items in sorted(groups.items()):
            durations = [
                item.duration_seconds for item in items if item.duration_seconds is not None
            ]
            summaries.append(
                WorkerPerformanceSummary(
                    role=role,
                    worker=worker,
                    worker_version=version,
                    observations=len(items),
                    succeeded=sum(item.outcome == "succeeded" for item in items),
                    failed=sum(item.outcome == "failed" for item in items),
                    other_outcomes=sum(
                        item.outcome not in {"succeeded", "failed"} for item in items
                    ),
                    usage_observations=sum(bool(item.usage) for item in items),
                    input_tokens=sum(item.usage.get("input_tokens", 0) for item in items),
                    cached_input_tokens=sum(
                        item.usage.get("cached_input_tokens", 0) for item in items
                    ),
                    cache_creation_input_tokens=sum(
                        item.usage.get("cache_creation_input_tokens", 0) for item in items
                    ),
                    cache_read_input_tokens=sum(
                        item.usage.get("cache_read_input_tokens", 0) for item in items
                    ),
                    output_tokens=sum(item.usage.get("output_tokens", 0) for item in items),
                    prompt_tokens=sum(item.usage.get("prompt_tokens", 0) for item in items),
                    completion_tokens=sum(item.usage.get("completion_tokens", 0) for item in items),
                    total_tokens=sum(item.usage.get("total_tokens", 0) for item in items),
                    duration_observations=len(durations),
                    duration_seconds=sum(durations),
                    validation_passes=sum(item.validation_passed is True for item in items),
                    validation_failures=sum(item.validation_passed is False for item in items),
                )
            )
        return summaries

    def evidence(self, *, limit: int = 200) -> PerformanceCostEvidence:
        with self.database.read() as session:
            turns = list(
                session.scalars(
                    select(ConversationTurn)
                    .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
                    .limit(limit)
                )
            )
            attempts = list(
                session.execute(
                    select(TaskAttempt, Task)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .where(Task.kind.in_(tuple(MODEL_TASK_KINDS | {"developer_run"})))
                    .order_by(TaskAttempt.started_at.desc(), TaskAttempt.id.desc())
                    .limit(limit)
                )
            )

        model_evidence = [self._model_from_turn(turn) for turn in turns]
        worker_evidence: list[WorkerPerformanceEvidence] = []
        for attempt, task in attempts:
            if task.kind in MODEL_TASK_KINDS:
                model_evidence.append(self._model_from_attempt(task, attempt))
            worker_evidence.extend(self._worker_from_attempt(task, attempt))

        model_evidence.sort(key=lambda item: (item.observed_at, item.source_id), reverse=True)
        worker_evidence.sort(
            key=lambda item: (item.observed_at, item.source_id, item.role),
            reverse=True,
        )
        model_evidence = model_evidence[:limit]
        worker_evidence = worker_evidence[:limit]

        return PerformanceCostEvidence(
            schema=PERFORMANCE_EVIDENCE_SCHEMA,
            generated_at=self.clock(),
            record_limit=limit,
            totals=PerformanceTotals(
                returned_model_observations=len(model_evidence),
                returned_worker_observations=len(worker_evidence),
            ),
            model_evidence=model_evidence,
            model_summaries=self._model_summaries(model_evidence),
            worker_evidence=worker_evidence,
            worker_summaries=self._worker_summaries(worker_evidence),
            reported_cost=ReportedCostEvidence(
                status="not_reported_by_current_sources",
                currency=None,
                amount_micros=None,
                note=(
                    "Current persisted model/worker sources report usage and timing, "
                    "not monetary cost. Zelo does not estimate or invent prices."
                ),
            ),
        )
