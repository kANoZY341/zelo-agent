"""Transactional task operations. Routes and workers delegate state changes here."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database
from app.models import Approval, AuditEvent, Task, TaskAttempt, utcnow
from app.policy import DomainError, State, action_hash, fingerprint, validate_transition
from app.schemas import (
    ApprovalResponse,
    AttemptResponse,
    AuditResponse,
    TaskCreate,
    TaskPage,
    TaskResponse,
)

WAITING = (State.PENDING, State.AWAITING_APPROVAL)


@dataclass(frozen=True)
class Execution:
    task_id: str
    attempt_id: str
    kind: str
    payload: dict[str, Any]
    timeout_seconds: float


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
    response = TaskResponse.model_validate(task)
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
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _check_capacity(self, session: Session) -> None:
        count = session.scalar(
            select(func.count()).select_from(Task).where(Task.status.in_(WAITING))
        )
        if count is not None and count >= self.settings.max_waiting_tasks:
            raise DomainError("Waiting task capacity reached; try again later", 503)

    def _new_approval(self, session: Session, task: Task) -> None:
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

    def submit(self, request: TaskCreate, key: str | None) -> tuple[TaskResponse, bool]:
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
            session.add(task)
            session.flush()
            audit(session, task, "task_submitted")
            if task.kind == "approval_demo":
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
        current = action_hash(task.kind, task.payload, task.timeout_seconds, task.action_version)
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
            target = State.AWAITING_APPROVAL if task.kind == "approval_demo" else State.PENDING
            validate_transition(task.status, target)
            self._check_capacity(session)
            audit(session, task, "retry_requested")
            transition(session, task, target)
            task.action_version += 1
            task.action_hash = action_hash(
                task.kind, task.payload, task.timeout_seconds, task.action_version
            )
            task.cancel_requested = False
            task.result = None
            task.error = None
            if task.kind == "approval_demo":
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
            if task.kind == "approval_demo":
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
            current_hash = action_hash(
                task.kind, task.payload, task.timeout_seconds, task.action_version
            )
            if current_hash != task.action_hash:
                task.error = "action_changed"
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
            return Execution(task.id, attempt.id, task.kind, task.payload, task.timeout_seconds)

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
                outcome, result, error = State.CANCELLED, None, "cancelled_by_operator"
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
