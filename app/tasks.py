"""Transactional task operations. Routes and workers delegate state changes here."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database
from app.developer import DeveloperRequest, DeveloperRuntime, DeveloperRuntimeError
from app.gateway import ModelGateway
from app.models import Approval, AuditEvent, Task, TaskAttempt, utcnow
from app.policy import DomainError, State, action_hash, fingerprint, validate_transition
from app.promotion_contracts import PromotionRequest
from app.promotion_review import PromotionReviewer
from app.schemas import (
    ApprovalResponse,
    AttemptResponse,
    AuditResponse,
    TaskCreate,
    TaskPage,
    TaskResponse,
)
from app.tools import ToolGateway, ToolPolicyDenied, ToolRuntimeError

WAITING = (State.PENDING, State.AWAITING_APPROVAL)


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
    promotion_request: PromotionRequest | None = None


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


def require_task(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise DomainError("Task not found", 404)
    return task


def snapshot(session: Session, task: Task) -> TaskResponse:
    values = {
        "id": task.id,
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
        "promotion_spec": task.promotion_spec,
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
        database: Database,
        settings: Settings,
        gateway: ModelGateway | None = None,
        tool_gateway: ToolGateway | None = None,
        developer_runtime: DeveloperRuntime | None = None,
        promotion_reviewer: PromotionReviewer | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.gateway = gateway
        self.tool_gateway = tool_gateway
        self.developer_runtime = developer_runtime
        self.promotion_reviewer = promotion_reviewer

    def _check_capacity(self, session: Session) -> None:
        count = session.scalar(
            select(func.count()).select_from(Task).where(Task.status.in_(WAITING))
        )
        if count is not None and count >= self.settings.max_waiting_tasks:
            raise DomainError("Waiting task capacity reached; try again later", 503)

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
    ) -> tuple[DeveloperRequest, int, str]:
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
            return developer_request, source.action_version, source.action_hash

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
        developer_request, source_version, source_hash = self._promotion_source(
            payload["developer_task_id"], payload["developer_attempt_id"]
        )
        try:
            review = asyncio.run(
                self.promotion_reviewer.create(
                    payload["developer_task_id"],
                    payload["developer_attempt_id"],
                    developer_request,
                    validation_passed=True,
                )
            )
            promotion = PromotionRequest(
                review=review,
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

    def submit(self, request: TaskCreate, key: str | None) -> tuple[TaskResponse, bool]:
        if request.kind == "promotion_run":
            return self._submit_promotion(request, key)
        request_hash = fingerprint(request.model_dump(mode="json"))
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
            payload = request.payload.model_dump(mode="json")
            task = Task(
                kind=request.kind,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
                status=State.PENDING,
                action_version=1,
                action_hash=action_hash(request.kind, payload, request.timeout_seconds, 1),
                idempotency_key=key,
                request_hash=request_hash,
            )
            if request.kind in {"ai_prompt", "agent_run"}:
                if self.gateway is None:
                    raise DomainError("Model gateway unavailable", 503)
                try:
                    alias, model = self.gateway.select(payload["profile"])
                except Exception:
                    raise DomainError("Invalid model profile", 422) from None
                task.selected_alias, task.selected_model = alias, model
                task.config_version = self.gateway.config_version
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    {
                        "profile": payload["profile"],
                        "alias": alias,
                        "model": model,
                        "config_version": task.config_version,
                    },
                )
            if request.kind == "tool_call":
                if self.tool_gateway is None:
                    raise DomainError("Tool gateway unavailable", 503)
                try:
                    self.tool_gateway.validate(payload["tool"], payload["arguments"])
                except ToolRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
            if request.kind == "developer_run":
                if self.developer_runtime is None:
                    raise DomainError("Developer runtime unavailable", 503)
                try:
                    developer_request = self.developer_runtime.request(
                        payload["instruction"],
                        payload["validation_profile"],
                        request.timeout_seconds,
                    )
                except DeveloperRuntimeError as error:
                    raise DomainError(error.args[0], 422) from None
                task.developer_spec = developer_request.model_dump(mode="json")
                task.action_hash = action_hash(
                    request.kind,
                    payload,
                    request.timeout_seconds,
                    1,
                    task.developer_spec,
                )
            session.add(task)
            session.flush()
            audit(session, task, "task_submitted")
            if task.kind in {"ai_prompt", "agent_run"}:
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
            if task.kind in {"approval_demo", "ai_prompt", "developer_run", "promotion_run"}:
                # Initial gating is explicit; no externally observable ungated pending state.
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

    def model_event(self, task_id: str, event: str, attempt_id: str, **details: Any) -> None:
        with self.database.transaction() as session:
            task = require_task(session, task_id)
            audit(session, task, event, attempt_id=attempt_id, **details)

    def get(self, task_id: str) -> TaskResponse:
        with self.database.read() as session:
            return snapshot(session, require_task(session, task_id))

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
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                    .limit(limit)
                    .offset(offset)
                )
            ]

    def _valid_action(self, task: Task, approval: Approval) -> bool:
        selection = None
        if task.kind in {"ai_prompt", "agent_run"}:
            selection = {
                "alias": task.selected_alias,
                "model": task.selected_model,
                "config_version": task.config_version,
                "profile": task.payload.get("profile"),
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
            except (ValueError, DeveloperRuntimeError):
                return False
        elif task.kind == "promotion_run":
            selection = task.promotion_spec
            try:
                request = PromotionRequest.model_validate(selection)
                payload = task.payload
                if (
                    self.settings.promotion_repository is None
                    or request.repository != self.settings.promotion_repository
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
            except (ValueError, TypeError, KeyError):
                return False
        current = action_hash(
            task.kind, task.payload, task.timeout_seconds, task.action_version, selection
        )
        return (
            approval.action_version == task.action_version
            and approval.action_hash == current == task.action_hash
        )

    def _expire_or_invalidate(self, session: Session, task: Task, approval: Approval) -> bool:
        if approval.status not in {"pending", "approved"} or task.status not in WAITING:
            return False
        reason = None
        if not self._valid_action(task, approval):
            reason = "invalidated"
        elif approval.expires_at <= utcnow():
            reason = "expired"
        if reason:
            approval.status = reason
            approval.decided_at = utcnow()
            task.error = f"approval_{reason}"
            audit(session, task, f"approval_{reason}", approval_id=approval.id)
            transition(session, task, State.CANCELLED)
            return True
        return False

    def expire_approvals(self) -> None:
        with self.database.transaction() as session:
            pairs = session.execute(
                select(Task, Approval)
                .join(Approval, Task.id == Approval.task_id)
                .where(Task.status.in_(WAITING), Approval.status.in_(("pending", "approved")))
            ).all()
            for task, approval in pairs:
                self._expire_or_invalidate(session, task, approval)

    def decide(self, approval_id: str, decision: str) -> ApprovalResponse:
        error = None
        with self.database.transaction() as session:
            approval = session.get(Approval, approval_id)
            if approval is None:
                raise DomainError("Approval not found", 404)
            task = require_task(session, approval.task_id)
            self._expire_or_invalidate(session, task, approval)
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
        with self.database.transaction() as session:
            task = require_task(session, task_id)
            if task.kind == "promotion_run":
                raise DomainError("Promotion retry is not enabled yet", 503)
            target = (
                State.AWAITING_APPROVAL
                if task.kind in {"approval_demo", "ai_prompt", "developer_run"}
                else State.PENDING
            )
            validate_transition(task.status, target)
            self._check_capacity(session)
            audit(session, task, "retry_requested")
            transition(session, task, target)
            task.action_version += 1
            if task.kind == "developer_run":
                if self.developer_runtime is None:
                    raise DomainError("Developer runtime unavailable", 503)
                try:
                    task.developer_spec = self.developer_runtime.request(
                        task.payload["instruction"],
                        task.payload["validation_profile"],
                        task.timeout_seconds,
                    ).model_dump(mode="json")
                except DeveloperRuntimeError as error:
                    raise DomainError(str(error), 422) from None
            task.action_hash = action_hash(
                task.kind,
                task.payload,
                task.timeout_seconds,
                task.action_version,
                {
                    "profile": task.payload.get("profile"),
                    "alias": task.selected_alias,
                    "model": task.selected_model,
                    "config_version": task.config_version,
                }
                if task.kind in {"ai_prompt", "agent_run"}
                else (
                    task.developer_spec
                    if task.kind == "developer_run"
                    else (task.promotion_spec if task.kind == "promotion_run" else None)
                ),
            )
            task.cancel_requested = False
            task.result = None
            task.error = None
            if task.kind in {"approval_demo", "ai_prompt", "developer_run"}:
                if task.kind == "ai_prompt":
                    audit(
                        session,
                        task,
                        "model_selected",
                        alias=task.selected_alias,
                        actual_model=task.selected_model,
                        config_version=task.config_version,
                    )
                self._new_approval(session, task)
            return snapshot(session, task)

    def claim(self) -> Execution | None:
        with self.database.transaction() as session:
            if session.scalar(select(Task.id).where(Task.status == State.RUNNING).limit(1)):
                return None
            task = session.scalar(
                select(Task)
                .where(Task.status == State.PENDING)
                .order_by(Task.created_at, Task.id)
                .limit(1)
            )
            if task is None:
                return None
            if task.kind in {"approval_demo", "ai_prompt", "developer_run", "promotion_run"}:
                approval = session.scalar(
                    select(Approval)
                    .where(Approval.task_id == task.id)
                    .order_by(Approval.created_at.desc())
                    .limit(1)
                )
                if approval and self._expire_or_invalidate(session, task, approval):
                    return None
                if (
                    approval is None
                    or approval.status != "approved"
                    or not self._valid_action(task, approval)
                ):
                    task.error = "approval_missing_or_invalid"
                    transition(session, task, State.CANCELLED)
                    audit(session, task, "execution_denied", reason=task.error)
                    return None
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
                {
                    "alias": task.selected_alias,
                    "model": task.selected_model,
                    "config_version": task.config_version,
                    "profile": task.payload.get("profile"),
                }
                if task.kind in {"ai_prompt", "agent_run"}
                else (
                    task.developer_spec
                    if task.kind == "developer_run"
                    else (task.promotion_spec if task.kind == "promotion_run" else None)
                ),
            )
            if current_hash != task.action_hash:
                task.error = "action_changed"
                transition(session, task, State.CANCELLED)
                audit(session, task, "execution_denied", reason=task.error)
                return None

            developer_request = None
            promotion_request = None
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
            number = (
                session.scalar(
                    select(func.max(TaskAttempt.number)).where(TaskAttempt.task_id == task.id)
                )
                or 0
            )
            attempt = TaskAttempt(task_id=task.id, number=number + 1, outcome=State.RUNNING)
            session.add(attempt)
            session.flush()
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
                promotion_request,
            )

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
            transition(session, task, outcome)
            task.result, task.error = result, error
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
            if task.kind == "promotion_run":
                details = (
                    {
                        "base_sha": result["base_sha"],
                        "tree_sha": result["tree_sha"],
                        "commit_sha": result["commit_sha"],
                        "branch": result["branch"],
                        "review_fingerprint": result["review_fingerprint"],
                    }
                    if outcome == State.SUCCEEDED and result is not None
                    else {"reason": error}
                )
                if outcome == State.SUCCEEDED:
                    audit(
                        session,
                        task,
                        "promotion_commit_created",
                        attempt_id=attempt.id,
                        **details,
                    )
                audit(
                    session,
                    task,
                    "promotion_completed" if outcome == State.SUCCEEDED else "promotion_failed",
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

    def recover(self) -> None:
        with self.database.transaction() as session:
            for task in session.scalars(select(Task).where(Task.status == State.RUNNING)):
                attempt = session.scalar(
                    select(TaskAttempt).where(
                        TaskAttempt.task_id == task.id, TaskAttempt.outcome == State.RUNNING
                    )
                )
                transition(session, task, State.INTERRUPTED)
                task.error = "process_interrupted"
                task.result = None
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
                if task.kind == "promotion_run":
                    audit(
                        session,
                        task,
                        "promotion_failed",
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
                if task.kind == "developer_run":
                    audit(
                        session,
                        task,
                        "developer_failed",
                        attempt_id=attempt.id if attempt else None,
                        reason="process_interrupted",
                    )
