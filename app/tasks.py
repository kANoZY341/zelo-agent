"""Transactional task operations. Routes and workers delegate state changes here."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.agent_execution_contracts import (
    AgentExecutionBinding,
    DelegationExecutionBinding,
)
from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.api_contracts import ApiExecutionSpec
from app.api_worker import ApiRuntimeError
from app.browser_contracts import BrowserExecutionSpec
from app.browser_worker import BrowserRuntimeError
from app.campaigns import append_campaign_event
from app.computer_contracts import ComputerExecutionSpec
from app.computer_control_fence import human_takeover_unreleased
from app.computer_worker import ComputerRuntimeError
from app.config import Settings
from app.developer import DeveloperRequest, DeveloperRuntime, DeveloperRuntimeError
from app.developer_contracts import WorkerSelectionSnapshot
from app.gateway import ModelGateway
from app.models import (
    AgentDelegation,
    AgentInstance,
    AgentInstanceEvent,
    Approval,
    AuditEvent,
    Campaign,
    CampaignBudgetReservation,
    CampaignTaskLink,
    Goal,
    Project,
    Task,
    TaskAttempt,
    TaskDependency,
    utcnow,
)
from app.policy import DomainError, State, action_hash, fingerprint, validate_transition
from app.promotion_ci import PromotionCIVerifier
from app.promotion_contracts import (
    MergeRequest,
    MergeRunResult,
    PromotionRequest,
    PromotionRunResult,
    ReleaseRequest,
)
from app.promotion_github import publisher_identity
from app.promotion_release import PromotionReleaseRuntime
from app.promotion_review import PromotionReviewer
from app.schemas import (
    ApprovalResponse,
    AttemptResponse,
    AuditResponse,
    DeveloperPayload,
    DeveloperRunInput,
    DeveloperRunResult,
    TaskCreate,
    TaskPage,
    TaskProjection,
    TaskResponse,
)
from app.self_improvement import ImprovementEvaluation, SelfImprovementError
from app.self_improvement_evaluation import evaluate_improvement_candidate
from app.self_improvement_planner import planner_configuration_version
from app.self_improvement_runtime import (
    SelfImprovementPreparation,
    SelfImprovementRunResult,
    developer_instruction,
    developer_review_instruction,
)
from app.shell_contracts import ShellExecutionSpec
from app.shell_worker import ShellRuntimeError
from app.skill_learning_store import SkillLearningProposalStore
from app.storage import SessionStore
from app.tools import ToolGateway, ToolPolicyDenied, ToolRuntimeError
from app.v015_release_hold import require_v015_release_scope
from app.worker_registry import Supervisor
from app.youtube_company_attempt_budget import youtube_company_attempt_claim_allowed
from app.youtube_company_authority import (
    validate_youtube_company_child_parent,
    validate_youtube_company_execution,
)
from app.youtube_research_attempt_quota import youtube_research_attempt_claim_allowed
from app.youtube_research_quota import enforce_youtube_research_new_task_quota

WAITING = (State.PENDING, State.AWAITING_APPROVAL)
TERMINAL_STATES = frozenset({State.SUCCEEDED, State.FAILED, State.CANCELLED, State.INTERRUPTED})
CAPACITY_BACKOFF_INITIAL_SECONDS = 0.25
CAPACITY_BACKOFF_MAX_SECONDS = 5.0


def _max_capacity_backoff_exponent() -> int:
    exponent = 0
    delay = CAPACITY_BACKOFF_INITIAL_SECONDS
    while delay < CAPACITY_BACKOFF_MAX_SECONDS:
        delay *= 2
        exponent += 1
    return exponent


CAPACITY_BACKOFF_MAX_EXPONENT = _max_capacity_backoff_exponent()


@dataclass(frozen=True)
class Execution:
    task_id: str
    attempt_id: str
    kind: str
    payload: dict[str, Any]
    timeout_seconds: float
    profile: str | None = None
    selected_alias: str | None = None
    selected_model: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    developer_request: DeveloperRequest | None = None
    research_selection: WorkerSelectionSnapshot | None = None
    python_selection: WorkerSelectionSnapshot | None = None
    api_request: ApiExecutionSpec | None = None
    browser_request: BrowserExecutionSpec | None = None
    promotion_request: PromotionRequest | None = None
    merge_request: MergeRequest | None = None
    release_request: ReleaseRequest | None = None
    agent_execution: AgentExecutionBinding | None = None
    shell_request: ShellExecutionSpec | None = None
    computer_request: ComputerExecutionSpec | None = None
    research_agent_lifecycle: tuple[int, int | None] | None = None


def audit(
    session: Session,
    task: Task,
    event: str,
    *,
    attempt_id: str | None = None,
    approval_id: str | None = None,
    **details: Any,
) -> None:
    session.add(
        AuditEvent(
            task_id=task.id,
            event=event,
            attempt_id=attempt_id,
            approval_id=approval_id,
            details=details,
        )
    )


def transition(session: Session, task: Task, target: State) -> None:
    validate_transition(task.status, target)
    previous = task.status
    task.status = target
    task.updated_at = utcnow()
    audit(session, task, "state_transition", previous=previous, target=target)
    if target in TERMINAL_STATES:
        campaign_id = _task_campaign_id(session, task)
        if campaign_id is not None:
            append_campaign_event(
                session,
                campaign_id,
                "task_finished",
                task_id=task.id,
                previous=previous,
                status=target,
                reason=task.error,
            )


def defer_transition(session: Session, task: Task) -> None:
    if task.status != State.RUNNING:
        raise DomainError(f"Cannot transition from {task.status} to {State.PENDING}")
    previous = task.status
    task.status = State.PENDING
    task.updated_at = utcnow()
    audit(session, task, "state_transition", previous=previous, target=State.PENDING)


def capacity_backoff_seconds(consecutive_deferrals: int) -> float:
    exponent = min(max(consecutive_deferrals - 1, 0), CAPACITY_BACKOFF_MAX_EXPONENT)
    return min(
        CAPACITY_BACKOFF_INITIAL_SECONDS * (2**exponent),
        CAPACITY_BACKOFF_MAX_SECONDS,
    )


def require_task(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise DomainError("Task not found", 404)
    return task


def _task_campaign_id(session: Session, task: Task) -> str | None:
    link = session.get(CampaignTaskLink, task.id)
    return link.campaign_id if link is not None else None


def _task_dependency_ids(session: Session, task: Task) -> list[str]:
    return list(
        session.scalars(
            select(TaskDependency.depends_on_task_id)
            .where(TaskDependency.task_id == task.id)
            .order_by(TaskDependency.depends_on_task_id)
        )
    )


TASK_PROJECTION_RESULT_CHARS = 4096
TASK_PROJECTION_SUMMARY_CHARS = 768
TASK_PROJECTION_PATH_CHARS = 192
TASK_PROJECTION_CHANGED_FILES = 6
TASK_PROJECTION_FIELD_CHARS = 128
TASK_PROJECTION_REVIEW_SUMMARY_CHARS = 384
TASK_PROJECTION_VALIDATIONS = 4
TASK_PROJECTION_INPUT_ITEMS = 1000
TASK_PROJECTION_EXIT_CODE_MIN = -(2**31)
TASK_PROJECTION_EXIT_CODE_MAX = (2**31) - 1


def _bounded_projection_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) <= limit:
        return value
    marker = "...[truncated]"
    return value[: limit - len(marker)] + marker


def _navigation_projection_url(value: Any) -> str | None:
    """Project a navigation URL without query or fragment data."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    safe = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return _bounded_projection_json_text(safe, TASK_PROJECTION_PATH_CHARS)


