"""Bounded Campaign scheduler and durable Zelo Mind wake execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.campaign_control import CampaignControlService
from app.campaigns import append_campaign_event
from app.config import Settings
from app.database import Database
from app.mind import MindService
from app.models import (
    Campaign,
    CampaignBudgetLimit,
    CampaignBudgetReservation,
    CampaignLease,
    CampaignSchedule,
    CampaignScheduleContinuation,
    CampaignScheduleRun,
    Conversation,
    ConversationTurn,
    Task,
    utcnow,
)
from app.policy import DomainError, fingerprint
from app.schemas import (
    CampaignBudgetCommit,
    CampaignBudgetReservationResponse,
    CampaignBudgetReserve,
    CampaignLeaseAcquire,
    CampaignLeaseRelease,
    CampaignLeaseRenew,
    CampaignScheduleCreate,
    CampaignSchedulePage,
    CampaignScheduleResponse,
    CampaignScheduleRunPage,
    CampaignScheduleRunResponse,
)

logger = logging.getLogger("zelo")

WAKE_BUDGET_RESOURCE = "mind_wakes"
WAKE_LEASE_SCOPE = "mind-wake"
WAKE_BUDGET_UNITS = 1
SCHEDULE_ERROR_CHARS = 256
TERMINAL_TASK_STATES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


class WakeAuthorityUnavailable(RuntimeError):
    """The current logical wake must retry before Mind can run."""


class CampaignScheduler:
    """One deliberately small scheduler over Core-owned Campaign controls."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        controls: CampaignControlService,
        mind: MindService,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.settings = settings
        self.controls = controls
        self.mind = mind
        self.clock = clock
        self.stopping = False
        self.loop_task: asyncio.Task[None] | None = None

    @staticmethod
    def _error_text(error: BaseException) -> str:
        if isinstance(error, DomainError):
            value = error.message
        else:
            value = error.__class__.__name__
        return value[:SCHEDULE_ERROR_CHARS]

    @staticmethod
    def _schedule_response(
        schedule: CampaignSchedule,
        *,
        mode: str = "cadence",
    ) -> CampaignScheduleResponse:
        return CampaignScheduleResponse.model_validate(schedule).model_copy(update={"mode": mode})

    @staticmethod
    def _schedule_mode(session: Session, schedule_id: str) -> str:
        return (
            "continuation"
            if session.get(CampaignScheduleContinuation, schedule_id) is not None
            else "cadence"
        )

    @staticmethod
    def _run_response(run: CampaignScheduleRun) -> CampaignScheduleRunResponse:
        return CampaignScheduleRunResponse.model_validate(run)

    @staticmethod
    def _require_campaign(session: Session, campaign_id: str) -> Campaign:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise DomainError("Campaign not found", 404)
        return campaign

    @staticmethod
    def _require_schedule(session: Session, schedule_id: str) -> CampaignSchedule:
        schedule = session.get(CampaignSchedule, schedule_id)
        if schedule is None:
            raise DomainError("Campaign schedule not found", 404)
        return schedule

    def create(
        self,
        campaign_id: str,
        body: CampaignScheduleCreate,
        idempotency_key: str,
    ) -> CampaignScheduleResponse:
        request_payload = body.model_dump(mode="json")
        if body.mode == "cadence":
            request_payload.pop("mode", None)
        request_hash = fingerprint(request_payload)
        with self.database.transaction() as session:
            now = self.clock()
            campaign = self._require_campaign(session, campaign_id)
            existing = session.scalar(
                select(CampaignSchedule).where(
                    CampaignSchedule.campaign_id == campaign.id,
                    CampaignSchedule.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise DomainError(
                        "Schedule idempotency key was reused with a different request",
                        409,
                    )
                return self._schedule_response(
                    existing,
                    mode=self._schedule_mode(session, existing.id),
                )
            if campaign.status != "active":
                raise DomainError("Campaign is not active", 409)
            if session.get(Conversation, body.conversation_id) is None:
                raise DomainError("Conversation not found", 404)
            if (
                session.get(
                    CampaignBudgetLimit,
                    (campaign.id, WAKE_BUDGET_RESOURCE),
                )
                is None
            ):
                raise DomainError(
                    "Campaign must configure the mind_wakes budget before scheduling",
                    409,
                )
            active_count = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignSchedule)
                    .where(
                        CampaignSchedule.campaign_id == campaign.id,
                        CampaignSchedule.status == "active",
                    )
                )
                or 0
            )
            if active_count >= self.settings.scheduler_max_active_per_campaign:
                raise DomainError("Campaign active schedule capacity reached", 503)

            schedule = CampaignSchedule(
                campaign_id=campaign.id,
                conversation_id=body.conversation_id,
                prompt=body.prompt,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="active",
                next_run_at=body.run_at,
                interval_seconds=body.interval_seconds,
                max_runs=body.max_runs,
                run_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(schedule)
            session.flush()
            if body.mode == "continuation":
                session.add(
                    CampaignScheduleContinuation(
                        schedule_id=schedule.id,
                        created_at=now,
                    )
                )
                session.flush()
            append_campaign_event(
                session,
                campaign.id,
                "schedule_created",
                schedule_id=schedule.id,
                conversation_id=schedule.conversation_id,
                next_run_at=schedule.next_run_at.isoformat(),
                interval_seconds=schedule.interval_seconds,
                max_runs=schedule.max_runs,
                mode=body.mode,
            )
            return self._schedule_response(schedule, mode=body.mode)

    def get(self, schedule_id: str) -> CampaignScheduleResponse:
        with self.database.read() as session:
            schedule = self._require_schedule(session, schedule_id)
            return self._schedule_response(
                schedule,
                mode=self._schedule_mode(session, schedule.id),
            )

    def list_for_campaign(
        self,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> CampaignSchedulePage:
        with self.database.read() as session:
            self._require_campaign(session, campaign_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignSchedule)
                    .where(CampaignSchedule.campaign_id == campaign_id)
                )
                or 0
            )
            schedules = list(
                session.scalars(
                    select(CampaignSchedule)
                    .where(CampaignSchedule.campaign_id == campaign_id)
                    .order_by(CampaignSchedule.created_at, CampaignSchedule.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return CampaignSchedulePage(
                items=[
                    self._schedule_response(
                        item,
                        mode=self._schedule_mode(session, item.id),
                    )
                    for item in schedules
                ],
                limit=limit,
                offset=offset,
                total=total,
            )

    def runs(
        self,
        schedule_id: str,
        limit: int,
        offset: int,
    ) -> CampaignScheduleRunPage:
        with self.database.read() as session:
            self._require_schedule(session, schedule_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignScheduleRun)
                    .where(CampaignScheduleRun.schedule_id == schedule_id)
                )
                or 0
            )
            rows = list(
                session.scalars(
                    select(CampaignScheduleRun)
                    .where(CampaignScheduleRun.schedule_id == schedule_id)
                    .order_by(CampaignScheduleRun.sequence)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return CampaignScheduleRunPage(
                items=[self._run_response(item) for item in rows],
                limit=limit,
                offset=offset,
                total=total,
            )

    def cancel(self, schedule_id: str) -> CampaignScheduleResponse:
        with self.database.transaction() as session:
            now = self.clock()
            schedule = self._require_schedule(session, schedule_id)
            if schedule.status == "cancelled":
                return self._schedule_response(schedule)
            if schedule.status == "completed":
                raise DomainError("Completed schedule cannot be cancelled", 409)
            schedule.status = "cancelled"
            schedule.next_run_at = None
            schedule.updated_at = now
            append_campaign_event(
                session,
                schedule.campaign_id,
                "schedule_cancelled",
                schedule_id=schedule.id,
                run_count=schedule.run_count,
            )
            return self._schedule_response(
                schedule,
                mode=self._schedule_mode(session, schedule.id),
            )

    def _is_due(self, session: Session, schedule: CampaignSchedule, now: datetime) -> bool:
        if (
            schedule.status != "active"
            or schedule.next_run_at is None
            or schedule.run_count >= schedule.max_runs
        ):
            return False

        continuation = session.get(CampaignScheduleContinuation, schedule.id)
        if continuation is None or schedule.run_count == 0:
            return schedule.next_run_at <= now

        latest_run = session.scalar(
            select(CampaignScheduleRun).where(
                CampaignScheduleRun.schedule_id == schedule.id,
                CampaignScheduleRun.sequence == schedule.run_count,
            )
        )
        if latest_run is None or latest_run.status != "succeeded":
            return schedule.next_run_at <= now
        if latest_run.conversation_turn_id is None:
            return False

        turn = session.get(ConversationTurn, latest_run.conversation_turn_id)
        if turn is None or turn.core_task_id is None:
            return False
        task = session.get(Task, turn.core_task_id)
        return task is not None and task.status in TERMINAL_TASK_STATES

    def _next_due_id(self) -> str | None:
        now = self.clock()
        with self.database.read() as session:
            common = (
                CampaignSchedule.status == "active",
                CampaignSchedule.next_run_at.is_not(None),
                CampaignSchedule.run_count < CampaignSchedule.max_runs,
                Campaign.status == "active",
                ~exists().where(
                    CampaignLease.campaign_id == CampaignSchedule.campaign_id,
                    CampaignLease.scope == WAKE_LEASE_SCOPE,
                    CampaignLease.holder_id.is_not(None),
                    CampaignLease.expires_at.is_not(None),
                    CampaignLease.expires_at > now,
                ),
            )
            clock_due = session.scalars(
                select(CampaignSchedule)
                .join(Campaign, Campaign.id == CampaignSchedule.campaign_id)
                .where(*common, CampaignSchedule.next_run_at <= now)
                .order_by(CampaignSchedule.next_run_at, CampaignSchedule.id)
            )
            for schedule in clock_due:
                if self._is_due(session, schedule, now):
                    return schedule.id

            continuation_candidates = session.scalars(
                select(CampaignSchedule)
                .join(Campaign, Campaign.id == CampaignSchedule.campaign_id)
                .join(
                    CampaignScheduleContinuation,
                    CampaignScheduleContinuation.schedule_id == CampaignSchedule.id,
                )
                .where(*common, CampaignSchedule.next_run_at > now)
                .order_by(CampaignSchedule.updated_at, CampaignSchedule.id)
            )
            for schedule in continuation_candidates:
                if self._is_due(session, schedule, now):
                    return schedule.id
            return None

    def _execution_snapshot(
        self,
        schedule_id: str,
    ) -> tuple[str, str, str, int] | None:
        with self.database.read() as session:
            schedule = session.get(CampaignSchedule, schedule_id)
            if schedule is None or not self._is_due(session, schedule, self.clock()):
                return None
            campaign = session.get(Campaign, schedule.campaign_id)
            if campaign is None or campaign.status != "active":
                return None
            return (
                schedule.campaign_id,
                schedule.conversation_id,
                schedule.prompt,
                schedule.run_count + 1,
            )

    def _begin_run(
        self,
        schedule_id: str,
        *,
        holder_id: str,
        lease_token: str,
        lease_generation: int,
    ) -> CampaignScheduleRun:
        with self.database.transaction() as session:
            now = self.clock()
            schedule = self._require_schedule(session, schedule_id)
            if not self._is_due(session, schedule, now):
                raise DomainError("Campaign schedule is no longer due", 409)
            sequence = schedule.run_count + 1
            run = session.scalar(
                select(CampaignScheduleRun).where(
                    CampaignScheduleRun.schedule_id == schedule.id,
                    CampaignScheduleRun.sequence == sequence,
                )
            )
            if run is None:
                run = CampaignScheduleRun(
                    schedule_id=schedule.id,
                    sequence=sequence,
                    status="running",
                    lease_holder_id=holder_id,
                    lease_token=lease_token,
                    lease_generation=lease_generation,
                    budget_reservation_id=None,
                    conversation_turn_id=None,
                    started_at=now,
                    completed_at=None,
                    error=None,
                )
                session.add(run)
            elif run.status == "interrupted":
                preserved_budget_id = None
                if run.budget_reservation_id is not None:
                    existing_budget = session.get(
                        CampaignBudgetReservation,
                        run.budget_reservation_id,
                    )
                    if (
                        existing_budget is not None
                        and existing_budget.status == "committed"
                        and existing_budget.committed_units == WAKE_BUDGET_UNITS
                    ):
                        preserved_budget_id = existing_budget.id
                run.status = "running"
                run.lease_holder_id = holder_id
                run.lease_token = lease_token
                run.lease_generation = lease_generation
                run.budget_reservation_id = preserved_budget_id
                run.started_at = now
                run.completed_at = None
                run.error = None
            else:
                raise DomainError("Campaign schedule occurrence is already owned", 409)
            session.flush()
            append_campaign_event(
                session,
                schedule.campaign_id,
                "schedule_wake_started",
                schedule_id=schedule.id,
                schedule_run_id=run.id,
                sequence=sequence,
                lease_generation=lease_generation,
            )
            session.expunge(run)
            return run

    def _bound_budget(
        self,
        run_id: str,
    ) -> CampaignBudgetReservationResponse | None:
        with self.database.read() as session:
            run = session.get(CampaignScheduleRun, run_id)
            if run is None:
                raise DomainError("Campaign schedule run not found", 404)
            if run.budget_reservation_id is None:
                return None
            reservation = session.get(
                CampaignBudgetReservation,
                run.budget_reservation_id,
            )
            if reservation is None:
                raise DomainError("Campaign schedule budget binding is invalid", 500)
            return CampaignBudgetReservationResponse.model_validate(reservation)

    def _bind_budget(self, run_id: str, reservation_id: str) -> None:
        with self.database.transaction() as session:
            run = session.get(CampaignScheduleRun, run_id)
            if run is None or run.status != "running":
                raise DomainError("Campaign schedule run is not active", 409)
            if run.budget_reservation_id not in {None, reservation_id}:
                raise DomainError("Campaign schedule budget binding changed", 409)
            run.budget_reservation_id = reservation_id

    def _require_live_mind_authority(
        self,
        run_id: str,
        *,
        allow_committed_budget: bool = False,
    ) -> None:
        with self.database.transaction() as session:
            now = self.clock()
            minimum_expiry = now + timedelta(seconds=self.settings.scheduler_mind_window_seconds)
            run = session.get(CampaignScheduleRun, run_id)
            if run is None or run.status != "running":
                raise WakeAuthorityUnavailable("wake_run_not_active")
            schedule = self._require_schedule(session, run.schedule_id)
            if schedule.status != "active":
                raise WakeAuthorityUnavailable("schedule_not_active")

            lease = session.get(
                CampaignLease,
                (schedule.campaign_id, WAKE_LEASE_SCOPE),
            )
            if (
                lease is None
                or lease.holder_id != run.lease_holder_id
                or lease.lease_token != run.lease_token
                or lease.generation != run.lease_generation
                or lease.expires_at is None
                or lease.expires_at < minimum_expiry
            ):
                raise WakeAuthorityUnavailable("wake_lease_window_unavailable")

            if run.budget_reservation_id is None:
                raise WakeAuthorityUnavailable("wake_budget_not_bound")
            reservation = session.get(
                CampaignBudgetReservation,
                run.budget_reservation_id,
            )
            if (
                reservation is None
                or reservation.campaign_id != schedule.campaign_id
                or reservation.resource != WAKE_BUDGET_RESOURCE
                or reservation.owner_id != run.lease_holder_id
                or reservation.units != WAKE_BUDGET_UNITS
            ):
                raise WakeAuthorityUnavailable("wake_budget_window_unavailable")
            reserved_window = (
                reservation.status == "reserved"
                and reservation.committed_units == 0
                and reservation.expires_at >= minimum_expiry
            )
            committed_reconciliation = (
                allow_committed_budget
                and reservation.status == "committed"
                and reservation.committed_units == WAKE_BUDGET_UNITS
            )
            if not (reserved_window or committed_reconciliation):
                raise WakeAuthorityUnavailable("wake_budget_window_unavailable")

    def _finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        conversation_turn_id: str | None,
        error: str | None,
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise DomainError("Invalid Campaign schedule run outcome", 422)
        with self.database.transaction() as session:
            now = self.clock()
            run = session.get(CampaignScheduleRun, run_id)
            if run is None:
                raise DomainError("Campaign schedule run not found", 404)
            if run.status != "running":
                if run.status == status and run.conversation_turn_id == conversation_turn_id:
                    return
                raise DomainError("Campaign schedule run is no longer active", 409)

            schedule = self._require_schedule(session, run.schedule_id)
            if run.sequence != schedule.run_count + 1:
                raise DomainError("Campaign schedule run sequence changed", 409)

            if status == "succeeded":
                lease = session.get(
                    CampaignLease,
                    (schedule.campaign_id, WAKE_LEASE_SCOPE),
                )
                if (
                    lease is None
                    or lease.holder_id != run.lease_holder_id
                    or lease.lease_token != run.lease_token
                    or lease.generation != run.lease_generation
                    or lease.expires_at is None
                    or lease.expires_at <= now
                ):
                    raise WakeAuthorityUnavailable("wake_lease_finalization_stale")
                if run.budget_reservation_id is None:
                    raise DomainError("Successful wake is missing its budget reservation", 500)
                reservation = session.get(
                    CampaignBudgetReservation,
                    run.budget_reservation_id,
                )
                if (
                    reservation is None
                    or reservation.campaign_id != schedule.campaign_id
                    or reservation.resource != WAKE_BUDGET_RESOURCE
                    or reservation.owner_id != run.lease_holder_id
                    or reservation.units != WAKE_BUDGET_UNITS
                ):
                    raise DomainError("Wake budget reservation binding is invalid", 500)
                if reservation.status == "reserved":
                    if reservation.expires_at <= now:
                        raise WakeAuthorityUnavailable("wake_budget_finalization_expired")
                    reservation.status = "committed"
                    reservation.committed_units = WAKE_BUDGET_UNITS
                    reservation.updated_at = now
                    session.flush([reservation])
                elif not (
                    reservation.status == "committed"
                    and reservation.committed_units == WAKE_BUDGET_UNITS
                ):
                    raise WakeAuthorityUnavailable("wake_budget_finalization_stale")
                append_campaign_event(
                    session,
                    schedule.campaign_id,
                    "budget_reconciled",
                    reservation_id=reservation.id,
                    resource=reservation.resource,
                    owner_id=reservation.owner_id,
                    reserved_units=reservation.units,
                    committed_units=WAKE_BUDGET_UNITS,
                )

            run.status = status
            run.conversation_turn_id = conversation_turn_id
            run.completed_at = now
            run.error = error

            continuation_idle = False
            if (
                status == "succeeded"
                and session.get(CampaignScheduleContinuation, schedule.id) is not None
                and conversation_turn_id is not None
            ):
                turn = session.get(ConversationTurn, conversation_turn_id)
                if turn is None:
                    raise DomainError("Successful continuation wake is missing its turn", 500)
                continuation_idle = turn.core_task_id is None

            if schedule.status == "active":
                schedule.run_count += 1
                schedule.last_run_at = now
                schedule.last_error = error
                schedule.updated_at = now
                if schedule.run_count >= schedule.max_runs or continuation_idle:
                    schedule.status = "completed"
                    schedule.next_run_at = None
                else:
                    if schedule.interval_seconds is None:
                        raise DomainError("Recurring Campaign schedule cadence is unavailable", 500)
                    schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)

                if continuation_idle:
                    append_campaign_event(
                        session,
                        schedule.campaign_id,
                        "schedule_continuation_completed",
                        schedule_id=schedule.id,
                        schedule_run_id=run.id,
                        sequence=run.sequence,
                        reason="mind_reply_without_core_task",
                    )
            elif schedule.status != "cancelled":
                raise DomainError("Campaign schedule cannot finalize a wake in this state", 409)

            append_campaign_event(
                session,
                schedule.campaign_id,
                "schedule_wake_finished",
                schedule_id=schedule.id,
                schedule_run_id=run.id,
                sequence=run.sequence,
                status=status,
                conversation_turn_id=conversation_turn_id,
                error=error,
                next_run_at=(
                    schedule.next_run_at.isoformat() if schedule.next_run_at is not None else None
                ),
            )

    def _interrupt_run(self, run_id: str, reason: str) -> None:
        with self.database.transaction() as session:
            now = self.clock()
            run = session.get(CampaignScheduleRun, run_id)
            if run is None or run.status != "running":
                return
            schedule = self._require_schedule(session, run.schedule_id)
            run.status = "interrupted"
            run.completed_at = now
            run.error = reason[:SCHEDULE_ERROR_CHARS]
            if schedule.status == "active":
                schedule.last_error = run.error
                schedule.updated_at = now
                schedule.next_run_at = now
            append_campaign_event(
                session,
                schedule.campaign_id,
                "schedule_wake_interrupted",
                schedule_id=schedule.id,
                schedule_run_id=run.id,
                sequence=run.sequence,
                reason=run.error,
            )

    def _running_runs(self) -> list[tuple[str, str, str, int, str | None]]:
        with self.database.read() as session:
            rows = session.execute(
                select(
                    CampaignScheduleRun.id,
                    CampaignSchedule.campaign_id,
                    CampaignScheduleRun.lease_token,
                    CampaignScheduleRun.lease_generation,
                    CampaignScheduleRun.budget_reservation_id,
                )
                .join(
                    CampaignSchedule,
                    CampaignSchedule.id == CampaignScheduleRun.schedule_id,
                )
                .where(CampaignScheduleRun.status == "running")
                .order_by(CampaignScheduleRun.started_at, CampaignScheduleRun.id)
            ).all()
            return [
                (
                    run_id,
                    campaign_id,
                    lease_token,
                    lease_generation,
                    budget_reservation_id,
                )
                for (
                    run_id,
                    campaign_id,
                    lease_token,
                    lease_generation,
                    budget_reservation_id,
                ) in rows
            ]

    def _reconcile_restart_budget(self, run_id: str) -> bool:
        with self.database.transaction() as session:
            now = self.clock()
            run = session.get(CampaignScheduleRun, run_id)
            if run is None:
                raise DomainError("Campaign schedule run not found", 404)
            schedule = self._require_schedule(session, run.schedule_id)
            turn_key = f"schedule:{schedule.id}:{run.sequence}"
            persisted_turn_id = session.scalar(
                select(ConversationTurn.id).where(
                    ConversationTurn.conversation_id == schedule.conversation_id,
                    ConversationTurn.idempotency_key == turn_key,
                )
            )
            reservation = (
                session.get(CampaignBudgetReservation, run.budget_reservation_id)
                if run.budget_reservation_id is not None
                else None
            )

            if persisted_turn_id is None:
                if reservation is not None and reservation.status == "committed":
                    raise DomainError(
                        "Interrupted wake has committed budget without a persisted turn",
                        500,
                    )
                return False

            if reservation is None:
                raise DomainError(
                    "Persisted scheduled turn is missing its wake budget reservation",
                    500,
                )
            if (
                reservation.campaign_id != schedule.campaign_id
                or reservation.resource != WAKE_BUDGET_RESOURCE
                or reservation.owner_id != run.lease_holder_id
                or reservation.units != WAKE_BUDGET_UNITS
            ):
                raise DomainError("Persisted wake budget binding is invalid", 500)

            if reservation.status == "reserved":
                reservation.status = "committed"
                reservation.committed_units = WAKE_BUDGET_UNITS
                reservation.updated_at = now
                append_campaign_event(
                    session,
                    schedule.campaign_id,
                    "budget_reconciled",
                    reservation_id=reservation.id,
                    resource=reservation.resource,
                    owner_id=reservation.owner_id,
                    reserved_units=reservation.units,
                    committed_units=WAKE_BUDGET_UNITS,
                    reason="service_restart",
                )
            elif not (
                reservation.status == "committed"
                and reservation.committed_units == WAKE_BUDGET_UNITS
            ):
                raise DomainError(
                    "Persisted scheduled turn has unrecoverable wake accounting",
                    500,
                )
            return True

    def recover(self) -> int:
        """Interrupt unfinished process-local wakes and free their control state."""

        rows = self._running_runs()
        for run_id, campaign_id, lease_token, lease_generation, reservation_id in rows:
            charge_committed = self._reconcile_restart_budget(run_id)
            if reservation_id is not None and not charge_committed:
                try:
                    self.controls.release_budget(campaign_id, reservation_id)
                except DomainError:
                    pass
            try:
                run = self.get_run(run_id)
                self.controls.release_lease(
                    campaign_id,
                    WAKE_LEASE_SCOPE,
                    CampaignLeaseRelease(
                        holder_id=run.lease_holder_id,
                        lease_token=lease_token,
                        generation=lease_generation,
                    ),
                )
            except DomainError:
                pass
            self._interrupt_run(run_id, "service_restart")
        return len(rows)

    def get_run(self, run_id: str) -> CampaignScheduleRunResponse:
        with self.database.read() as session:
            run = session.get(CampaignScheduleRun, run_id)
            if run is None:
                raise DomainError("Campaign schedule run not found", 404)
            return self._run_response(run)

    def _persisted_turn_id(
        self,
        conversation_id: str,
        idempotency_key: str,
    ) -> str | None:
        with self.database.read() as session:
            return session.scalar(
                select(ConversationTurn.id).where(
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.idempotency_key == idempotency_key,
                )
            )

    async def _thread_mutation(
        self,
        operation: Callable[..., object],
        *args: object,
        remember: Callable[[object], None] | None = None,
        **kwargs: object,
    ) -> object:
        pending = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            result = await asyncio.shield(pending)
        except asyncio.CancelledError as cancellation:
            try:
                result = await pending
            except Exception as error:
                raise cancellation from error
            if remember is not None:
                remember(result)
            raise cancellation
        if remember is not None:
            remember(result)
        return result

    async def _execute_due(self, schedule_id: str) -> None:
        snapshot = self._execution_snapshot(schedule_id)
        if snapshot is None:
            return
        campaign_id, conversation_id, prompt, sequence = snapshot
        holder_id = f"schedule:{schedule_id}"
        turn_key = f"schedule:{schedule_id}:{sequence}"
        lease = None
        run = None
        reservation = None
        budget_committed = False

        def remember_lease(value: object) -> None:
            nonlocal lease
            lease = value

        def remember_run(value: object) -> None:
            nonlocal run
            run = value

        def remember_reservation(value: object) -> None:
            nonlocal reservation
            reservation = value

        try:
            lease = await self._thread_mutation(
                self.controls.acquire_lease,
                campaign_id,
                WAKE_LEASE_SCOPE,
                CampaignLeaseAcquire(
                    holder_id=holder_id,
                    ttl_seconds=self.settings.scheduler_wake_ttl_seconds,
                ),
                remember=remember_lease,
            )
            run = await self._thread_mutation(
                self._begin_run,
                schedule_id,
                holder_id=holder_id,
                lease_token=lease.lease_token,
                lease_generation=lease.generation,
                remember=remember_run,
            )
            if run.sequence != sequence:
                raise DomainError("Campaign schedule occurrence changed", 409)

            reservation = await asyncio.to_thread(self._bound_budget, run.id)
            persisted_turn_id = await asyncio.to_thread(
                self._persisted_turn_id,
                conversation_id,
                turn_key,
            )
            if reservation is not None:
                budget_committed = (
                    reservation.status == "committed"
                    and reservation.committed_units == WAKE_BUDGET_UNITS
                )
                if not budget_committed or persisted_turn_id is None:
                    raise DomainError("Interrupted wake accounting is inconsistent", 500)
            else:
                reservation = await self._thread_mutation(
                    self.controls.reserve_budget,
                    campaign_id,
                    CampaignBudgetReserve(
                        resource=WAKE_BUDGET_RESOURCE,
                        owner_id=holder_id,
                        idempotency_key=(
                            f"schedule:{schedule_id}:{run.sequence}:lease:{lease.generation}"
                        ),
                        units=WAKE_BUDGET_UNITS,
                        ttl_seconds=self.settings.scheduler_wake_ttl_seconds,
                    ),
                    remember=remember_reservation,
                )
                await self._thread_mutation(self._bind_budget, run.id, reservation.id)

            try:
                lease = await self._thread_mutation(
                    self.controls.renew_lease,
                    campaign_id,
                    WAKE_LEASE_SCOPE,
                    CampaignLeaseRenew(
                        holder_id=holder_id,
                        lease_token=lease.lease_token,
                        generation=lease.generation,
                        ttl_seconds=self.settings.scheduler_wake_ttl_seconds,
                    ),
                    remember=remember_lease,
                )
            except DomainError as error:
                raise WakeAuthorityUnavailable("wake_lease_renewal_failed") from error
            await asyncio.to_thread(
                self._require_live_mind_authority,
                run.id,
                allow_committed_budget=budget_committed,
            )

            async def require_authority_after_admission() -> None:
                await asyncio.to_thread(
                    self._require_live_mind_authority,
                    run.id,
                    allow_committed_budget=budget_committed,
                )

            async def commit_budget_after_persist(_: ConversationTurn) -> None:
                nonlocal budget_committed
                if reservation is None:
                    raise DomainError("Wake budget reservation is unavailable", 500)
                await self._thread_mutation(
                    self.controls.commit_budget,
                    campaign_id,
                    reservation.id,
                    CampaignBudgetCommit(actual_units=WAKE_BUDGET_UNITS),
                    remember=remember_reservation,
                )
                budget_committed = True

            turn = await self.mind.send_turn(
                conversation_id,
                prompt,
                turn_key,
                campaign_id=campaign_id,
                before_side_effect=require_authority_after_admission,
                after_persist=commit_budget_after_persist,
            )
            await self._thread_mutation(
                self._finalize_run,
                run.id,
                status="succeeded",
                conversation_turn_id=turn.id,
                error=None,
            )
        except WakeAuthorityUnavailable as error:
            if reservation is not None and not budget_committed:
                try:
                    await asyncio.to_thread(
                        self.controls.release_budget,
                        campaign_id,
                        reservation.id,
                    )
                except DomainError:
                    pass
            if run is not None:
                await asyncio.to_thread(
                    self._interrupt_run,
                    run.id,
                    str(error),
                )
        except asyncio.CancelledError:
            if run is not None:
                await asyncio.to_thread(self._interrupt_run, run.id, "service_shutdown")
            if reservation is not None and not budget_committed:
                try:
                    await asyncio.to_thread(
                        self.controls.release_budget,
                        campaign_id,
                        reservation.id,
                    )
                except DomainError:
                    pass
            raise
        except DomainError as error:
            persisted_turn_id = None
            if run is not None:
                persisted_turn_id = await asyncio.to_thread(
                    self._persisted_turn_id,
                    conversation_id,
                    turn_key,
                )
            if reservation is not None and not budget_committed:
                try:
                    await asyncio.to_thread(
                        self.controls.release_budget,
                        campaign_id,
                        reservation.id,
                    )
                except DomainError:
                    pass
            if run is not None:
                if persisted_turn_id is not None:
                    await asyncio.to_thread(
                        self._interrupt_run,
                        run.id,
                        "wake_post_persist_failure",
                    )
                else:
                    await asyncio.to_thread(
                        self._finalize_run,
                        run.id,
                        status="failed",
                        conversation_turn_id=None,
                        error=self._error_text(error),
                    )
        except Exception as error:
            logger.exception("campaign_schedule_wake_failed")
            persisted_turn_id = None
            if run is not None:
                persisted_turn_id = await asyncio.to_thread(
                    self._persisted_turn_id,
                    conversation_id,
                    turn_key,
                )
            if reservation is not None and not budget_committed:
                try:
                    await asyncio.to_thread(
                        self.controls.release_budget,
                        campaign_id,
                        reservation.id,
                    )
                except DomainError:
                    pass
            if run is not None:
                if persisted_turn_id is not None:
                    await asyncio.to_thread(
                        self._interrupt_run,
                        run.id,
                        "wake_post_persist_failure",
                    )
                else:
                    await asyncio.to_thread(
                        self._finalize_run,
                        run.id,
                        status="failed",
                        conversation_turn_id=None,
                        error=self._error_text(error),
                    )
        finally:
            if lease is not None:
                try:
                    await asyncio.to_thread(
                        self.controls.release_lease,
                        campaign_id,
                        WAKE_LEASE_SCOPE,
                        CampaignLeaseRelease(
                            holder_id=holder_id,
                            lease_token=lease.lease_token,
                            generation=lease.generation,
                        ),
                    )
                except DomainError:
                    pass

    async def _loop(self) -> None:
        try:
            while not self.stopping:
                schedule_id = await asyncio.to_thread(self._next_due_id)
                if schedule_id is not None:
                    await self._execute_due(schedule_id)
                await asyncio.sleep(self.settings.scheduler_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("campaign_scheduler_failed")
            raise

    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        self.loop_task = asyncio.create_task(self._loop(), name="zelo-campaign-scheduler")

    @property
    def ready(self) -> bool:
        if not self.settings.scheduler_enabled:
            return True
        return self.loop_task is not None and not self.loop_task.done() and not self.stopping

    async def stop(self) -> None:
        self.stopping = True
        if self.loop_task is None:
            return
        self.loop_task.cancel()
        try:
            await self.loop_task
        except asyncio.CancelledError:
            pass
        self.loop_task = None