def _bounded_projection_exit_code(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < TASK_PROJECTION_EXIT_CODE_MIN or value > TASK_PROJECTION_EXIT_CODE_MAX:
        return None
    return value


def _encoded_json_length(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


def _bounded_projection_json_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    if _encoded_json_length(value) <= limit:
        return value

    marker = "...[truncated]"
    if _encoded_json_length(marker) > limit:
        return ""

    low = 0
    high = len(value)
    best = marker
    while low <= high:
        middle = (low + high) // 2
        candidate = value[:middle] + marker
        if _encoded_json_length(candidate) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _developer_result_projection(result: dict[str, Any]) -> dict[str, Any]:
    ignored_items = result.get("ignored_generated_files")
    ignored_paths = (
        {
            item.get("path")
            for item in ignored_items[:TASK_PROJECTION_INPUT_ITEMS]
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if isinstance(ignored_items, list)
        else set()
    )

    changed_items = result.get("changed_files")
    source_changed = (
        [
            item
            for item in changed_items[:TASK_PROJECTION_INPUT_ITEMS]
            if isinstance(item, str) and item not in ignored_paths
        ]
        if isinstance(changed_items, list)
        else []
    )
    bounded_source_changed = [
        bounded
        for item in source_changed[:TASK_PROJECTION_CHANGED_FILES]
        if (bounded := _bounded_projection_text(item, TASK_PROJECTION_PATH_CHARS)) is not None
    ]

    validation_items: list[dict[str, Any]] = []
    raw_validation = result.get("validation")
    if isinstance(raw_validation, list):
        for item in raw_validation[:TASK_PROJECTION_VALIDATIONS]:
            if not isinstance(item, dict):
                continue
            validation_items.append(
                {
                    "identifier": _bounded_projection_text(
                        item.get("identifier"),
                        TASK_PROJECTION_FIELD_CHARS,
                    ),
                    "exit_code": _bounded_projection_exit_code(item.get("exit_code")),
                    "error": _bounded_projection_text(
                        item.get("error"),
                        TASK_PROJECTION_FIELD_CHARS,
                    ),
                }
            )

    review_projection = None
    raw_review = result.get("independent_review")
    if isinstance(raw_review, dict):
        report = raw_review.get("report")
        report_dict = report if isinstance(report, dict) else {}
        findings = report_dict.get("findings")
        review_projection = {
            "worker_id": _bounded_projection_text(
                raw_review.get("worker_id"),
                TASK_PROJECTION_FIELD_CHARS,
            ),
            "summary": _bounded_projection_text(
                report_dict.get("summary"),
                TASK_PROJECTION_REVIEW_SUMMARY_CHARS,
            ),
            "finding_count": len(findings) if isinstance(findings, list) else 0,
        }

    return {
        "worker": _bounded_projection_text(
            result.get("worker"),
            TASK_PROJECTION_FIELD_CHARS,
        ),
        "worker_version": _bounded_projection_text(
            result.get("worker_version"),
            TASK_PROJECTION_FIELD_CHARS,
        ),
        "worker_exit_code": _bounded_projection_exit_code(result.get("worker_exit_code")),
        "worker_summary": _bounded_projection_text(
            result.get("worker_summary"),
            TASK_PROJECTION_SUMMARY_CHARS,
        ),
        "source_changed_files": bounded_source_changed,
        "source_changed_file_count": len(source_changed),
        "ignored_generated_file_count": len(ignored_paths),
        "validation_passed": result.get("validation_passed"),
        "validation": validation_items,
        "independent_review": review_projection,
        "error": _bounded_projection_text(
            result.get("error"),
            TASK_PROJECTION_FIELD_CHARS,
        ),
        "protocol_diagnostic": _bounded_projection_text(
            result.get("protocol_diagnostic"),
            TASK_PROJECTION_FIELD_CHARS,
        ),
        "boundary_error": _bounded_projection_text(
            result.get("boundary_error"),
            TASK_PROJECTION_FIELD_CHARS,
        ),
        "original_error": _bounded_projection_text(
            result.get("original_error"),
            TASK_PROJECTION_FIELD_CHARS,
        ),
        "cancelled": result.get("cancelled"),
    }


def _minimal_developer_result_projection(projection: dict[str, Any]) -> dict[str, Any]:
    source_changed_files = projection.get("source_changed_files")
    bounded_source_changed = (
        [
            bounded
            for item in source_changed_files[:2]
            if (bounded := _bounded_projection_json_text(item, 192)) is not None
        ]
        if isinstance(source_changed_files, list)
        else []
    )

    validation_items: list[dict[str, Any]] = []
    raw_validation = projection.get("validation")
    if isinstance(raw_validation, list):
        for item in raw_validation[:TASK_PROJECTION_VALIDATIONS]:
            if not isinstance(item, dict):
                continue
            validation_items.append(
                {
                    "identifier": _bounded_projection_json_text(
                        item.get("identifier"),
                        96,
                    ),
                    "exit_code": _bounded_projection_exit_code(item.get("exit_code")),
                    "error": _bounded_projection_json_text(
                        item.get("error"),
                        192,
                    ),
                }
            )

    return {
        "worker": _bounded_projection_json_text(projection.get("worker"), 96),
        "worker_exit_code": _bounded_projection_exit_code(projection.get("worker_exit_code")),
        "worker_summary": _bounded_projection_json_text(
            projection.get("worker_summary"),
            384,
        ),
        "source_changed_files": bounded_source_changed,
        "source_changed_file_count": projection.get("source_changed_file_count"),
        "validation_passed": projection.get("validation_passed"),
        "validation": validation_items,
        "error": _bounded_projection_json_text(projection.get("error"), 128),
        "boundary_error": _bounded_projection_json_text(
            projection.get("boundary_error"),
            128,
        ),
        "original_error": _bounded_projection_json_text(
            projection.get("original_error"),
            128,
        ),
        "cancelled": projection.get("cancelled"),
    }


def _emergency_developer_result_projection(
    projection: dict[str, Any],
) -> dict[str, Any]:
    validation_items: list[dict[str, Any]] = []
    raw_validation = projection.get("validation")
    if isinstance(raw_validation, list):
        for item in raw_validation[:2]:
            if not isinstance(item, dict):
                continue
            validation_items.append(
                {
                    "identifier": _bounded_projection_json_text(
                        item.get("identifier"),
                        64,
                    ),
                    "exit_code": _bounded_projection_exit_code(item.get("exit_code")),
                    "error": _bounded_projection_json_text(
                        item.get("error"),
                        96,
                    ),
                }
            )

    return {
        "worker": _bounded_projection_json_text(projection.get("worker"), 64),
        "worker_exit_code": _bounded_projection_exit_code(projection.get("worker_exit_code")),
        "source_changed_file_count": projection.get("source_changed_file_count"),
        "validation_passed": projection.get("validation_passed"),
        "validation": validation_items,
        "error": _bounded_projection_json_text(projection.get("error"), 96),
        "cancelled": projection.get("cancelled"),
    }


def _research_result_projection(result: dict[str, Any]) -> dict[str, Any]:
    raw_sources = result.get("sources")
    sources: list[dict[str, Any]] = []
    if isinstance(raw_sources, list):
        for item in raw_sources[:8]:
            if not isinstance(item, dict):
                continue
            sources.append(
                {
                    "id": _bounded_projection_text(item.get("id"), 16),
                    "tool": _bounded_projection_text(item.get("tool"), 64),
                    "locator": _bounded_projection_text(item.get("locator"), 256),
                    "sha256": _bounded_projection_text(item.get("sha256"), 64),
                    "truncated": item.get("truncated"),
                }
            )
    return {
        "answer": _bounded_projection_text(result.get("answer"), 3072),
        "sources": sources,
        "source_count": len(raw_sources) if isinstance(raw_sources, list) else 0,
        "model_steps": result.get("model_steps"),
        "tool_calls": result.get("tool_calls"),
    }


def _minimal_research_result_projection(result: dict[str, Any]) -> dict[str, Any]:
    raw_sources = result.get("sources")
    sources: list[dict[str, Any]] = []
    if isinstance(raw_sources, list):
        for item in raw_sources[:4]:
            if not isinstance(item, dict):
                continue
            sources.append(
                {
                    "id": _bounded_projection_json_text(item.get("id"), 16),
                    "tool": _bounded_projection_json_text(item.get("tool"), 64),
                    "locator": _bounded_projection_json_text(item.get("locator"), 192),
                    "sha256": _bounded_projection_json_text(item.get("sha256"), 64),
                    "truncated": item.get("truncated"),
                }
            )
    return {
        "answer": _bounded_projection_json_text(result.get("answer"), 1536),
        "sources": sources,
        "source_count": len(raw_sources) if isinstance(raw_sources, list) else 0,
        "model_steps": result.get("model_steps"),
        "tool_calls": result.get("tool_calls"),
    }


def _computer_result_projection(result: dict[str, Any]) -> dict[str, Any]:
    """Project only non-content metadata from private Agent computers."""

    action = result.get("action")
    projection: dict[str, Any] = {
        "action": action,
        "agent_instance_id": _bounded_projection_text(
            result.get("agent_instance_id"),
            36,
        ),
    }
    if action == "browser_snapshot" and isinstance(result.get("browser_snapshot"), dict):
        value = result["browser_snapshot"]
        elements = value.get("elements")
        projection["browser_snapshot"] = {
            "snapshot_id": value.get("snapshot_id"),
            "url": _bounded_projection_json_text(value.get("url"), TASK_PROJECTION_PATH_CHARS),
            "title": _bounded_projection_json_text(value.get("title"), TASK_PROJECTION_FIELD_CHARS),
            "element_count": len(elements) if isinstance(elements, list) else 0,
            "truncated": value.get("truncated"),
            "challenge_present": value.get("challenge_present"),
        }
    elif action == "browser_read" and isinstance(result.get("browser_read"), dict):
        value = result["browser_read"]
        text_value = value.get("text")
        projection["browser_read"] = {
            "url": _bounded_projection_json_text(value.get("url"), TASK_PROJECTION_PATH_CHARS),
            "title": _bounded_projection_json_text(value.get("title"), TASK_PROJECTION_FIELD_CHARS),
            "text_chars": len(text_value) if isinstance(text_value, str) else 0,
            "truncated": value.get("truncated"),
            "challenge_present": value.get("challenge_present"),
        }
    elif action == "browser_navigate" and isinstance(result.get("browser_navigate"), dict):
        value = result["browser_navigate"]
        text_value = value.get("text")
        projection["browser_navigate"] = {
            "endpoint_id": _bounded_projection_text(value.get("endpoint_id"), 64),
            "url": _navigation_projection_url(value.get("url")),
            "title": _bounded_projection_json_text(value.get("title"), TASK_PROJECTION_FIELD_CHARS),
            "text_chars": len(text_value) if isinstance(text_value, str) else 0,
            "truncated": value.get("truncated"),
            "elapsed_ms": value.get("elapsed_ms"),
            "challenge_present": value.get("challenge_present"),
        }
    elif action == "files_list" and isinstance(result.get("files_list"), dict):
        value = result["files_list"]
        entries = value.get("entries")
        projection["files_list"] = {
            "path": _bounded_projection_json_text(value.get("path"), TASK_PROJECTION_PATH_CHARS),
            "entry_count": len(entries) if isinstance(entries, list) else 0,
            "truncated": value.get("truncated"),
        }
    elif action == "files_read" and isinstance(result.get("files_read"), dict):
        value = result["files_read"]
        text_value = value.get("text")
        projection["files_read"] = {
            "path": _bounded_projection_json_text(value.get("path"), TASK_PROJECTION_PATH_CHARS),
            "bytes": value.get("bytes"),
            "text_chars": len(text_value) if isinstance(text_value, str) else 0,
            "truncated": value.get("truncated"),
        }
    return projection


def _project_task_result(
    kind: str,
    result: dict[str, Any] | None,
    limit: int,
) -> tuple[dict[str, Any] | None, bool]:
    if result is None:
        return None, False

    if kind == "computer_run":
        return _computer_result_projection(result), True

    if kind == "research_run":
        effective_limit = min(limit, TASK_PROJECTION_RESULT_CHARS)
        projection = _research_result_projection(result)
        if _encoded_json_length(projection) <= effective_limit:
            return projection, True

        minimal = _minimal_research_result_projection(result)
        if _encoded_json_length(minimal) <= effective_limit:
            return minimal, True

        return {
            "summary": "Research result exceeded the conversation projection budget.",
            "source_count": (
                len(result["sources"]) if isinstance(result.get("sources"), list) else 0
            ),
            "model_steps": result.get("model_steps"),
            "tool_calls": result.get("tool_calls"),
        }, True

    if kind == "developer_run":
        projection = _developer_result_projection(result)
        encoded_projection = json.dumps(
            projection,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(encoded_projection) <= TASK_PROJECTION_RESULT_CHARS:
            return projection, True

        minimal = _minimal_developer_result_projection(projection)
        if _encoded_json_length(minimal) <= TASK_PROJECTION_RESULT_CHARS:
            return minimal, True

        emergency = _emergency_developer_result_projection(projection)
        if _encoded_json_length(emergency) <= TASK_PROJECTION_RESULT_CHARS:
            return emergency, True

        return {
            "summary": "Developer result exceeded the conversation projection budget.",
            "worker_exit_code": _bounded_projection_exit_code(projection.get("worker_exit_code")),
            "source_changed_file_count": projection.get("source_changed_file_count"),
            "validation_passed": projection.get("validation_passed"),
            "cancelled": projection.get("cancelled"),
        }, True

    encoded = json.dumps(
        result,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    effective_limit = min(limit, TASK_PROJECTION_RESULT_CHARS)
    if len(encoded) <= effective_limit:
        return result, False

    preview_limit = max(256, min(2048, effective_limit // 2))
    return (
        {
            "summary": "Task result exceeded the conversation projection budget.",
            "preview_json": encoded[:preview_limit],
            "original_chars": len(encoded),
        },
        True,
    )


def snapshot(session: Session, task: Task) -> TaskResponse:
    values = {
        "id": task.id,
        "campaign_id": _task_campaign_id(session, task),
        "depends_on_task_ids": _task_dependency_ids(session, task),
        "kind": task.kind,
        "payload": task.payload,
        "timeout_seconds": task.timeout_seconds,
        "status": task.status,
        "action_version": task.action_version,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "cancel_requested": task.cancel_requested,
        "result": task.result,
        "error": task.error,
        "selected_alias": task.selected_alias,
        "selected_model": task.selected_model,
        "config_version": task.config_version,
        "developer_spec": task.developer_spec,
        "research_spec": task.research_spec,
        "python_spec": task.python_spec,
        "api_spec": task.api_spec,
        "browser_spec": task.browser_spec,
        "computer_spec": task.computer_spec,
        "shell_spec": task.shell_spec,
        "promotion_spec": task.promotion_spec,
        "merge_spec": task.merge_spec,
        "release_spec": task.release_spec,
        "agent_execution_spec": task.agent_execution_spec,
    }
    response = TaskResponse.model_validate(values)
    response.attempts = [
        AttemptResponse.model_validate(attempt)
        for attempt in session.scalars(
            select(TaskAttempt).where(TaskAttempt.task_id == task.id).order_by(TaskAttempt.number)
        )
    ]
    response.approvals = [
        ApprovalResponse.model_validate(approval)
        for approval in session.scalars(
            select(Approval).where(Approval.task_id == task.id).order_by(Approval.created_at)
        )
    ]
    return response


class TaskService:
    def __init__(
        self,
        database: SessionStore,
        settings: Settings,
        gateway: ModelGateway | None = None,
        tool_gateway: ToolGateway | None = None,
        developer_runtime: DeveloperRuntime | None = None,
        promotion_reviewer: PromotionReviewer | None = None,
        promotion_ci_verifier: PromotionCIVerifier | None = None,
        promotion_release_runtime: PromotionReleaseRuntime | None = None,
        agent_factory: AgentFactory | None = None,
        research_supervisor: Supervisor[Any] | None = None,
        python_supervisor: Supervisor[Any] | None = None,
        api_supervisor: Supervisor[Any] | None = None,
        browser_supervisor: Supervisor[Any] | None = None,
        shell_supervisor: Supervisor[Any] | None = None,
        computer_supervisor: Supervisor[Any] | None = None,
        skill_learning_store: SkillLearningProposalStore | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.gateway = gateway
        self.tool_gateway = tool_gateway
        self.developer_runtime = developer_runtime
        self.promotion_reviewer = promotion_reviewer
        self.promotion_ci_verifier = promotion_ci_verifier
        self.promotion_release_runtime = promotion_release_runtime
        self.agent_factory = agent_factory
        self.research_supervisor = research_supervisor
        self.python_supervisor = python_supervisor
        self.api_supervisor = api_supervisor
        self.browser_supervisor = browser_supervisor
        self.shell_supervisor = shell_supervisor
        self.computer_supervisor = computer_supervisor
        self.skill_learning_store = skill_learning_store

    def _check_space_approval(
        self,
        session: Session,
        agent_execution: AgentExecutionBinding | None,
        computer_spec: ComputerExecutionSpec,
        computer_worker: Any,
    ) -> None:
        """Reject owner takeover and stale Space authority before Task work.

        The Computer runtime and Space gateway also recheck at the final
        use-point; this validation never adds new Computer permissions.
        """
        # The durable owner takeover fence applies to every Agent Computer
        # action, not only the narrower Task-bound Space file gateway.
        # Refuse before recording an Agent Task as well as at retry/claim;
        # runtime control revalidation still protects out-of-band takeovers.
        activation_id = computer_spec.space_activation_id
        if agent_execution is not None and human_takeover_unreleased(
            session, agent_execution.agent_instance_id
        ):
            code = (
                "computer_space_activation_invalid"
                if activation_id is not None
                else "computer_human_control_active"
            )
            raise DomainError(code, 409)
        if activation_id is None:
            return
        if (
            agent_execution is None
            or agent_execution.delegation is not None
            or self.agent_factory is None
            or computer_spec.action not in ("files_read", "files_list")
        ):
            # The only qualified Space file Task is owner-confirmed and
            # nondelegated. Refuse delegated execution before resource lookups
            # at submission, retry and claim, not only at the file use point.
            raise DomainError("computer_space_activation_invalid", 409)
        from app.space_file_gateway import (
            SpaceFileReadGateway,
            _directory_components,
            _path_components,
        )

        try:
            if computer_spec.action == "files_list":
                _directory_components(computer_spec.path)
            else:
                _path_components(computer_spec.path)
            SpaceFileReadGateway(
                self.database,
                self.agent_factory,
                self.settings.runtime_dir,
                computer_worker,
            )._verified_resource(
                session,
                instance_id=agent_execution.agent_instance_id,
                activation_id=activation_id,
                operation="list" if computer_spec.action == "files_list" else "read",
            )
        except DomainError:
            raise DomainError("computer_space_activation_invalid", 409) from None

    @staticmethod
    def _agent_execution_selection(
        selection: dict[str, Any] | None,
        agent_execution_spec: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if agent_execution_spec is None:
            return selection
        return {
            "runtime": selection,
            "agent_execution": agent_execution_spec,
        }

    @staticmethod
    def _delegation_execution_binding(item: AgentDelegation) -> DelegationExecutionBinding:
        return DelegationExecutionBinding(
            delegation_id=item.id,
            parent_instance_id=item.parent_instance_id,
            child_instance_id=item.child_instance_id,
            campaign_id=item.campaign_id,
            budget_reservation_id=item.budget_reservation_id,
            idempotency_key=item.idempotency_key,
            request_hash=item.request_hash,
            objective=item.objective,
            allowed_task_kinds=tuple(item.allowed_task_kinds),
            required_capabilities=tuple(item.required_capabilities),
            depth=item.depth,
            max_depth=item.max_depth,
            budget_resource=item.budget_resource,
            budget_units=item.budget_units,
        )

    def _current_agent_binding(self, instance: AgentInstance) -> AgentBinding:
        try:
            spec = AgentSpec.model_validate(instance.spec)
            return AgentBinding(spec=spec, registry_version=instance.registry_version)
        except ValueError:
            raise DomainError("Agent execution identity is invalid", 409) from None

    @staticmethod
    def _agent_lifecycle_sequence(session: Session, instance_id: str) -> int:
        return (
            session.scalar(
                select(func.max(AgentInstanceEvent.sequence)).where(
                    AgentInstanceEvent.agent_instance_id == instance_id
                )
            )
            or 0
        )

    def _validate_agent_execution(
        self,
        session: Session,
        binding: AgentExecutionBinding,
        *,
        campaign_id: str | None,
    ) -> None:
        if self.agent_factory is None:
            raise DomainError("Agent execution unavailable", 503)

        instance = session.get(AgentInstance, binding.agent_instance_id)
        if instance is None:
            raise DomainError("Agent instance not found", 404)
        if instance.status != "active":
            raise DomainError("Agent instance is not active", 409)
        project = session.get(Project, instance.project_id)
        if project is None or project.status != "active":
            raise DomainError("Agent Project is not active", 409)
        current_binding = self._current_agent_binding(instance)
        if (
            instance.project_id != binding.project_id
            or instance.agent_id != binding.spec.agent_id
            or instance.agent_version != binding.spec.version
            or instance.registry_version != binding.registry_version
            or current_binding.spec != binding.spec
            or not self.agent_factory.matches_agent(current_binding)
            or (
                binding.task_kind == "skill_learning_proposal_run"
                and (binding.spec.skill_learning != "propose" or binding.delegation is not None)
            )
            or (
                binding.task_kind != "skill_learning_proposal_run"
                and binding.task_kind not in binding.spec.allowed_task_kinds
            )
        ):
            raise DomainError("Agent execution identity is stale", 409)

        # An Active role alone is never authority for detached company work.
        # The actual reporting assignment and its exact incoming Delegation
        # are checked on submit, retry, approval and *every* Worker claim.
        validate_youtube_company_execution(session, binding, campaign_id=campaign_id)

        if campaign_id is not None:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise DomainError("Campaign not found", 404)
            goal = session.get(Goal, campaign.goal_id)
            if goal is None or goal.project_id != binding.project_id or campaign.status != "active":
                raise DomainError("Agent task campaign is outside the bound Project", 409)

        delegated = binding.delegation
        if delegated is None:
            return
        if campaign_id != delegated.campaign_id:
            raise DomainError("Delegated agent task must use the delegation Campaign", 409)

        item = session.get(AgentDelegation, delegated.delegation_id)
        if (
            item is None
            or item.status != "active"
            or self._delegation_execution_binding(item) != delegated
        ):
            raise DomainError("Agent delegation is not active and exact", 409)

        parent = session.get(AgentInstance, delegated.parent_instance_id)
        if parent is None or parent.status != "active":
            raise DomainError("Delegating parent agent is not active", 409)
        # A historic or externally injected generic-child Delegation from a
        # company executive must never bypass the atomic trusted org chain.
        validate_youtube_company_child_parent(parent.agent_id, binding.spec.agent_id)
        if parent.project_id != binding.project_id or not self.agent_factory.matches_agent(
            self._current_agent_binding(parent)
        ):
            raise DomainError("Delegating parent agent identity is stale", 409)

        reservation = session.get(CampaignBudgetReservation, delegated.budget_reservation_id)
        if (
            reservation is None
            or reservation.status != "reserved"
            or reservation.expires_at <= utcnow()
            or reservation.campaign_id != delegated.campaign_id
            or reservation.owner_id != delegated.parent_instance_id
            or reservation.resource != delegated.budget_resource
            or reservation.units != delegated.budget_units
        ):
            raise DomainError("Agent delegation budget is not valid", 409)

    @staticmethod
    def _runtime_selection(task: Task) -> dict[str, Any] | None:
        if task.kind in {"ai_prompt", "agent_run", "self_improvement_run"}:
            return {
                "alias": task.selected_alias,
                "model": task.selected_model,
                "config_version": task.config_version,
                "profile": (
                    "general"
                    if task.kind == "self_improvement_run"
                    else task.payload.get("profile")
                ),
            }
        if task.kind == "research_run":
            return {
                "alias": task.selected_alias,
                "model": task.selected_model,
                "config_version": task.config_version,
                "profile": "general",
                "worker_selection": task.research_spec,
            }
        if task.kind == "python_run":
            return {"worker_selection": task.python_spec}
        if task.kind == "api_run":
            return task.api_spec

        if task.kind == "browser_run":
            return task.browser_spec
        if task.kind == "computer_run":
            return task.computer_spec
        if task.kind == "shell_run":
            return task.shell_spec
        if task.kind == "developer_run":
            return task.developer_spec
        if task.kind == "promotion_run":
            return task.promotion_spec
        if task.kind == "merge_run":
            return task.merge_spec
        if task.kind == "release_run":
            return task.release_spec
        return None

    def _action_selection(self, task: Task) -> dict[str, Any] | None:
        return self._agent_execution_selection(
            self._runtime_selection(task),
            task.agent_execution_spec,
        )

    def _bind_campaign_task(self, session: Session, task: Task, request: TaskCreate) -> None:
        dependency_ids = list(request.depends_on_task_ids)
        if len(set(dependency_ids)) != len(dependency_ids):
            raise DomainError("Task dependencies must be unique", 422)
        if request.campaign_id is None:
            if dependency_ids:
                raise DomainError("Task dependencies require campaign ownership", 422)
            return

        campaign = session.get(Campaign, request.campaign_id)
        if campaign is None:
            raise DomainError("Campaign not found", 404)
        if campaign.status != "active":
            raise DomainError("Campaign is not active", 409)

        session.add(CampaignTaskLink(task_id=task.id, campaign_id=campaign.id))
        for dependency_id in dependency_ids:
            if dependency_id == task.id:
                raise DomainError("Task cannot depend on itself", 422)
            dependency = require_task(session, dependency_id)
            dependency_link = session.get(CampaignTaskLink, dependency.id)
            if dependency_link is None or dependency_link.campaign_id != campaign.id:
                raise DomainError("Task dependency must belong to the same campaign", 422)
            session.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency.id))

        append_campaign_event(
            session,
            campaign.id,
            "task_attached",
            task_id=task.id,
            kind=task.kind,
            depends_on_task_ids=dependency_ids,
        )

    def _check_capacity(self, session: Session) -> None:
        count = session.scalar(
            select(func.count()).select_from(Task).where(Task.status.in_(WAITING))
        )
        if count is not None and count >= self.settings.max_waiting_tasks:
            raise DomainError("Waiting task capacity reached; try again later", 503)

    @staticmethod
    def _developer_running_counts(session: Session) -> dict[str, int]:
        counts: dict[str, int] = {}
        running = session.scalars(
            select(Task).where(Task.kind == "developer_run", Task.status == State.RUNNING)
        )
        for task in running:
            try:
                request = DeveloperRequest.model_validate(task.developer_spec)
            except ValueError:
                raise DomainError("Developer routing state unavailable", 503) from None
            worker_id = request.worker_selection.descriptor.worker_id
            counts[worker_id] = counts.get(worker_id, 0) + 1
        return counts

    def _prepare_developer_request(
        self,
        session: Session,
        *,
        payload: dict[str, Any],
        timeout_seconds: float,
        capabilities: tuple[str, ...] = ("zelo_core_development",),
        review_instruction: str | None = None,
    ) -> tuple[DeveloperRequest, dict[str, int]]:
        if self.developer_runtime is None:
            raise DomainError("Developer runtime unavailable", 503)
        running_counts = self._developer_running_counts(session)
        try:
            if capabilities == ("zelo_core_development",):
                request = self.developer_runtime.request(
                    payload["instruction"],
                    payload["validation_profile"],
                    timeout_seconds,
                    running_counts=running_counts,
                )
            else:
                request = self.developer_runtime.request(
                    payload["instruction"],
                    payload["validation_profile"],
                    timeout_seconds,
                    capabilities=capabilities,
                    running_counts=running_counts,
                )
        except DeveloperRuntimeError as error:
            raise DomainError(error.args[0], 422) from None
        if review_instruction is not None:
            request = request.model_copy(update={"review_instruction": review_instruction})
        return request, running_counts

    @staticmethod
    def _audit_developer_binding(
        session: Session,
        task: Task,
        request: DeveloperRequest,
        running_counts: dict[str, int],
    ) -> None:
        selection = request.worker_selection
        audit(
            session,
            task,
            "supervisor_routing_requested",
            role=selection.requested_role,
            capabilities=list(selection.requested_capabilities),
            routing_policy=selection.routing_policy,
        )
        running_count = running_counts.get(selection.descriptor.worker_id, 0)
        audit(
            session,
            task,
            "supervisor_worker_selected",
            worker_id=selection.descriptor.worker_id,
            implementation=selection.descriptor.implementation,
            registry_version=selection.registry_version,
            priority=selection.descriptor.priority,
            running_count=running_count,
            available_capacity=max(0, selection.descriptor.max_concurrency - running_count),
        )
        if request.reviewer is not None:
            reviewer_selection = request.reviewer.worker_selection
            audit(
                session,
                task,
                "reviewer_selected",
                worker_id=reviewer_selection.descriptor.worker_id,
                implementation=reviewer_selection.descriptor.implementation,
                registry_version=reviewer_selection.registry_version,
                authority_profile=reviewer_selection.authority_profile,
                excluded_worker_ids=list(reviewer_selection.excluded_worker_ids),
            )

    @staticmethod
    def _self_improvement_parent(
        session: Session,
        developer_task: Task,
    ) -> tuple[Task, SelfImprovementRunResult] | None:
        key = developer_task.idempotency_key
        if not key or not key.startswith("self-improvement:"):
            return None
        parts = key.split(":")
        if len(parts) != 3 or len(parts[1]) != 36 or len(parts[2]) != 64:
            return None

        parent = session.get(Task, parts[1])
        if (
            parent is None
            or parent.kind != "self_improvement_run"
            or parent.status != State.SUCCEEDED
            or not isinstance(parent.result, dict)
        ):
            return None
        try:
            run_result = SelfImprovementRunResult.model_validate(parent.result)
        except ValueError:
            return None
        if (
            run_result.developer_task_id != developer_task.id
            or run_result.developer_action_hash != developer_task.action_hash
            or run_result.proposal.proposal_fingerprint != parts[2]
        ):
            return None
        return parent, run_result

    def _evaluate_self_improvement_child(
        self,
        session: Session,
        task: Task,
        attempt: TaskAttempt,
        result: dict[str, Any] | None,
    ) -> None:
        linked = self._self_improvement_parent(session, task)
        if linked is None:
            return
        parent, run_result = linked
        proposal = run_result.proposal
        if result is None:
            audit(
                session,
                parent,
                "self_improvement_evaluation_completed",
                developer_task_id=task.id,
                developer_attempt_id=attempt.id,
                status="inconclusive",
                reason="developer_result_unavailable",
            )
            return
        try:
            developer_result = DeveloperRunResult.model_validate(result)
            developer_request = DeveloperRequest.model_validate(task.developer_spec)
            if task.payload.get("instruction") != developer_instruction(
                run_result
            ) or developer_request.review_instruction != developer_review_instruction(run_result):
                raise ValueError("self_improvement_child_context_mismatch")
            review = developer_result.independent_review
            reviewer = developer_request.reviewer
            if review is not None:
                if reviewer is None:
                    raise ValueError("unexpected_independent_review")
                review_selection = reviewer.worker_selection
                if (
                    review.worker_id != review_selection.descriptor.worker_id
                    or review.implementation != review_selection.descriptor.implementation
                    or review.worker_version != reviewer.worker.cli_version
                ):
                    raise ValueError("independent_review_identity_mismatch")
            if (
                developer_result.worker != developer_request.worker.implementation
                or developer_result.worker_version != developer_request.worker.cli_version
                or developer_result.worker_config_version != developer_request.worker.config_version
            ):
                raise ValueError("developer_worker_identity_mismatch")
            evaluation = evaluate_improvement_candidate(
                proposal,
                developer_task_id=task.id,
                developer_attempt_id=attempt.id,
                worker_id=developer_request.worker_selection.descriptor.worker_id,
                worker_implementation=developer_request.worker.implementation,
                review_context_bound=developer_request.review_instruction is not None,
                result=developer_result,
            )
        except (ValueError, SelfImprovementError):
            audit(
                session,
                parent,
                "self_improvement_evaluation_completed",
                developer_task_id=task.id,
                developer_attempt_id=attempt.id,
                status="inconclusive",
                reason="evaluation_evidence_invalid",
            )
            return

        audit(
            session,
            parent,
            "self_improvement_evaluation_completed",
            **evaluation.model_dump(mode="json"),
        )

    def _accepted_self_improvement_evaluation(
        self,
        session: Session,
        source: Task,
        attempt: TaskAttempt,
    ) -> ImprovementEvaluation | None:
        linked = self._self_improvement_parent(session, source)
        if linked is None:
            return None
        parent, run_result = linked
        events = session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.task_id == parent.id,
                AuditEvent.event == "self_improvement_evaluation_completed",
            )
            .order_by(AuditEvent.created_at.desc())
        )
        for event in events:
            details = event.details or {}
            if (
                details.get("developer_task_id") != source.id
                or details.get("developer_attempt_id") != attempt.id
            ):
                continue
            try:
                evaluation = ImprovementEvaluation.model_validate(details)
                developer_result = DeveloperRunResult.model_validate(attempt.result)
            except ValueError:
                raise DomainError("Self-improvement evaluation evidence is invalid") from None
            if (
                evaluation.proposal_fingerprint != run_result.proposal.proposal_fingerprint
                or evaluation.status != "accepted_candidate"
            ):
                raise DomainError("Self-improvement candidate is not accepted for promotion")
            current_diff_sha256 = hashlib.sha256(developer_result.diff.encode("utf-8")).hexdigest()
            if current_diff_sha256 != evaluation.final_diff_sha256:
                raise DomainError("Self-improvement evaluation does not match promotion source")
            return evaluation
        raise DomainError("Self-improvement candidate is not accepted for promotion")

    def _new_approval(self, session: Session, task: Task) -> Approval:
        approval = Approval(
            task_id=task.id,
            action_version=task.action_version,
            action_hash=task.action_hash,
            status="pending",
            expires_at=utcnow() + timedelta(seconds=self.settings.approval_ttl_seconds),
        )
        session.add(approval)
        session.flush()
        audit(session, task, "approval_created", approval_id=approval.id)
        return approval

    def _promotion_source(
        self, developer_task_id: str, developer_attempt_id: str
    ) -> tuple[DeveloperRequest, int, str, str | None, str | None]:
        with self.database.read() as session:
            source = require_task(session, developer_task_id)
            attempt = session.get(TaskAttempt, developer_attempt_id)
            latest_attempt_id = session.scalar(
                select(TaskAttempt.id)
                .where(TaskAttempt.task_id == developer_task_id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            if (
                source.kind != "developer_run"
                or source.status != State.SUCCEEDED
                or source.developer_spec is None
                or attempt is None
                or attempt.task_id != source.id
                or attempt.id != latest_attempt_id
                or attempt.outcome != State.SUCCEEDED
                or attempt.error is not None
                or not isinstance(attempt.result, dict)
                or attempt.result.get("validation_passed") is not True
                or source.result != attempt.result
            ):
                raise DomainError(
                    "Promotion source must be the latest successful developer attempt"
                )
            try:
                developer_request = DeveloperRequest.model_validate(source.developer_spec)
            except ValueError:
                raise DomainError("Promotion source specification is invalid") from None
            evaluation = self._accepted_self_improvement_evaluation(session, source, attempt)
            expected_diff_sha256 = evaluation.final_diff_sha256 if evaluation is not None else None
            expected_candidate_content_sha256 = (
                evaluation.candidate_content_sha256 if evaluation is not None else None
            )
            return (
                developer_request,
                source.action_version,
                source.action_hash,
                expected_diff_sha256,
                expected_candidate_content_sha256,
            )

    def _submit_promotion(self, request: TaskCreate, key: str | None) -> tuple[TaskResponse, bool]:
        request_hash = fingerprint(request.model_dump(mode="json"))
        if key is not None:
            with self.database.read() as session:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False

        if self.promotion_reviewer is None or self.settings.promotion_repository is None:
            raise DomainError("Promotion review unavailable", 503)

        payload = request.payload.model_dump(mode="json")
        (
            developer_request,
            source_version,
            source_hash,
            expected_diff_sha256,
            expected_candidate_content_sha256,
        ) = self._promotion_source(payload["developer_task_id"], payload["developer_attempt_id"])
        try:
            review = asyncio.run(
                self.promotion_reviewer.create(
                    payload["developer_task_id"],
                    payload["developer_attempt_id"],
                    developer_request,
                    validation_passed=True,
                    expected_diff_sha256=expected_diff_sha256,
                    expected_candidate_content_sha256=expected_candidate_content_sha256,
                )
            )
            promotion = PromotionRequest(
                review=review,
                publisher=publisher_identity(self.settings),
                repository=self.settings.promotion_repository,
                target_base_branch=payload["target_base_branch"],
                branch=payload["branch"],
                commit_title=payload["commit_title"],
                commit_message=payload["commit_message"],
                commit_author_name=self.settings.promotion_git_author_name,
                commit_author_email=self.settings.promotion_git_author_email,
                commit_timestamp=utcnow(),
                pr_title=payload["pr_title"],
                pr_body=payload["pr_body"],
            )
        except DeveloperRuntimeError as error:
            raise DomainError(error.args[0]) from None
        except ValueError:
            raise DomainError("Promotion request is invalid", 422) from None

        with self.database.transaction() as session:
            if key is not None:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False
            self._check_capacity(session)
            source = require_task(session, payload["developer_task_id"])
            latest_attempt_id = session.scalar(
                select(TaskAttempt.id)
                .where(TaskAttempt.task_id == source.id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            if (
                source.status != State.SUCCEEDED
                or source.action_version != source_version
                or source.action_hash != source_hash
                or latest_attempt_id != payload["developer_attempt_id"]
            ):
                raise DomainError("Promotion source changed during review")

            spec = promotion.model_dump(mode="json")
            task = Task(
                kind=request.kind,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
                status=State.AWAITING_APPROVAL,
                action_version=1,
                action_hash=action_hash(request.kind, payload, request.timeout_seconds, 1, spec),
                idempotency_key=key,
                request_hash=request_hash,
                promotion_spec=spec,
            )
            session.add(task)
            session.flush()
            self._bind_campaign_task(session, task, request)
            audit(session, task, "task_submitted")
            audit(
                session,
                task,
                "promotion_review_created",
                developer_task_id=review.developer_task_id,
                developer_attempt_id=review.developer_attempt_id,
                base_sha=review.base_sha,
                changed_file_count=review.changed_file_count,
                review_fingerprint=review.review_fingerprint,
            )
            audit(
                session,
                task,
                "state_transition",
                previous=None,
                target=State.AWAITING_APPROVAL,
            )
            approval = self._new_approval(session, task)
            audit(
                session,
                task,
                "promotion_approval_created",
                approval_id=approval.id,
                review_fingerprint=review.review_fingerprint,
            )
            return snapshot(session, task), True

    def _merge_source(
        self, promotion_task_id: str
    ) -> tuple[PromotionRequest, PromotionRunResult, int, str, str]:
        with self.database.read() as session:
            source = require_task(session, promotion_task_id)
            latest = session.scalar(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == promotion_task_id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            if (
                source.kind != "promotion_run"
                or source.status != State.SUCCEEDED
                or source.promotion_spec is None
                or source.result is None
                or latest is None
                or latest.outcome != State.SUCCEEDED
                or latest.error is not None
                or latest.result != source.result
            ):
                raise DomainError("Merge source must be a successful promotion task")
            try:
                promotion = PromotionRequest.model_validate(source.promotion_spec)
                result = PromotionRunResult.model_validate(source.result)
            except ValueError:
                raise DomainError("Merge source specification is invalid") from None
            if (
                result.publication.repository != promotion.repository
                or result.publication.pr_head_sha != result.commit.commit_sha
                or result.publication.pr_base_sha != promotion.review.base_sha
            ):
                raise DomainError("Merge source identities do not match")
            return (
                promotion,
                result,
                source.action_version,
                source.action_hash,
                latest.id,
            )

    def _submit_merge(self, request: TaskCreate, key: str | None) -> tuple[TaskResponse, bool]:
        request_hash = fingerprint(request.model_dump(mode="json"))
        if key is not None:
            with self.database.read() as session:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False

        if self.promotion_ci_verifier is None:
            raise DomainError("Promotion CI verifier unavailable", 503)

        payload = request.payload.model_dump(mode="json")
        promotion, result, source_version, source_hash, source_attempt_id = self._merge_source(
            payload["promotion_task_id"]
        )
        try:
            ci = asyncio.run(
                self.promotion_ci_verifier.verify(
                    promotion,
                    result.publication,
                )
            )
            merge = MergeRequest(
                promotion_task_id=payload["promotion_task_id"],
                promotion_action_version=source_version,
                promotion_action_hash=source_hash,
                promotion=promotion,
                publication=result.publication,
                ci=ci,
                merge_commit_title=promotion.pr_title,
                merge_commit_message=promotion.pr_body,
            )
        except DeveloperRuntimeError as error:
            raise DomainError(error.args[0]) from None
        except ValueError:
            raise DomainError("Merge request is invalid", 422) from None

        with self.database.transaction() as session:
            if key is not None:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False
            self._check_capacity(session)
            source = require_task(session, payload["promotion_task_id"])
            latest_attempt_id = session.scalar(
                select(TaskAttempt.id)
                .where(TaskAttempt.task_id == source.id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            if (
                source.kind != "promotion_run"
                or source.status != State.SUCCEEDED
                or source.action_version != source_version
                or source.action_hash != source_hash
                or source.result != result.model_dump(mode="json")
                or latest_attempt_id != source_attempt_id
            ):
                raise DomainError("Merge source changed during CI verification")

            spec = merge.model_dump(mode="json")
            task = Task(
                kind=request.kind,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
                status=State.AWAITING_APPROVAL,
                action_version=1,
                action_hash=action_hash(request.kind, payload, request.timeout_seconds, 1, spec),
                idempotency_key=key,
                request_hash=request_hash,
                merge_spec=spec,
            )
            session.add(task)
            session.flush()
            self._bind_campaign_task(session, task, request)
            audit(session, task, "task_submitted")
            audit(
                session,
                task,
                "promotion_ci_verified",
                promotion_task_id=source.id,
                pr_number=ci.pr_number,
                head_sha=ci.head_sha,
                base_sha=ci.base_sha,
                required_checks=ci.required_checks,
            )
            audit(
                session,
                task,
                "state_transition",
                previous=None,
                target=State.AWAITING_APPROVAL,
            )
            approval = self._new_approval(session, task)
            audit(
                session,
                task,
                "merge_approval_created",
                approval_id=approval.id,
                promotion_task_id=source.id,
                pr_number=ci.pr_number,
                head_sha=ci.head_sha,
            )
            return snapshot(session, task), True

    def _release_source(
        self, merge_task_id: str
    ) -> tuple[MergeRequest, MergeRunResult, int, str, str]:
        with self.database.read() as session:
            source = require_task(session, merge_task_id)
            latest = session.scalar(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == merge_task_id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            if (
                source.kind != "merge_run"
                or source.status != State.SUCCEEDED
                or source.merge_spec is None
                or source.result is None
                or latest is None
                or latest.outcome != State.SUCCEEDED
                or latest.error is not None
                or latest.result != source.result
            ):
                raise DomainError("Release source must be a successful merge task")
            try:
                merge = MergeRequest.model_validate(source.merge_spec)
                result = MergeRunResult.model_validate(source.result)
            except ValueError:
                raise DomainError("Release source specification is invalid") from None
            if (
                result.repository != merge.promotion.repository
                or result.pr_number != merge.publication.pr_number
                or result.base_branch != merge.publication.base_branch
                or result.head_branch != merge.publication.branch
                or result.head_sha != merge.publication.pr_head_sha
                or result.merge_method != merge.merge_method
            ):
                raise DomainError("Release source identities do not match")
            return merge, result, source.action_version, source.action_hash, latest.id

    def _submit_release(self, request: TaskCreate, key: str | None) -> tuple[TaskResponse, bool]:
        request_hash = fingerprint(request.model_dump(mode="json"))
        if key is not None:
            with self.database.read() as session:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False

        if self.promotion_release_runtime is None or self.settings.promotion_repository is None:
            raise DomainError("Promotion release runtime unavailable", 503)

        payload = request.payload.model_dump(mode="json")
        merge, result, source_version, source_hash, source_attempt_id = self._release_source(
            payload["merge_task_id"]
        )
        # Task approval alone cannot lift the separately held v0.15 release.
        # Refuse before the GitHub readiness GET or creating an approval Task.
        try:
            require_v015_release_scope(payload["tag"], result.merge_commit_sha, self.settings)
        except ValueError:
            raise DomainError("v015_release_scope_approval_required", 409) from None
        try:
            release = ReleaseRequest(
                merge_task_id=payload["merge_task_id"],
                merge_action_version=source_version,
                merge_action_hash=source_hash,
                merge=merge,
                merge_result=result,
                publisher=publisher_identity(self.settings),
                repository=self.settings.promotion_repository,
                main_sha=result.merge_commit_sha,
                tag=payload["tag"],
                tag_message=payload["tag"],
                tagger_name=self.settings.promotion_git_author_name,
                tagger_email=self.settings.promotion_git_author_email,
                tagger_date=utcnow(),
            )
            existing_tag = asyncio.run(self.promotion_release_runtime.verify_ready(release))
        except DeveloperRuntimeError as error:
            raise DomainError(error.args[0]) from None
        except ValueError:
            raise DomainError("Release request is invalid", 422) from None

        with self.database.transaction() as session:
            if key is not None:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False
            self._check_capacity(session)
            source = require_task(session, payload["merge_task_id"])
            latest_attempt_id = session.scalar(
                select(TaskAttempt.id)
                .where(TaskAttempt.task_id == source.id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            if (
                source.kind != "merge_run"
                or source.status != State.SUCCEEDED
                or source.action_version != source_version
                or source.action_hash != source_hash
                or source.merge_spec != merge.model_dump(mode="json")
                or source.result != result.model_dump(mode="json")
                or latest_attempt_id != source_attempt_id
            ):
                raise DomainError("Release source changed during readiness verification")

            spec = release.model_dump(mode="json")
            task = Task(
                kind=request.kind,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
                status=State.AWAITING_APPROVAL,
                action_version=1,
                action_hash=action_hash(request.kind, payload, request.timeout_seconds, 1, spec),
                idempotency_key=key,
                request_hash=request_hash,
                release_spec=spec,
            )
            session.add(task)
            session.flush()
            self._bind_campaign_task(session, task, request)
            audit(session, task, "task_submitted")
            audit(
                session,
                task,
                "release_ready_verified",
                merge_task_id=source.id,
                main_sha=release.main_sha,
                tag=release.tag,
                tag_already_exact=existing_tag is not None,
            )
            audit(
                session,
                task,
                "state_transition",
                previous=None,
                target=State.AWAITING_APPROVAL,
            )
            approval = self._new_approval(session, task)
            audit(
                session,
                task,
                "release_approval_created",
                approval_id=approval.id,
                merge_task_id=source.id,
                main_sha=release.main_sha,
                tag=release.tag,
            )
            return snapshot(session, task), True

    def _browser_preflight(self, worker_id: str | None = None) -> dict[str, Any]:
        validations: dict[str, Any] = {}
        if self.browser_supervisor is None:
            return validations
        registry = self.browser_supervisor.registry
        for descriptor in registry.list():
            if descriptor.role != "browser" or not descriptor.enabled:
                continue
            if worker_id is not None and descriptor.worker_id != worker_id:
                continue
            selected = registry.get(descriptor.worker_id)
            try:
                validations[descriptor.worker_id] = selected.worker.prepare_validation()
            except BrowserRuntimeError:
                validations[descriptor.worker_id] = None
        return validations

    @staticmethod
    def _browser_action_identity(task: Task) -> tuple[Any, ...]:
        return task.id, task.action_version, task.action_hash, task.browser_spec

    def _shell_preflight(self, worker_id: str | None = None) -> dict[str, Any]:
        validations: dict[str, Any] = {}
        if self.shell_supervisor is None:
            return validations
        registry = self.shell_supervisor.registry
        for descriptor in registry.list():
            if descriptor.role != "shell" or not descriptor.enabled:
                continue
            if worker_id is not None and descriptor.worker_id != worker_id:
                continue
            selected = registry.get(descriptor.worker_id)
            try:
                validations[descriptor.worker_id] = selected.worker.prepare_validation()
            except ShellRuntimeError as error:
                validations[descriptor.worker_id] = error
        return validations

    @staticmethod
    def _computer_action_identity(task: Task) -> tuple[Any, ...]:
        return task.id, task.action_version, task.action_hash, task.computer_spec

    @staticmethod
    def _shell_action_identity(task: Task) -> tuple[Any, ...]:
        return task.id, task.action_version, task.action_hash, task.shell_spec

    def submit(
        self,
        request: TaskCreate,
        key: str | None,
        agent_execution: AgentExecutionBinding | None = None,
    ) -> tuple[TaskResponse, bool]:
        if agent_execution is not None and request.kind not in {
            "api_run",
            "browser_run",
            "computer_run",
            "shell_run",
            "developer_run",
            "python_run",
            "research_run",
            "skill_learning_proposal_run",
        }:
            raise DomainError("Agent execution supports only specialist task kinds", 422)
        if request.kind == "skill_learning_proposal_run":
            if agent_execution is None:
                raise DomainError("Skill-learning proposal requires Agent execution", 422)
            if (
                agent_execution.spec.skill_learning != "propose"
                or agent_execution.delegation is not None
            ):
                raise DomainError("Agent skill-learning policy does not allow proposals", 409)
        if request.kind == "computer_run" and agent_execution is None:
            raise DomainError("Computer worker requires Agent execution", 422)
        if request.kind == "promotion_run":
            return self._submit_promotion(request, key)
        if request.kind == "merge_run":
            return self._submit_merge(request, key)
        if request.kind == "release_run":
            return self._submit_release(request, key)

        agent_execution_spec = (
            agent_execution.model_dump(mode="json") if agent_execution is not None else None
        )
        request_payload = request.model_dump(mode="json")
        request_hash = fingerprint(
            request_payload
            if agent_execution_spec is None
            else {
                "request": request_payload,
                "agent_execution": agent_execution_spec,
            }
        )

        browser_validations = self._browser_preflight() if request.kind == "browser_run" else {}
        shell_validations = self._shell_preflight() if request.kind == "shell_run" else {}
        with self.database.transaction() as session:
            if key is not None:
                existing = session.scalar(select(Task).where(Task.idempotency_key == key))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError(
                            "Idempotency key was already used with a different request"
                        )
                    return snapshot(session, existing), False

            self._check_capacity(session)
            if agent_execution is not None:
                self._validate_agent_execution(
                    session,
                    agent_execution,
                    campaign_id=request.campaign_id,
                )
                # The quota is checked under the same BEGIN IMMEDIATE lock as
                # the Task INSERT, after exact idempotency replay resolution.
                # All accepted statuses count to prevent cancel/retry bypass.
                enforce_youtube_research_new_task_quota(session, agent_execution)

            payload = request.payload.model_dump(mode="json")
            task = Task(
                kind=request.kind,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
                status=State.PENDING,
                action_version=1,
                action_hash=action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(None, agent_execution_spec),
                ),
                idempotency_key=key,
                request_hash=request_hash,
                agent_execution_spec=agent_execution_spec,
            )

            if request.kind in {
                "ai_prompt",
                "agent_run",
                "research_run",
                "self_improvement_run",
            }:
                if self.gateway is None:
                    raise DomainError("Model gateway unavailable", 503)
                profile = "general"
                if request.kind in {"ai_prompt", "agent_run"}:
                    profile = payload["profile"]
                try:
                    alias, model = self.gateway.select(profile)
                except Exception:
                    raise DomainError("Invalid model profile", 422) from None
                task.selected_alias, task.selected_model = alias, model
                task.config_version = self.gateway.config_version
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(
                        {
                            "profile": profile,
                            "alias": alias,
                            "model": model,
                            "config_version": task.config_version,
                        },
                        agent_execution_spec,
                    ),
                )

            research_selection = None
            if request.kind == "research_run":
                if self.research_supervisor is None:
                    raise DomainError("Research worker unavailable", 503)
                role = "research"
                capabilities = ("research",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "research":
                        raise DomainError("Agent research worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    _, research_selection = self.research_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                task.research_spec = research_selection.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(
                        {
                            "profile": "general",
                            "alias": task.selected_alias,
                            "model": task.selected_model,
                            "config_version": task.config_version,
                            "worker_selection": task.research_spec,
                        },
                        agent_execution_spec,
                    ),
                )

            python_selection = None
            if request.kind == "python_run":
                if self.python_supervisor is None:
                    raise DomainError("Python worker unavailable", 503)
                role = "python"
                capabilities = ("python_execution",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "python":
                        raise DomainError("Agent Python worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    _, python_selection = self.python_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                task.python_spec = python_selection.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(
                        {"worker_selection": task.python_spec},
                        agent_execution_spec,
                    ),
                )

            api_request = None
            if request.kind == "api_run":
                if self.api_supervisor is None:
                    raise DomainError("API worker unavailable", 503)
                role = "api"
                capabilities = ("api_read",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "api":
                        raise DomainError("Agent API worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    selected, api_selection = self.api_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                    api_request = selected.worker.bind(api_selection, payload["endpoint_id"])
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except ApiRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.api_spec = api_request.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(task.api_spec, agent_execution_spec),
                )

            browser_request = None
            if request.kind == "browser_run":
                if self.browser_supervisor is None:
                    raise DomainError("Browser worker unavailable", 503)
                role = "browser"
                capabilities = ("browser_document",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "browser":
                        raise DomainError("Agent Browser worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    selected, browser_selection = self.browser_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                    browser_request = selected.worker.bind_validated(
                        browser_validations.get(browser_selection.descriptor.worker_id),
                        browser_selection,
                        payload["endpoint_id"],
                        payload["path"],
                        request.timeout_seconds,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except BrowserRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.browser_spec = browser_request.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(task.browser_spec, agent_execution_spec),
                )

            computer_request = None
            if request.kind == "computer_run":
                if self.computer_supervisor is None or agent_execution is None:
                    raise DomainError("Computer worker unavailable", 503)
                required_capability = (
                    "computer_navigate"
                    if payload["action"] == "browser_navigate"
                    else "computer_read"
                )
                if required_capability not in agent_execution.spec.capabilities:
                    raise DomainError("Agent computer capability is invalid", 409)
                try:
                    selected, computer_selection = self.computer_supervisor.select(
                        role="computer",
                        capabilities=(required_capability,),
                    )
                    computer_request = selected.worker.bind(
                        computer_selection,
                        agent_execution,
                        payload["action"],
                        payload.get("path", ""),
                        request.timeout_seconds,
                        endpoint_id=payload.get("endpoint_id"),
                        space_activation_id=payload.get("space_activation_id"),
                    )
                    self._check_space_approval(
                        session, agent_execution, computer_request, selected.worker
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except ComputerRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.computer_spec = computer_request.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(task.computer_spec, agent_execution_spec),
                )

            shell_request = None
            if request.kind == "shell_run":
                if self.shell_supervisor is None:
                    raise DomainError("Shell worker unavailable", 503)
                role = "shell"
                capabilities = ("shell_execution",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "shell":
                        raise DomainError("Agent Shell worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    selected, shell_selection = self.shell_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                    validation = shell_validations.get(shell_selection.descriptor.worker_id)
                    if isinstance(validation, ShellRuntimeError):
                        raise validation
                    shell_request = selected.worker.bind_validated(
                        validation,
                        shell_selection,
                        payload["script"],
                        request.timeout_seconds,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except ShellRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.shell_spec = shell_request.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(task.shell_spec, agent_execution_spec),
                )

            if request.kind == "tool_call":
                if self.tool_gateway is None:
                    raise DomainError("Tool gateway unavailable", 503)
                try:
                    self.tool_gateway.validate(payload["tool"], payload["arguments"])
                except ToolRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None

            developer_request = None
            developer_running_counts: dict[str, int] = {}
            if request.kind == "developer_run":
                capabilities = ("zelo_core_development",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "developer":
                        raise DomainError("Agent developer worker requirement is invalid", 409)
                    capabilities = tuple(requirement.capabilities)

                developer_request, developer_running_counts = self._prepare_developer_request(
                    session,
                    payload=payload,
                    timeout_seconds=request.timeout_seconds,
                    capabilities=capabilities,
                )
                if (
                    agent_execution is not None
                    and agent_execution.spec.review_required
                    and developer_request.reviewer is None
                ):
                    raise DomainError("Agent execution requires independent review", 409)
                task.developer_spec = developer_request.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    self._agent_execution_selection(
                        task.developer_spec,
                        agent_execution_spec,
                    ),
                )

            session.add(task)
            session.flush()
            self._bind_campaign_task(session, task, request)
            audit(session, task, "task_submitted")
            if agent_execution is not None:
                audit(
                    session,
                    task,
                    "agent_execution_bound",
                    agent_instance_id=agent_execution.agent_instance_id,
                    agent_id=agent_execution.spec.agent_id,
                    agent_version=agent_execution.spec.version,
                    registry_version=agent_execution.registry_version,
                    project_id=agent_execution.project_id,
                    delegation_id=(
                        agent_execution.delegation.delegation_id
                        if agent_execution.delegation is not None
                        else None
                    ),
                )

            if task.kind == "developer_run":
                if developer_request is None:
                    raise DomainError("Developer routing state unavailable", 503)
                self._audit_developer_binding(
                    session,
                    task,
                    developer_request,
                    developer_running_counts,
                )

            if task.kind == "research_run":
                if research_selection is None:
                    raise DomainError("Research routing state unavailable", 503)
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=research_selection.requested_role,
                    capabilities=list(research_selection.requested_capabilities),
                    routing_policy=research_selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=research_selection.descriptor.worker_id,
                    implementation=research_selection.descriptor.implementation,
                    registry_version=research_selection.registry_version,
                    authority_profile=research_selection.authority_profile,
                )

            if task.kind == "python_run":
                if python_selection is None:
                    raise DomainError("Python routing state unavailable", 503)
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=python_selection.requested_role,
                    capabilities=list(python_selection.requested_capabilities),
                    routing_policy=python_selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=python_selection.descriptor.worker_id,
                    implementation=python_selection.descriptor.implementation,
                    registry_version=python_selection.registry_version,
                    authority_profile=python_selection.authority_profile,
                )

            if task.kind == "api_run":
                if api_request is None:
                    raise DomainError("API routing state unavailable", 503)
                selection = api_request.worker_selection
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "api_endpoint_bound",
                    endpoint_id=api_request.endpoint.endpoint.endpoint_id,
                    endpoint_registry_version=api_request.endpoint.registry_version,
                    api_policy_version=api_request.policy_version,
                )

            if task.kind == "browser_run":
                if browser_request is None:
                    raise DomainError("Browser routing state unavailable", 503)
                selection = browser_request.worker_selection
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "browser_endpoint_bound",
                    endpoint_id=browser_request.endpoint.endpoint.endpoint_id,
                    endpoint_registry_version=browser_request.endpoint.registry_version,
                    browser_policy_version=browser_request.policy_version,
                )

            if task.kind == "computer_run":
                if computer_request is None:
                    raise DomainError("Computer routing state unavailable", 503)
                selection = computer_request.worker_selection
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "computer_permission_bound",
                    agent_instance_id=computer_request.agent_instance_id,
                    action=computer_request.action,
                    permission_version=computer_request.permission_version,
                    backend_identity=computer_request.backend_identity,
                    endpoint_id=(
                        computer_request.endpoint.endpoint.endpoint_id
                        if computer_request.endpoint is not None
                        else None
                    ),
                    endpoint_registry_version=(
                        computer_request.endpoint.registry_version
                        if computer_request.endpoint is not None
                        else None
                    ),
                    egress_policy_version=computer_request.egress_policy_version,
                )

            if task.kind == "shell_run":
                if shell_request is None:
                    raise DomainError("Shell routing state unavailable", 503)
                selection = shell_request.worker_selection
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "shell_policy_bound",
                    operator_identity=shell_request.operator_identity,
                    shell_policy_version=shell_request.policy_version,
                )

            if task.kind in {"ai_prompt", "agent_run", "research_run", "self_improvement_run"}:
                audit(
                    session,
                    task,
                    "model_selected",
                    alias=task.selected_alias,
                    actual_model=task.selected_model,
                    config_version=task.config_version,
                )
            if task.kind == "tool_call":
                audit(session, task, "tool_selected", tool=task.payload["tool"])
            if task.kind in {
                "approval_demo",
                "ai_prompt",
                "developer_run",
                "promotion_run",
                "merge_run",
                "release_run",
            }:
                task.status = State.AWAITING_APPROVAL
                audit(
                    session,
                    task,
                    "state_transition",
                    previous=None,
                    target=State.AWAITING_APPROVAL,
                )
                self._new_approval(session, task)
            return snapshot(session, task), True

    def finish_self_improvement(
        self,
        execution: Execution,
        preparation: SelfImprovementPreparation,
        instruction: str,
    ) -> SelfImprovementRunResult | None:
        if execution.kind != "self_improvement_run":
            raise DomainError("Self-improvement execution kind mismatch")
        child_request = DeveloperRunInput(
            kind="developer_run",
            payload=DeveloperPayload(
                instruction=instruction,
                validation_profile="zelo_core",
            ),
            timeout_seconds=self.settings.developer_timeout_seconds,
        )
        child_payload = child_request.payload.model_dump(mode="json")
        child_request_hash = fingerprint(child_request.model_dump(mode="json"))
        child_key = (
            f"self-improvement:{execution.task_id}:{preparation.proposal.proposal_fingerprint}"
        )

        with self.database.transaction() as session:
            parent = require_task(session, execution.task_id)
            attempt = session.get(TaskAttempt, execution.attempt_id)
            if attempt is None or attempt.outcome != State.RUNNING:
                raise DomainError("Attempt is not running")
            if parent.kind != "self_improvement_run":
                raise DomainError("Self-improvement parent kind mismatch")
            if parent.payload.get("goal") != preparation.goal:
                raise DomainError("Self-improvement goal changed")
            if (
                parent.selected_alias != preparation.planner_alias
                or parent.selected_model != preparation.planner_model
                or parent.config_version is None
                or preparation.proposal.planner_config_version
                != planner_configuration_version(
                    model_config_version=parent.config_version,
                    alias=preparation.planner_alias,
                    model=preparation.planner_model,
                )
            ):
                raise DomainError("Self-improvement planner binding changed")

            if parent.cancel_requested:
                parent.capacity_backoff_attempts = 0
                parent.capacity_backoff_until = None
                parent.result = None
                parent.error = "cancelled_by_operator"
                transition(session, parent, State.CANCELLED)
                attempt.outcome = State.CANCELLED
                attempt.result = None
                attempt.error = "cancelled_by_operator"
                attempt.ended_at = utcnow()
                audit(
                    session,
                    parent,
                    "cancellation_completed",
                    attempt_id=attempt.id,
                )
                audit(
                    session,
                    parent,
                    "self_improvement_failed",
                    attempt_id=attempt.id,
                    reason="cancelled_by_operator",
                )
                return None

            if session.scalar(select(Task).where(Task.idempotency_key == child_key)) is not None:
                raise DomainError("Self-improvement developer child already exists")

            self._check_capacity(session)
            developer_request, running_counts = self._prepare_developer_request(
                session,
                payload=child_payload,
                timeout_seconds=child_request.timeout_seconds,
                capabilities=tuple(preparation.proposal.draft.required_capabilities),
                review_instruction=developer_review_instruction(preparation),
            )
            if developer_request.base_sha != preparation.base_sha:
                raise DomainError("self_improvement_source_changed")

            developer_spec = developer_request.model_dump(mode="json")
            child = Task(
                kind="developer_run",
                payload=child_payload,
                timeout_seconds=child_request.timeout_seconds,
                status=State.AWAITING_APPROVAL,
                action_version=1,
                action_hash=action_hash(
                    "developer_run",
                    child_payload,
                    child_request.timeout_seconds,
                    1,
                    developer_spec,
                ),
                idempotency_key=child_key,
                request_hash=child_request_hash,
                developer_spec=developer_spec,
            )
            session.add(child)
            session.flush()
            parent_campaign_id = _task_campaign_id(session, parent)
            if parent_campaign_id is not None:
                session.add(
                    CampaignTaskLink(
                        task_id=child.id,
                        campaign_id=parent_campaign_id,
                    )
                )
                append_campaign_event(
                    session,
                    parent_campaign_id,
                    "task_attached",
                    task_id=child.id,
                    kind=child.kind,
                    depends_on_task_ids=[],
                    inherited_from_task_id=parent.id,
                )
            audit(session, child, "task_submitted")
            self._audit_developer_binding(session, child, developer_request, running_counts)
            audit(
                session,
                child,
                "state_transition",
                previous=None,
                target=State.AWAITING_APPROVAL,
            )
            approval = self._new_approval(session, child)

            result = SelfImprovementRunResult(
                **preparation.model_dump(mode="python"),
                developer_task_id=child.id,
                developer_approval_id=approval.id,
                developer_action_hash=child.action_hash,
            )
            result_json = result.model_dump(mode="json")

            parent.capacity_backoff_attempts = 0
            parent.capacity_backoff_until = None
            parent.result = result_json
            parent.error = None
            transition(session, parent, State.SUCCEEDED)
            attempt.outcome = State.SUCCEEDED
            attempt.result = result_json
            attempt.error = None
            attempt.ended_at = utcnow()
            audit(session, parent, "execution_completed", attempt_id=attempt.id)
            audit(
                session,
                parent,
                "self_improvement_proposal_created",
                attempt_id=attempt.id,
                base_sha=preparation.base_sha,
                inspection_fingerprint=preparation.inspection_fingerprint,
                proposal_fingerprint=preparation.proposal.proposal_fingerprint,
                risk=preparation.proposal.draft.risk,
            )
            audit(
                session,
                parent,
                "self_improvement_developer_prepared",
                attempt_id=attempt.id,
                developer_task_id=child.id,
                developer_approval_id=approval.id,
                developer_action_hash=child.action_hash,
                worker_id=developer_request.worker_selection.descriptor.worker_id,
            )
            return result

    def model_event(self, task_id: str, event: str, attempt_id: str, **details: Any) -> None:
        with self.database.transaction() as session:
            task = require_task(session, task_id)
            audit(session, task, event, attempt_id=attempt_id, **details)

    def get(self, task_id: str) -> TaskResponse:
        with self.database.read() as session:
            return snapshot(session, require_task(session, task_id))

    def project(self, task_id: str) -> TaskProjection:
        """Return bounded authoritative task state for conversation/workspace projection."""
        with self.database.read() as session:
            task = require_task(session, task_id)
            attempt_count = (
                session.scalar(
                    select(func.count())
                    .select_from(TaskAttempt)
                    .where(TaskAttempt.task_id == task.id)
                )
                or 0
            )
            latest_attempt = session.scalar(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == task.id)
                .order_by(TaskAttempt.number.desc())
                .limit(1)
            )
            approval_count = (
                session.scalar(
                    select(func.count()).select_from(Approval).where(Approval.task_id == task.id)
                )
                or 0
            )
            latest_approval = session.scalar(
                select(Approval)
                .where(Approval.task_id == task.id)
                .order_by(Approval.created_at.desc(), Approval.id.desc())
                .limit(1)
            )
            projected_result, result_truncated = _project_task_result(
                task.kind,
                task.result,
                self.settings.max_agent_tool_result_chars,
            )
            return TaskProjection(
                id=task.id,
                campaign_id=_task_campaign_id(session, task),
                depends_on_task_ids=_task_dependency_ids(session, task),
                kind=task.kind,
                status=task.status,
                created_at=task.created_at,
                updated_at=task.updated_at,
                cancel_requested=task.cancel_requested,
                result=projected_result,
                result_truncated=result_truncated,
                error=task.error,
                attempt_count=attempt_count,
                latest_attempt_outcome=(
                    latest_attempt.outcome if latest_attempt is not None else None
                ),
                approval_count=approval_count,
                latest_approval_id=(latest_approval.id if latest_approval is not None else None),
                latest_approval_status=(
                    latest_approval.status if latest_approval is not None else None
                ),
                selected_alias=task.selected_alias,
                selected_model=task.selected_model,
            )

    def list_tasks(self, limit: int, offset: int) -> TaskPage:
        with self.database.read() as session:
            total = session.scalar(select(func.count()).select_from(Task)) or 0
            tasks = session.scalars(
                select(Task).order_by(Task.created_at, Task.id).limit(limit).offset(offset)
            )
            return TaskPage(
                items=[snapshot(session, task) for task in tasks],
                total=total,
                limit=limit,
                offset=offset,
            )

    def events(self, task_id: str, limit: int, offset: int) -> list[AuditResponse]:
        with self.database.read() as session:
            require_task(session, task_id)
            return [
                AuditResponse.model_validate(event)
                for event in session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.task_id == task_id)
                    # SQLite is the only configured backend. Audit rows are append-only;
                    # wall-clock timestamps can tie or regress, and UUID4 IDs are
                    # not insertion-ordered. Preserve actual database append order.
                    .order_by(literal_column("audit_events.rowid"))
                    .limit(limit)
                    .offset(offset)
                )
            ]

    def _publisher_compatible(self, expected: Any, *, live: bool) -> bool:
        if not live:
            return True
        try:
            return expected == publisher_identity(self.settings)
        except DeveloperRuntimeError as error:
            if str(error) == "promotion_github_cli_unavailable":
                raise DeveloperRuntimeError("approval_revalidation_unavailable") from None
            return False

    def _valid_action(self, task: Task, approval: Approval, *, live: bool = True) -> bool:
        selection = None
        if task.kind in {"ai_prompt", "agent_run", "research_run", "self_improvement_run"}:
            selection = {
                "alias": task.selected_alias,
                "model": task.selected_model,
                "config_version": task.config_version,
                "profile": (
                    "general"
                    if task.kind in {"research_run", "self_improvement_run"}
                    else task.payload.get("profile")
                ),
            }
        elif task.kind == "developer_run":
            selection = task.developer_spec
            try:
                request = DeveloperRequest.model_validate(selection)
                if (
                    self.developer_runtime is None
                    or not self.developer_runtime.compatible(request, task.timeout_seconds)
                    or request.instruction != task.payload["instruction"]
                ):
                    return False
                if live and not self.developer_runtime.approval_compatible(
                    request, task.timeout_seconds
                ):
                    return False
            except DeveloperRuntimeError as error:
                if str(error) == "approval_revalidation_unavailable":
                    raise
                return False
            except ValueError:
                return False
        elif task.kind == "promotion_run":
            selection = task.promotion_spec
            try:
                request = PromotionRequest.model_validate(selection)
                payload = task.payload
                if (
                    self.settings.promotion_repository is None
                    or request.repository != self.settings.promotion_repository
                    or not self._publisher_compatible(request.publisher, live=live)
                    or request.review.developer_task_id != payload["developer_task_id"]
                    or request.review.developer_attempt_id != payload["developer_attempt_id"]
                    or request.target_base_branch != payload["target_base_branch"]
                    or request.branch != payload["branch"]
                    or request.commit_title != payload["commit_title"]
                    or request.commit_message != payload["commit_message"]
                    or request.pr_title != payload["pr_title"]
                    or request.pr_body != payload["pr_body"]
                ):
                    return False
            except DeveloperRuntimeError as error:
                if str(error) == "approval_revalidation_unavailable":
                    raise
                return False
            except (ValueError, TypeError, KeyError):
                return False
        elif task.kind == "merge_run":
            selection = task.merge_spec
            try:
                request = MergeRequest.model_validate(selection)
                if (
                    request.promotion_task_id != task.payload["promotion_task_id"]
                    or self.settings.promotion_repository is None
                    or request.promotion.repository != self.settings.promotion_repository
                    or not self._publisher_compatible(request.promotion.publisher, live=live)
                ):
                    return False
            except DeveloperRuntimeError as error:
                if str(error) == "approval_revalidation_unavailable":
                    raise
                return False
            except (ValueError, TypeError, KeyError):
                return False
        elif task.kind == "release_run":
            selection = task.release_spec
            try:
                request = ReleaseRequest.model_validate(selection)
                # Cancel a queued/approved v0.15 release if its exact host pin
                # was removed or changed *before* Task claim or approval.
                # PromotionReleaseRuntime independently rechecks at the PUT.
                require_v015_release_scope(request.tag, request.main_sha, self.settings)
                if (
                    request.merge_task_id != task.payload["merge_task_id"]
                    or request.tag != task.payload["tag"]
                    or self.settings.promotion_repository is None
                    or request.repository != self.settings.promotion_repository
                    or (live and request.publisher != publisher_identity(self.settings))
                ):
                    return False
            except DeveloperRuntimeError as error:
                if str(error) == "approval_revalidation_unavailable":
                    raise
                return False
            except (ValueError, TypeError, KeyError):
                return False
        selection = self._agent_execution_selection(selection, task.agent_execution_spec)
        current = action_hash(
            task.kind, task.payload, task.timeout_seconds, task.action_version, selection
        )
        return (
            approval.action_version == task.action_version
            and approval.action_hash == current == task.action_hash
        )

    def _cancel_approval(
        self, session: Session, task: Task, approval: Approval, reason: str
    ) -> None:
        approval.status = reason
        approval.decided_at = utcnow()
        task.error = f"approval_{reason}"
        audit(session, task, f"approval_{reason}", approval_id=approval.id)
        transition(session, task, State.CANCELLED)

    def _expire_or_invalidate(
        self,
        session: Session,
        task: Task,
        approval: Approval,
        *,
        revalidate: bool = False,
    ) -> bool:
        if approval.status not in {"pending", "approved"} or task.status not in WAITING:
            return False
        if approval.expires_at <= utcnow():
            self._cancel_approval(session, task, approval, "expired")
            return True
        try:
            valid = self._valid_action(task, approval, live=revalidate)
        except DeveloperRuntimeError as error:
            if str(error) == "approval_revalidation_unavailable":
                return False
            valid = False
        if not valid:
            self._cancel_approval(session, task, approval, "invalidated")
            return True
        return False

    def expire_approvals(self) -> None:
        """Expire TTLs and cheap structural mismatches only.

        Expensive developer worker/venv revalidation is performed at the operator
        approval boundary and again by DeveloperRuntime immediately before execution.
        """
        with self.database.transaction() as session:
            pairs = session.execute(
                select(Task, Approval)
                .join(Approval, Task.id == Approval.task_id)
                .where(Task.status.in_(WAITING), Approval.status.in_(("pending", "approved")))
            ).all()
            for task, approval in pairs:
                self._expire_or_invalidate(session, task, approval, revalidate=False)

    def _live_approval_validity(self, approval_id: str) -> bool | None:
        with self.database.read() as session:
            approval = session.get(Approval, approval_id)
            if approval is None:
                raise DomainError("Approval not found", 404)
            task = require_task(session, approval.task_id)
            if (
                approval.status != "pending"
                or task.status != State.AWAITING_APPROVAL
                or approval.expires_at <= utcnow()
            ):
                return None
            try:
                if task.agent_execution_spec is not None:
                    binding = AgentExecutionBinding.model_validate(task.agent_execution_spec)
                    self._validate_agent_execution(
                        session,
                        binding,
                        campaign_id=_task_campaign_id(session, task),
                    )
                return self._valid_action(task, approval, live=True)
            except (ValueError, DomainError):
                return False
            except DeveloperRuntimeError as error:
                if str(error) == "approval_revalidation_unavailable":
                    raise DomainError(
                        "Approval revalidation temporarily unavailable", 503
                    ) from None
                raise

    def decide(self, approval_id: str, decision: str) -> ApprovalResponse:
        live_valid = self._live_approval_validity(approval_id) if decision == "approve" else None
        error = None
        with self.database.transaction() as session:
            approval = session.get(Approval, approval_id)
            if approval is None:
                raise DomainError("Approval not found", 404)
            task = require_task(session, approval.task_id)
            self._expire_or_invalidate(session, task, approval, revalidate=False)
            if (
                decision == "approve"
                and live_valid is False
                and approval.status == "pending"
                and task.status == State.AWAITING_APPROVAL
            ):
                self._cancel_approval(session, task, approval, "invalidated")
            target = "approved" if decision == "approve" else "rejected"
            if approval.status == target:
                pass  # Idempotent decision: no state changes, events, or scheduling.
            elif approval.status != "pending" or task.status != State.AWAITING_APPROVAL:
                error = DomainError("Approval is no longer pending or the decision conflicts")
            else:
                approval.status = target
                approval.decided_at = utcnow()
                audit(session, task, f"approval_{target}", approval_id=approval.id)
                if decision == "approve":
                    transition(session, task, State.PENDING)
                else:
                    task.error = "approval_rejected"
                    transition(session, task, State.CANCELLED)
            response = ApprovalResponse.model_validate(approval)
        # Commit expiry/invalidation even when the late decision must return a conflict.
        if error:
            raise error
        return response

    def cancel(self, task_id: str) -> TaskResponse:
        with self.database.transaction() as session:
            task = require_task(session, task_id)
            if task.status == State.CANCELLED:
                return snapshot(session, task)
            if task.status not in (*WAITING, State.RUNNING):
                raise DomainError(f"Cannot cancel task in state {task.status}")
            if not task.cancel_requested:
                task.cancel_requested = True
                task.updated_at = utcnow()
                audit(session, task, "cancellation_requested")
            if task.status == State.RUNNING:
                attempt = session.scalar(
                    select(TaskAttempt).where(
                        TaskAttempt.task_id == task.id, TaskAttempt.outcome == State.RUNNING
                    )
                )
                if attempt and attempt.cancellation_requested_at is None:
                    attempt.cancellation_requested_at = utcnow()
            else:
                for approval in session.scalars(
                    select(Approval).where(
                        Approval.task_id == task.id,
                        Approval.status.in_(("pending", "approved")),
                    )
                ):
                    approval.status = "cancelled"
                    approval.decided_at = utcnow()
                    audit(session, task, "approval_cancelled", approval_id=approval.id)
                task.error = "cancelled_by_operator"
                transition(session, task, State.CANCELLED)
                audit(session, task, "cancellation_completed")
            return snapshot(session, task)

    def retry(self, task_id: str) -> TaskResponse:
        browser_action = None
        browser_validations = {}
        computer_action = None
        shell_action = None
        shell_validations = {}
        with self.database.read() as session:
            candidate = require_task(session, task_id)
            if candidate.kind == "browser_run":
                browser_action = self._browser_action_identity(candidate)
            if candidate.kind == "computer_run":
                computer_action = self._computer_action_identity(candidate)
            if candidate.kind == "shell_run":
                shell_action = self._shell_action_identity(candidate)
        if browser_action is not None:
            browser_validations = self._browser_preflight()
        if shell_action is not None:
            shell_validations = self._shell_preflight()
        with self.database.transaction() as session:
            task = require_task(session, task_id)
            if browser_action is not None and browser_action != self._browser_action_identity(task):
                raise DomainError("Browser task changed during validation", 409)
            if computer_action is not None and computer_action != self._computer_action_identity(
                task
            ):
                raise DomainError("Computer task changed during validation", 409)
            if shell_action is not None and shell_action != self._shell_action_identity(task):
                raise DomainError("Shell task changed during validation", 409)
            agent_execution = None
            if task.agent_execution_spec is not None:
                try:
                    agent_execution = AgentExecutionBinding.model_validate(
                        task.agent_execution_spec
                    )
                except ValueError:
                    raise DomainError("Agent execution identity is invalid", 409) from None
                self._validate_agent_execution(
                    session,
                    agent_execution,
                    campaign_id=_task_campaign_id(session, task),
                )
            if task.status == State.RUNNING:
                raise DomainError(f"Cannot retry task in state {task.status}")
            if (
                task.kind == "developer_run"
                and task.idempotency_key
                and task.idempotency_key.startswith("self-improvement:")
            ):
                raise DomainError(
                    "Self-improvement developer child requires a fresh self-improvement run"
                )
            approval_kinds = {
                "approval_demo",
                "ai_prompt",
                "developer_run",
                "promotion_run",
                "merge_run",
                "release_run",
            }
            target = State.AWAITING_APPROVAL if task.kind in approval_kinds else State.PENDING
            validate_transition(task.status, target)
            if task.kind == "release_run":
                # A revoked milestone scope must refuse before creating another
                # pending approval or incrementing the immutable action version.
                # Approval/claim/side-effect checks remain independent fences.
                try:
                    release = ReleaseRequest.model_validate(task.release_spec)
                except (TypeError, ValueError):
                    raise DomainError("Release Task identity is invalid", 409) from None
                try:
                    require_v015_release_scope(release.tag, release.main_sha, self.settings)
                except ValueError:
                    raise DomainError("v015_release_scope_approval_required", 409) from None
            self._check_capacity(session)
            audit(session, task, "retry_requested")
            task.capacity_backoff_attempts = 0
            task.capacity_backoff_until = None
            for approval in session.scalars(
                select(Approval).where(
                    Approval.task_id == task.id,
                    Approval.status.in_(("pending", "approved")),
                )
            ):
                approval.status = "cancelled"
                approval.decided_at = utcnow()
                audit(
                    session,
                    task,
                    "approval_cancelled",
                    approval_id=approval.id,
                    reason="retry_superseded",
                )
            transition(session, task, target)
            task.action_version += 1
            if task.kind == "developer_run":
                if self.developer_runtime is None:
                    raise DomainError("Developer runtime unavailable", 503)
                try:
                    kwargs: dict[str, Any] = {}
                    if agent_execution is not None:
                        requirement = agent_execution.spec.worker_requirement
                        if requirement is None or requirement.role != "developer":
                            raise DomainError(
                                "Agent developer worker requirement is invalid",
                                409,
                            )
                        kwargs["capabilities"] = tuple(requirement.capabilities)
                    refreshed = self.developer_runtime.request(
                        task.payload["instruction"],
                        task.payload["validation_profile"],
                        task.timeout_seconds,
                        **kwargs,
                    )
                    if (
                        agent_execution is not None
                        and agent_execution.spec.review_required
                        and refreshed.reviewer is None
                    ):
                        raise DomainError("Agent execution requires independent review", 409)
                    task.developer_spec = refreshed.model_dump(mode="json")
                except DeveloperRuntimeError as error:
                    raise DomainError(str(error), 422) from None
                selection = DeveloperRequest.model_validate(task.developer_spec).worker_selection
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.descriptor.role,
                    capabilities=list(selection.descriptor.capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    priority=selection.descriptor.priority,
                )
            if task.kind == "research_run":
                if self.research_supervisor is None:
                    raise DomainError("Research worker unavailable", 503)
                role = "research"
                capabilities = ("research",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "research":
                        raise DomainError("Agent research worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    _, selection = self.research_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                task.research_spec = selection.model_dump(mode="json")
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
            if task.kind == "python_run":
                if self.python_supervisor is None:
                    raise DomainError("Python worker unavailable", 503)
                role = "python"
                capabilities = ("python_execution",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "python":
                        raise DomainError("Agent Python worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    _, selection = self.python_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                task.python_spec = selection.model_dump(mode="json")
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
            if task.kind == "api_run":
                if self.api_supervisor is None:
                    raise DomainError("API worker unavailable", 503)
                role = "api"
                capabilities = ("api_read",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "api":
                        raise DomainError("Agent API worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    selected, selection = self.api_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                    refreshed_api = selected.worker.bind(selection, task.payload["endpoint_id"])
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except ApiRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.api_spec = refreshed_api.model_dump(mode="json")
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "api_endpoint_bound",
                    endpoint_id=refreshed_api.endpoint.endpoint.endpoint_id,
                    endpoint_registry_version=refreshed_api.endpoint.registry_version,
                    api_policy_version=refreshed_api.policy_version,
                )

            if task.kind == "browser_run":
                if self.browser_supervisor is None:
                    raise DomainError("Browser worker unavailable", 503)
                role = "browser"
                capabilities = ("browser_document",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "browser":
                        raise DomainError("Agent Browser worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    selected, selection = self.browser_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                    refreshed_browser = selected.worker.bind_validated(
                        browser_validations.get(selection.descriptor.worker_id),
                        selection,
                        task.payload["endpoint_id"],
                        task.payload["path"],
                        task.timeout_seconds,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except BrowserRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.browser_spec = refreshed_browser.model_dump(mode="json")
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "browser_endpoint_bound",
                    endpoint_id=refreshed_browser.endpoint.endpoint.endpoint_id,
                    endpoint_registry_version=refreshed_browser.endpoint.registry_version,
                    browser_policy_version=refreshed_browser.policy_version,
                )

            if task.kind == "computer_run":
                if self.computer_supervisor is None:
                    raise DomainError("Computer worker unavailable", 503)
                if agent_execution is None:
                    raise DomainError("Computer worker requires Agent execution", 409)
                required_capability = (
                    "computer_navigate"
                    if task.payload["action"] == "browser_navigate"
                    else "computer_read"
                )
                if required_capability not in agent_execution.spec.capabilities:
                    raise DomainError("Agent computer capability is invalid", 409)
                try:
                    selected, selection = self.computer_supervisor.select(
                        role="computer",
                        capabilities=(required_capability,),
                    )
                    refreshed_computer = selected.worker.bind(
                        selection,
                        agent_execution,
                        task.payload["action"],
                        task.payload.get("path", ""),
                        task.timeout_seconds,
                        endpoint_id=task.payload.get("endpoint_id"),
                        space_activation_id=task.payload.get("space_activation_id"),
                    )
                    # The takeover/Space checks issue SELECTs. Keep the new
                    # action version and its rebound Computer identity in one
                    # eventual flush, preserving the DB immutability trigger.
                    with session.no_autoflush:
                        self._check_space_approval(
                            session, agent_execution, refreshed_computer, selected.worker
                        )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except ComputerRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.computer_spec = refreshed_computer.model_dump(mode="json")
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "computer_permission_bound",
                    agent_instance_id=refreshed_computer.agent_instance_id,
                    action=refreshed_computer.action,
                    permission_version=refreshed_computer.permission_version,
                    backend_identity=refreshed_computer.backend_identity,
                    endpoint_id=(
                        refreshed_computer.endpoint.endpoint.endpoint_id
                        if refreshed_computer.endpoint is not None
                        else None
                    ),
                    endpoint_registry_version=(
                        refreshed_computer.endpoint.registry_version
                        if refreshed_computer.endpoint is not None
                        else None
                    ),
                    egress_policy_version=refreshed_computer.egress_policy_version,
                )

            if task.kind == "shell_run":
                if self.shell_supervisor is None:
                    raise DomainError("Shell worker unavailable", 503)
                role = "shell"
                capabilities = ("shell_execution",)
                if agent_execution is not None:
                    requirement = agent_execution.spec.worker_requirement
                    if requirement is None or requirement.role != "shell":
                        raise DomainError("Agent Shell worker requirement is invalid", 409)
                    role = requirement.role
                    capabilities = tuple(requirement.capabilities)
                try:
                    selected, selection = self.shell_supervisor.select(
                        role=role,
                        capabilities=capabilities,
                    )
                    validation = shell_validations.get(selection.descriptor.worker_id)
                    if isinstance(validation, ShellRuntimeError):
                        raise validation
                    refreshed_shell = selected.worker.bind_validated(
                        validation,
                        selection,
                        task.payload["script"],
                        task.timeout_seconds,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                except ShellRuntimeError as error:
                    raise DomainError(error.code, 422) from None
                task.shell_spec = refreshed_shell.model_dump(mode="json")
                audit(
                    session,
                    task,
                    "supervisor_routing_requested",
                    role=selection.requested_role,
                    capabilities=list(selection.requested_capabilities),
                    routing_policy=selection.routing_policy,
                )
                audit(
                    session,
                    task,
                    "supervisor_worker_selected",
                    worker_id=selection.descriptor.worker_id,
                    implementation=selection.descriptor.implementation,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "shell_policy_bound",
                    operator_identity=refreshed_shell.operator_identity,
                    shell_policy_version=refreshed_shell.policy_version,
                )
            task.action_hash = action_hash(
                task.kind,
                task.payload,
                task.timeout_seconds,
                task.action_version,
                self._action_selection(task),
            )
            task.cancel_requested = False
            task.result = None
            task.error = None
            if task.kind in approval_kinds:
                if task.kind == "ai_prompt":
                    audit(
                        session,
                        task,
                        "model_selected",
                        alias=task.selected_alias,
                        actual_model=task.selected_model,
                        config_version=task.config_version,
                    )
                approval = self._new_approval(session, task)
                if task.kind == "promotion_run":
                    audit(
                        session,
                        task,
                        "promotion_approval_created",
                        approval_id=approval.id,
                        review_fingerprint=task.promotion_spec["review"]["review_fingerprint"],
                    )
                elif task.kind == "merge_run":
                    audit(
                        session,
                        task,
                        "merge_approval_created",
                        approval_id=approval.id,
                        promotion_task_id=task.merge_spec["promotion_task_id"],
                        head_sha=task.merge_spec["publication"]["pr_head_sha"],
                    )
                elif task.kind == "release_run":
                    audit(
                        session,
                        task,
                        "release_approval_created",
                        approval_id=approval.id,
                        merge_task_id=task.release_spec["merge_task_id"],
                        main_sha=task.release_spec["main_sha"],
                        tag=task.release_spec["tag"],
                    )
            return snapshot(session, task)

    @staticmethod
    def _dependencies_satisfied(session: Session, task: Task) -> bool:
        dependency_ids = _task_dependency_ids(session, task)
        if not dependency_ids:
            return True
        states = list(session.scalars(select(Task.status).where(Task.id.in_(dependency_ids))))
        return len(states) == len(dependency_ids) and all(
            state == State.SUCCEEDED for state in states
        )

    def _claimable_task(self, session: Session) -> Task | None:
        running = list(session.scalars(select(Task).where(Task.status == State.RUNNING)))
        pending = list(
            session.scalars(
                select(Task)
                .where(
                    Task.status == State.PENDING,
                    Task.cancel_requested.is_(False),
                    or_(
                        Task.capacity_backoff_until.is_(None),
                        Task.capacity_backoff_until <= utcnow(),
                    ),
                )
                .order_by(Task.created_at, Task.id)
                .limit(self.settings.max_waiting_tasks)
            )
        )
        pending = [task for task in pending if self._dependencies_satisfied(session, task)]
        if not pending:
            return None

        # v0.9 permits concurrency only between developer tasks. Every other task
        # remains an exclusive barrier so external actions and model/tool tasks keep
        # the existing single-operation semantics.
        running_counts: dict[str, int] = {}
        for current in running:
            if current.kind != "developer_run":
                return None
            try:
                request = DeveloperRequest.model_validate(current.developer_spec)
            except ValueError:
                return None
            worker_id = request.worker_selection.descriptor.worker_id
            running_counts[worker_id] = running_counts.get(worker_id, 0) + 1

        if not running:
            return pending[0]

        for candidate in pending:
            if candidate.kind != "developer_run":
                # Backed-off tasks are temporarily invisible here, so later runnable
                # work may overtake them. Among currently claimable tasks, a
                # non-developer entry remains an exclusivity barrier.
                return None
            try:
                request = DeveloperRequest.model_validate(candidate.developer_spec)
            except ValueError:
                # Let the normal claim validation cancel the malformed task.
                return candidate
            descriptor = request.worker_selection.descriptor
            if running_counts.get(descriptor.worker_id, 0) < descriptor.max_concurrency:
                return candidate
        return None

    def claim(self) -> Execution | None:
        browser_action = None
        browser_validations = {}
        if self.browser_supervisor is not None:
            with self.database.read() as session:
                candidate = self._claimable_task(session)
                if candidate is None:
                    return None
                if candidate.kind == "browser_run":
                    browser_action = self._browser_action_identity(candidate)
                    try:
                        worker_id = BrowserExecutionSpec.model_validate(
                            candidate.browser_spec
                        ).worker_selection.descriptor.worker_id
                    except ValueError:
                        worker_id = ""
            if browser_action is not None:
                browser_validations = self._browser_preflight(worker_id)
        shell_action = None
        shell_validations = {}
        if self.shell_supervisor is not None:
            with self.database.read() as session:
                candidate = self._claimable_task(session)
                if candidate is None:
                    return None
                if candidate.kind == "shell_run":
                    shell_action = self._shell_action_identity(candidate)
                    try:
                        worker_id = ShellExecutionSpec.model_validate(
                            candidate.shell_spec
                        ).worker_selection.descriptor.worker_id
                    except ValueError:
                        worker_id = ""
            if shell_action is not None:
                shell_validations = self._shell_preflight(worker_id)
                validation = shell_validations.get(worker_id)
                if isinstance(validation, ShellRuntimeError) and validation.code in {
                    "shell_admission_denied",
                    "shell_cleanup_unproven",
                }:
                    # A competing metadata lease or uncertain resource fence
                    # defers this queued action; neither invalidates its identity.
                    return None
        with self.database.transaction() as session:
            task = self._claimable_task(session)
            if task is None:
                return None
            if (
                task.kind == "browser_run"
                and self.browser_supervisor is not None
                and (
                    browser_action is None or browser_action != self._browser_action_identity(task)
                )
            ):
                return None

            if (
                task.kind == "shell_run"
                and self.shell_supervisor is not None
                and (shell_action is None or shell_action != self._shell_action_identity(task))
            ):
                return None

            agent_execution = None
            research_agent_lifecycle = None
            if task.agent_execution_spec is not None:
                try:
                    agent_execution = AgentExecutionBinding.model_validate(
                        task.agent_execution_spec
                    )
                    self._validate_agent_execution(
                        session,
                        agent_execution,
                        campaign_id=_task_campaign_id(session, task),
                    )
                except (ValueError, DomainError):
                    task.error = "agent_execution_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "agent_execution_validated",
                    agent_instance_id=agent_execution.agent_instance_id,
                    delegation_id=(
                        agent_execution.delegation.delegation_id
                        if agent_execution.delegation is not None
                        else None
                    ),
                )
                if task.kind == "research_run":
                    # Capture a monotonic fence so a pause/resume cannot restore
                    # authority to this already-claimed execution.
                    delegated = agent_execution.delegation
                    research_agent_lifecycle = (
                        self._agent_lifecycle_sequence(session, agent_execution.agent_instance_id),
                        self._agent_lifecycle_sequence(session, delegated.parent_instance_id)
                        if delegated is not None
                        else None,
                    )

            if task.kind in {
                "approval_demo",
                "ai_prompt",
                "developer_run",
                "promotion_run",
                "merge_run",
                "release_run",
            }:
                approval = session.scalar(
                    select(Approval)
                    .where(Approval.task_id == task.id)
                    .order_by(Approval.created_at.desc())
                    .limit(1)
                )
                if approval and self._expire_or_invalidate(
                    session, task, approval, revalidate=False
                ):
                    return None
                if (
                    approval is None
                    or approval.status != "approved"
                    or not self._valid_action(task, approval, live=False)
                ):
                    task.error = "approval_missing_or_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
            if task.kind == "self_improvement_run":
                try:
                    if self.gateway is None:
                        raise ValueError("gateway_unavailable")
                    alias, model = self.gateway.select("general")
                    if (
                        alias != task.selected_alias
                        or model != task.selected_model
                        or self.gateway.config_version != task.config_version
                    ):
                        raise ValueError("planner_selection_changed")
                except Exception:
                    task.error = "planner_selection_changed"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
            research_selection = None
            if task.kind == "research_run":
                try:
                    if self.research_supervisor is None or task.research_spec is None:
                        raise ValueError("research_worker_selection_unavailable")
                    research_selection = WorkerSelectionSnapshot.model_validate(task.research_spec)
                    if not self.research_supervisor.matches(research_selection):
                        raise ValueError("research_worker_selection_stale")
                    if agent_execution is not None:
                        requirement = agent_execution.spec.worker_requirement
                        if (
                            requirement is None
                            or research_selection.requested_role != requirement.role
                            or tuple(research_selection.requested_capabilities)
                            != tuple(requirement.capabilities)
                        ):
                            raise ValueError("research_worker_requirement_mismatch")
                except ValueError:
                    task.error = "research_worker_selection_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "supervisor_worker_revalidated",
                    worker_id=research_selection.descriptor.worker_id,
                    registry_version=research_selection.registry_version,
                    authority_profile=research_selection.authority_profile,
                )

            python_selection = None
            if task.kind == "python_run":
                try:
                    if self.python_supervisor is None or task.python_spec is None:
                        raise ValueError("python_worker_selection_unavailable")
                    python_selection = WorkerSelectionSnapshot.model_validate(task.python_spec)
                    if not self.python_supervisor.matches(python_selection):
                        raise ValueError("python_worker_selection_stale")
                    if agent_execution is not None:
                        requirement = agent_execution.spec.worker_requirement
                        if (
                            requirement is None
                            or python_selection.requested_role != requirement.role
                            or tuple(python_selection.requested_capabilities)
                            != tuple(requirement.capabilities)
                        ):
                            raise ValueError("python_worker_requirement_mismatch")
                except ValueError:
                    task.error = "python_worker_selection_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "supervisor_worker_revalidated",
                    worker_id=python_selection.descriptor.worker_id,
                    registry_version=python_selection.registry_version,
                    authority_profile=python_selection.authority_profile,
                )

            api_request = None
            if task.kind == "api_run":
                try:
                    if self.api_supervisor is None or task.api_spec is None:
                        raise ValueError("api_execution_spec_unavailable")
                    api_request = ApiExecutionSpec.model_validate(task.api_spec)
                    selection = api_request.worker_selection
                    selected = self.api_supervisor.registry.get(selection.descriptor.worker_id)
                    if (
                        selected is None
                        or not self.api_supervisor.matches(selection)
                        or not selected.worker.matches(api_request)
                    ):
                        raise ValueError("api_execution_spec_stale")
                    if agent_execution is not None:
                        requirement = agent_execution.spec.worker_requirement
                        if (
                            requirement is None
                            or selection.requested_role != requirement.role
                            or tuple(selection.requested_capabilities)
                            != tuple(requirement.capabilities)
                        ):
                            raise ValueError("api_worker_requirement_mismatch")
                except ValueError:
                    task.error = "api_execution_spec_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "supervisor_worker_revalidated",
                    worker_id=selection.descriptor.worker_id,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "api_endpoint_revalidated",
                    endpoint_id=api_request.endpoint.endpoint.endpoint_id,
                    endpoint_registry_version=api_request.endpoint.registry_version,
                    api_policy_version=api_request.policy_version,
                )

            browser_request = None
            if task.kind == "browser_run":
                try:
                    if self.browser_supervisor is None or task.browser_spec is None:
                        raise ValueError("browser_execution_spec_unavailable")
                    browser_request = BrowserExecutionSpec.model_validate(task.browser_spec)
                    selection = browser_request.worker_selection
                    selected = self.browser_supervisor.registry.get(selection.descriptor.worker_id)
                    if (
                        selected is None
                        or not self.browser_supervisor.matches(selection)
                        or not selected.worker.matches_validated(
                            browser_validations.get(selection.descriptor.worker_id), browser_request
                        )
                        or not selected.worker.request_matches(
                            browser_request, task.payload, task.timeout_seconds
                        )
                    ):
                        raise ValueError("browser_execution_spec_stale")
                    if agent_execution is not None:
                        requirement = agent_execution.spec.worker_requirement
                        if (
                            requirement is None
                            or selection.requested_role != requirement.role
                            or tuple(selection.requested_capabilities)
                            != tuple(requirement.capabilities)
                        ):
                            raise ValueError("browser_worker_requirement_mismatch")
                except ValueError:
                    task.error = "browser_execution_spec_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "supervisor_worker_revalidated",
                    worker_id=selection.descriptor.worker_id,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "browser_endpoint_revalidated",
                    endpoint_id=browser_request.endpoint.endpoint.endpoint_id,
                    endpoint_registry_version=browser_request.endpoint.registry_version,
                    browser_policy_version=browser_request.policy_version,
                )

            computer_request = None
            if task.kind == "computer_run":
                try:
                    if (
                        self.computer_supervisor is None
                        or task.computer_spec is None
                        or agent_execution is None
                    ):
                        raise ValueError("computer_execution_spec_unavailable")
                    computer_request = ComputerExecutionSpec.model_validate(task.computer_spec)
                    selection = computer_request.worker_selection
                    selected = self.computer_supervisor.registry.get(selection.descriptor.worker_id)
                    required_capability = (
                        "computer_navigate"
                        if task.payload["action"] == "browser_navigate"
                        else "computer_read"
                    )
                    if (
                        selected is None
                        or not self.computer_supervisor.matches(selection)
                        or not selected.worker.matches(computer_request)
                        or not selected.worker.request_matches(
                            computer_request, task.payload, task.timeout_seconds
                        )
                        or computer_request.agent_instance_id != agent_execution.agent_instance_id
                        or required_capability not in agent_execution.spec.capabilities
                        or tuple(selection.requested_capabilities) != (required_capability,)
                    ):
                        raise ValueError("computer_execution_spec_stale")
                    self._check_space_approval(
                        session, agent_execution, computer_request, selected.worker
                    )
                except (ValueError, DomainError):
                    task.error = "computer_execution_spec_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "supervisor_worker_revalidated",
                    worker_id=selection.descriptor.worker_id,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "computer_permission_revalidated",
                    agent_instance_id=computer_request.agent_instance_id,
                    action=computer_request.action,
                    permission_version=computer_request.permission_version,
                    backend_identity=computer_request.backend_identity,
                    endpoint_id=(
                        computer_request.endpoint.endpoint.endpoint_id
                        if computer_request.endpoint is not None
                        else None
                    ),
                    endpoint_registry_version=(
                        computer_request.endpoint.registry_version
                        if computer_request.endpoint is not None
                        else None
                    ),
                    egress_policy_version=computer_request.egress_policy_version,
                )

            shell_request = None
            if task.kind == "shell_run":
                try:
                    if self.shell_supervisor is None or task.shell_spec is None:
                        raise ValueError("shell_execution_spec_unavailable")
                    shell_request = ShellExecutionSpec.model_validate(task.shell_spec)
                    selection = shell_request.worker_selection
                    validation = shell_validations.get(selection.descriptor.worker_id)
                    if isinstance(validation, ShellRuntimeError):
                        raise ValueError("shell_execution_spec_invalid")
                    selected = self.shell_supervisor.registry.get(selection.descriptor.worker_id)
                    if (
                        selected is None
                        or not self.shell_supervisor.matches(selection)
                        or not selected.worker.matches_validated(validation, shell_request)
                        or not selected.worker.request_matches(
                            shell_request, task.payload, task.timeout_seconds
                        )
                    ):
                        raise ValueError("shell_execution_spec_stale")
                    if agent_execution is not None:
                        requirement = agent_execution.spec.worker_requirement
                        if (
                            requirement is None
                            or selection.requested_role != requirement.role
                            or tuple(selection.requested_capabilities)
                            != tuple(requirement.capabilities)
                        ):
                            raise ValueError("shell_worker_requirement_mismatch")
                except ValueError:
                    task.error = "shell_execution_spec_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
                audit(
                    session,
                    task,
                    "supervisor_worker_revalidated",
                    worker_id=selection.descriptor.worker_id,
                    registry_version=selection.registry_version,
                    authority_profile=selection.authority_profile,
                )
                audit(
                    session,
                    task,
                    "shell_policy_revalidated",
                    operator_identity=shell_request.operator_identity,
                    shell_policy_version=shell_request.policy_version,
                )

            if task.kind == "tool_call":
                if self.tool_gateway is None:
                    task.error = "tool_gateway_unavailable"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "tool_policy_denied", reason=task.error)
                    return None
                try:
                    tool = self.tool_gateway.registry.get(task.payload["tool"])
                    self.tool_gateway.policy.evaluate(tool)
                except ToolPolicyDenied as error:
                    task.error = error.args[0]
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "tool_policy_denied", tool=task.payload["tool"])
                    return None
            current_hash = action_hash(
                task.kind,
                task.payload,
                task.timeout_seconds,
                task.action_version,
                self._action_selection(task),
            )
            if current_hash != task.action_hash:
                task.error = "action_changed"
                transition(session, task, State.CANCELLED)
                audit(session, task, "execution_denied", reason=task.error)
                return None

            developer_request = None
            promotion_request = None
            merge_request = None
            release_request = None
            if task.kind == "developer_run":
                try:
                    developer_request = DeveloperRequest.model_validate(task.developer_spec)
                except ValueError:
                    task.error = "developer_spec_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
            if task.kind == "promotion_run":
                try:
                    promotion_request = PromotionRequest.model_validate(task.promotion_spec)
                    source = require_task(session, promotion_request.review.developer_task_id)
                    source_attempt = session.get(
                        TaskAttempt, promotion_request.review.developer_attempt_id
                    )
                    latest_attempt_id = session.scalar(
                        select(TaskAttempt.id)
                        .where(TaskAttempt.task_id == source.id)
                        .order_by(TaskAttempt.number.desc())
                        .limit(1)
                    )
                    if (
                        source.kind != "developer_run"
                        or source.status != State.SUCCEEDED
                        or source.developer_spec is None
                        or source_attempt is None
                        or source_attempt.task_id != source.id
                        or source_attempt.id != latest_attempt_id
                        or source_attempt.outcome != State.SUCCEEDED
                        or source_attempt.error is not None
                        or not isinstance(source_attempt.result, dict)
                        or source_attempt.result.get("validation_passed") is not True
                        or source.result != source_attempt.result
                    ):
                        raise ValueError("promotion source changed")
                    developer_request = DeveloperRequest.model_validate(source.developer_spec)
                except (DomainError, ValueError, TypeError, KeyError):
                    task.error = "promotion_source_changed"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
            if task.kind == "merge_run":
                try:
                    merge_request = MergeRequest.model_validate(task.merge_spec)
                    source = require_task(session, merge_request.promotion_task_id)
                    latest = session.scalar(
                        select(TaskAttempt)
                        .where(TaskAttempt.task_id == source.id)
                        .order_by(TaskAttempt.number.desc())
                        .limit(1)
                    )
                    if (
                        source.kind != "promotion_run"
                        or source.status != State.SUCCEEDED
                        or source.action_version != merge_request.promotion_action_version
                        or source.action_hash != merge_request.promotion_action_hash
                        or source.promotion_spec != merge_request.promotion.model_dump(mode="json")
                        or source.result is None
                        or PromotionRunResult.model_validate(source.result).publication
                        != merge_request.publication
                        or latest is None
                        or latest.outcome != State.SUCCEEDED
                        or latest.result != source.result
                    ):
                        raise ValueError("merge source changed")
                except (DomainError, ValueError, TypeError, KeyError):
                    task.error = "merge_source_changed"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
            if task.kind == "release_run":
                try:
                    release_request = ReleaseRequest.model_validate(task.release_spec)
                    source = require_task(session, release_request.merge_task_id)
                    latest = session.scalar(
                        select(TaskAttempt)
                        .where(TaskAttempt.task_id == source.id)
                        .order_by(TaskAttempt.number.desc())
                        .limit(1)
                    )
                    if (
                        source.kind != "merge_run"
                        or source.status != State.SUCCEEDED
                        or source.action_version != release_request.merge_action_version
                        or source.action_hash != release_request.merge_action_hash
                        or source.merge_spec != release_request.merge.model_dump(mode="json")
                        or source.result is None
                        or MergeRunResult.model_validate(source.result)
                        != release_request.merge_result
                        or latest is None
                        or latest.outcome != State.SUCCEEDED
                        or latest.error is not None
                        or latest.result != source.result
                    ):
                        raise ValueError("release source changed")
                except (DomainError, ValueError, TypeError, KeyError):
                    task.error = "release_source_changed"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
            # The claim-time attempt cap is independent of admission count.
            # Validate while the same serialized writer owns the impending
            # TaskAttempt insert. This cannot be bypassed by Task.retry().
            if not youtube_research_attempt_claim_allowed(session, agent_execution):
                task.error = "youtube_research_attempt_quota_exhausted"
                transition(session, task, State.CANCELLED)
                audit(session, task, "execution_denied", reason=task.error)
                return None
            # Company VP, Director, Manager and Specialist share ONE durable
            # per-Project claim-time ceiling, including Task.retry() attempts.
            if not youtube_company_attempt_claim_allowed(session, agent_execution):
                task.error = "youtube_company_attempt_budget_exhausted"
                transition(session, task, State.CANCELLED)
                audit(session, task, "execution_denied", reason=task.error)
                return None
            number = (
                session.scalar(
                    select(func.max(TaskAttempt.number)).where(TaskAttempt.task_id == task.id)
                )
                or 0
            )
            attempt = TaskAttempt(task_id=task.id, number=number + 1, outcome=State.RUNNING)
            session.add(attempt)
            session.flush()
            task.capacity_backoff_until = None
            audit(session, task, "attempt_created", attempt_id=attempt.id, number=attempt.number)
            transition(session, task, State.RUNNING)
            audit(session, task, "execution_started", attempt_id=attempt.id)
            return Execution(
                task.id,
                attempt.id,
                task.kind,
                task.payload,
                task.timeout_seconds,
                task.payload.get("profile"),
                task.selected_alias,
                task.selected_model,
                task.payload.get("tool"),
                task.payload.get("arguments"),
                developer_request,
                research_selection,
                python_selection,
                api_request,
                browser_request,
                promotion_request,
                merge_request,
                release_request,
                agent_execution,
                shell_request=shell_request,
                computer_request=computer_request,
                research_agent_lifecycle=research_agent_lifecycle,
            )

    def research_tool_allowed(self, execution: Execution, tool_name: str) -> bool:
        """Recheck live Core authority at each Research tool use point."""
        if (
            execution.kind != "research_run"
            or execution.research_selection is None
            or not tool_name
        ):
            return False
        with self.database.read() as session:
            task = session.get(Task, execution.task_id)
            attempt = session.get(TaskAttempt, execution.attempt_id)
            if (
                task is None
                or attempt is None
                or attempt.task_id != execution.task_id
                or task.kind != "research_run"
                or task.status != State.RUNNING
                or attempt.outcome != State.RUNNING
                or task.cancel_requested
                or task.research_spec is None
                or self.research_supervisor is None
            ):
                return False
            try:
                selection = WorkerSelectionSnapshot.model_validate(task.research_spec)
                if (
                    selection != execution.research_selection
                    or not self.research_supervisor.matches(selection)
                ):
                    return False
                if task.agent_execution_spec is None:
                    return execution.agent_execution is None
                binding = AgentExecutionBinding.model_validate(task.agent_execution_spec)
                if (
                    binding != execution.agent_execution
                    or tool_name not in binding.spec.allowed_tools
                ):
                    return False
                self._validate_agent_execution(
                    session, binding, campaign_id=_task_campaign_id(session, task)
                )
                delegated = binding.delegation
                lifecycle = (
                    self._agent_lifecycle_sequence(session, binding.agent_instance_id),
                    self._agent_lifecycle_sequence(session, delegated.parent_instance_id)
                    if delegated is not None
                    else None,
                )
                return lifecycle == execution.research_agent_lifecycle
            except (DomainError, ValueError):
                return False

    def finish(
        self,
        execution: Execution,
        outcome: State,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.database.transaction() as session:
            task = require_task(session, execution.task_id)
            attempt = session.get(TaskAttempt, execution.attempt_id)
            if attempt is None or attempt.outcome != State.RUNNING:
                raise DomainError("Attempt is not running")
            if task.cancel_requested:
                outcome, error = State.CANCELLED, "cancelled_by_operator"
                if task.kind != "developer_run":
                    result = None
            elif task.kind == "research_run" and (
                task.agent_execution_spec is not None or execution.agent_execution is not None
            ):
                try:
                    binding = AgentExecutionBinding.model_validate(task.agent_execution_spec)
                    if binding != execution.agent_execution:
                        raise ValueError("Research Agent execution binding changed")
                    self._validate_agent_execution(
                        session, binding, campaign_id=_task_campaign_id(session, task)
                    )
                    delegated = binding.delegation
                    lifecycle = (
                        self._agent_lifecycle_sequence(session, binding.agent_instance_id),
                        self._agent_lifecycle_sequence(session, delegated.parent_instance_id)
                        if delegated is not None
                        else None,
                    )
                    if lifecycle != execution.research_agent_lifecycle:
                        raise ValueError("Research Agent lifecycle changed after claim")
                except (ValueError, DomainError):
                    outcome, error, result = State.CANCELLED, "agent_execution_invalidated", None
            task.capacity_backoff_attempts = 0
            task.capacity_backoff_until = None
            task.result, task.error = result, error
            transition(session, task, outcome)
            attempt.outcome = outcome
            attempt.result, attempt.error = result, error
            attempt.ended_at = utcnow()
            event = {
                State.SUCCEEDED: "execution_completed",
                State.FAILED: "execution_failed",
                State.CANCELLED: "cancellation_completed",
                State.INTERRUPTED: "execution_interrupted",
            }[outcome]
            audit(session, task, event, attempt_id=attempt.id, reason=error)
            if task.kind == "developer_run":
                audit(
                    session,
                    task,
                    "developer_completed" if outcome == State.SUCCEEDED else "developer_failed",
                    attempt_id=attempt.id,
                    reason=error,
                    workspace=result.get("worktree") if result else None,
                )
                self._evaluate_self_improvement_child(
                    session,
                    task,
                    attempt,
                    result,
                )
            if task.kind == "promotion_run":
                details = (
                    {
                        "commit_sha": result["commit"]["commit_sha"],
                        "pr_number": result["publication"]["pr_number"],
                        "head_sha": result["publication"]["pr_head_sha"],
                        "base_sha": result["publication"]["pr_base_sha"],
                    }
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                audit(
                    session,
                    task,
                    "promotion_completed" if outcome == State.SUCCEEDED else "promotion_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "merge_run":
                details = (
                    {
                        "pr_number": result["pr_number"],
                        "head_sha": result["head_sha"],
                        "merge_commit_sha": result["merge_commit_sha"],
                    }
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                audit(
                    session,
                    task,
                    "merge_completed" if outcome == State.SUCCEEDED else "merge_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "release_run":
                details = (
                    {
                        "main_sha": result["main_sha"],
                        "tag": result["tag"],
                        "tag_object_sha": result["tag_object_sha"],
                        "reused": result["reused"],
                    }
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                audit(
                    session,
                    task,
                    "release_tag_created" if outcome == State.SUCCEEDED else "release_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "agent_run":
                details = (
                    {"model_steps": result["model_steps"], "tool_calls": result["tool_calls"]}
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                audit(
                    session,
                    task,
                    "agent_completed" if outcome == State.SUCCEEDED else "agent_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "research_run":
                details = (
                    {
                        "model_steps": result["model_steps"],
                        "tool_calls": result["tool_calls"],
                        "source_count": len(result["sources"]),
                    }
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                audit(
                    session,
                    task,
                    "research_completed" if outcome == State.SUCCEEDED else "research_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "python_run":
                details = {"reason": error}
                if result is not None:
                    details.update(
                        exit_code=result["exit_code"],
                        duration_seconds=result["duration_seconds"],
                    )
                audit(
                    session,
                    task,
                    "python_completed" if outcome == State.SUCCEEDED else "python_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "api_run":
                details = {"reason": error}
                if result is not None:
                    details.update(
                        endpoint_id=result["endpoint_id"],
                        status_code=result["status_code"],
                        response_bytes=result["response_bytes"],
                    )
                audit(
                    session,
                    task,
                    "api_completed" if outcome == State.SUCCEEDED else "api_failed",
                    attempt_id=attempt.id,
                    **details,
                )

            if task.kind == "browser_run":
                details = {"reason": error}
                if result is not None:
                    details.update(
                        endpoint_id=result["endpoint_id"],
                        nodes=result["nodes"],
                        truncated=result["truncated"],
                    )
                audit(
                    session,
                    task,
                    "browser_completed" if outcome == State.SUCCEEDED else "browser_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "computer_run":
                details = {"reason": error}
                if result is not None:
                    details.update(
                        action=result["action"],
                        agent_instance_id=result["agent_instance_id"],
                    )
                    if result.get("action") == "browser_navigate" and isinstance(
                        result.get("browser_navigate"), dict
                    ):
                        details["endpoint_id"] = result["browser_navigate"].get("endpoint_id")
                audit(
                    session,
                    task,
                    "computer_completed" if outcome == State.SUCCEEDED else "computer_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "shell_run":
                details = {"reason": error, "instruction_trust": "none"}
                if result is not None:
                    details.update(
                        exit_code=result["exit_code"],
                        stdout_bytes=len(result["stdout"].encode()),
                        stderr_bytes=len(result["stderr"].encode()),
                        shell_policy_version=result["policy_version"],
                    )
                audit(
                    session,
                    task,
                    "shell_completed" if outcome == State.SUCCEEDED else "shell_failed",
                    attempt_id=attempt.id,
                    **details,
                )
            if task.kind == "self_improvement_run":
                details = (
                    {
                        "base_sha": result["base_sha"],
                        "inspection_fingerprint": result["inspection_fingerprint"],
                        "proposal_fingerprint": result["proposal"]["proposal_fingerprint"],
                        "risk": result["proposal"]["draft"]["risk"],
                    }
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                audit(
                    session,
                    task,
                    (
                        "self_improvement_proposal_created"
                        if outcome == State.SUCCEEDED
                        else "self_improvement_failed"
                    ),
                    attempt_id=attempt.id,
                    **details,
                )

    def defer(self, execution: Execution, *, error: str) -> None:
        with self.database.transaction() as session:
            task = require_task(session, execution.task_id)
            attempt = session.get(TaskAttempt, execution.attempt_id)
            if attempt is None or attempt.outcome != State.RUNNING:
                raise DomainError("Attempt is not running")
            if task.cancel_requested:
                task.capacity_backoff_attempts = 0
                task.capacity_backoff_until = None
                task.error = "cancelled_by_operator"
                task.result = None
                transition(session, task, State.CANCELLED)
                attempt.outcome = State.CANCELLED
                attempt.result = None
                attempt.error = "cancelled_by_operator"
                attempt.ended_at = utcnow()
                audit(session, task, "cancellation_completed", attempt_id=attempt.id)
                if task.kind == "developer_run":
                    audit(
                        session,
                        task,
                        "developer_failed",
                        attempt_id=attempt.id,
                        reason="cancelled_by_operator",
                    )
                return
            task.capacity_backoff_attempts += 1
            delay_seconds = capacity_backoff_seconds(task.capacity_backoff_attempts)
            eligible_at = utcnow() + timedelta(seconds=delay_seconds)
            task.capacity_backoff_until = eligible_at
            defer_transition(session, task)
            task.error = None
            task.result = None
            attempt.outcome = State.INTERRUPTED
            attempt.result = None
            attempt.error = error
            attempt.ended_at = utcnow()
            audit(
                session,
                task,
                "execution_deferred",
                attempt_id=attempt.id,
                reason=error,
                retry_after_seconds=delay_seconds,
                eligible_at=eligible_at.isoformat(),
                consecutive_deferrals=task.capacity_backoff_attempts,
            )
            if task.kind == "developer_run":
                audit(
                    session,
                    task,
                    "developer_deferred",
                    attempt_id=attempt.id,
                    reason=error,
                    retry_after_seconds=delay_seconds,
                    consecutive_deferrals=task.capacity_backoff_attempts,
                )

    @staticmethod
    def _attempt_has_unreleased_capacity(session: Session, attempt_id: str) -> bool:
        claimed = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.attempt_id == attempt_id,
                AuditEvent.event == "worker_capacity_claimed",
            )
        )
        if not claimed:
            return False
        released = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.attempt_id == attempt_id,
                AuditEvent.event == "worker_capacity_released",
            )
        )
        return claimed > (released or 0)

    def recover(self) -> None:
        with self.database.transaction() as session:
            for task in session.scalars(select(Task).where(Task.status == State.RUNNING)):
                attempt = session.scalar(
                    select(TaskAttempt).where(
                        TaskAttempt.task_id == task.id, TaskAttempt.outcome == State.RUNNING
                    )
                )
                task.error = "process_interrupted"
                task.result = None
                transition(session, task, State.INTERRUPTED)
                if attempt:
                    attempt.outcome = State.INTERRUPTED
                    attempt.ended_at = utcnow()
                    attempt.error = "process_interrupted"
                audit(
                    session,
                    task,
                    "startup_interruption_recovery",
                    attempt_id=attempt.id if attempt else None,
                )
                if (
                    task.kind == "developer_run"
                    and attempt is not None
                    and self._attempt_has_unreleased_capacity(session, attempt.id)
                ):
                    descriptor = None
                    if task.developer_spec is not None:
                        try:
                            descriptor = DeveloperRequest.model_validate(
                                task.developer_spec
                            ).worker_selection.descriptor
                        except (ValueError, TypeError, KeyError):
                            descriptor = None
                    audit(
                        session,
                        task,
                        "worker_capacity_released",
                        attempt_id=attempt.id if attempt else None,
                        worker_id=descriptor.worker_id if descriptor else None,
                        recovered=True,
                    )
                if task.kind == "promotion_run":
                    audit(
                        session,
                        task,
                        "promotion_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "merge_run":
                    audit(
                        session,
                        task,
                        "merge_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "release_run":
                    audit(
                        session,
                        task,
                        "release_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "agent_run":
                    audit(
                        session,
                        task,
                        "agent_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "research_run":
                    audit(
                        session,
                        task,
                        "research_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "python_run":
                    audit(
                        session,
                        task,
                        "python_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "api_run":
                    audit(
                        session,
                        task,
                        "api_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )

                if task.kind == "browser_run":
                    audit(
                        session,
                        task,
                        "browser_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "computer_run":
                    audit(
                        session,
                        task,
                        "computer_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "shell_run":
                    audit(
                        session,
                        task,
                        "shell_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "self_improvement_run":
                    audit(
                        session,
                        task,
                        "self_improvement_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
                if task.kind == "developer_run":
                    audit(
                        session,
                        task,
                        "developer_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
