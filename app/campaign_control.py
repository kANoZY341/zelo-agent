"""Core-authoritative v0.12 campaign leases, recovery, and shared budgets."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns import append_campaign_event
from app.storage import SessionStore
from app.models import (
    Campaign,
    CampaignBudgetLimit,
    CampaignBudgetReservation,
    CampaignLease,
    new_id,
    utcnow,
)
from app.policy import DomainError, fingerprint
from app.schemas import (
    CampaignBudgetCommit,
    CampaignBudgetPage,
    CampaignBudgetReservationPage,
    CampaignBudgetReservationResponse,
    CampaignBudgetReserve,
    CampaignBudgetResponse,
    CampaignBudgetSet,
    CampaignLeaseAcquire,
    CampaignLeaseGrantResponse,
    CampaignLeaseRelease,
    CampaignLeaseRenew,
    CampaignLeaseStatusResponse,
    CampaignRecoveryResponse,
)

CONTROL_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


class CampaignControlService:
    def __init__(
        self,
        database: SessionStore,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock

    @staticmethod
    def _require_campaign(session: Session, campaign_id: str) -> Campaign:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise DomainError("Campaign not found", 404)
        return campaign

    @staticmethod
    def _validate_name(value: str, label: str) -> str:
        if CONTROL_NAME.fullmatch(value) is None:
            raise DomainError(
                f"{label} must start with a lowercase letter and contain only "
                "lowercase letters, digits, '.', ':', '_' or '-'",
                422,
            )
        return value

    @staticmethod
    def _ensure_campaign_accepts_new_work(campaign: Campaign) -> None:
        if campaign.status != "active":
            raise DomainError("Campaign is not active", 409)

    @staticmethod
    def _ensure_campaign_budget_mutable(campaign: Campaign) -> None:
        if campaign.status not in {"active", "paused"}:
            raise DomainError("Campaign budget cannot be changed in its current state", 409)

    def _lease_status(
        self,
        lease: CampaignLease,
        *,
        now: datetime | None = None,
    ) -> CampaignLeaseStatusResponse:
        current = now or self.clock()
        if lease.holder_id is None:
            status = "free"
        elif lease.expires_at is not None and lease.expires_at <= current:
            status = "expired"
        else:
            status = "held"
        return CampaignLeaseStatusResponse(
            campaign_id=lease.campaign_id,
            scope=lease.scope,
            generation=lease.generation,
            status=status,
            holder_id=lease.holder_id,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            updated_at=lease.updated_at,
        )

    @staticmethod
    def _lease_grant(lease: CampaignLease) -> CampaignLeaseGrantResponse:
        if (
            lease.holder_id is None
            or lease.lease_token is None
            or lease.acquired_at is None
            or lease.expires_at is None
        ):
            raise DomainError("Lease is not held")
        return CampaignLeaseGrantResponse(
            campaign_id=lease.campaign_id,
            scope=lease.scope,
            generation=lease.generation,
            holder_id=lease.holder_id,
            lease_token=lease.lease_token,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            updated_at=lease.updated_at,
        )

    @staticmethod
    def _clear_lease(
        session: Session,
        lease: CampaignLease,
        now: datetime,
        *,
        event: str,
    ) -> None:
        previous_holder = lease.holder_id
        previous_expiry = lease.expires_at
        lease.holder_id = None
        lease.lease_token = None
        lease.acquired_at = None
        lease.expires_at = None
        lease.updated_at = now
        append_campaign_event(
            session,
            lease.campaign_id,
            event,
            scope=lease.scope,
            generation=lease.generation,
            previous_holder_id=previous_holder,
            previous_expires_at=previous_expiry.isoformat() if previous_expiry else None,
        )

    def acquire_lease(
        self,
        campaign_id: str,
        scope: str,
        body: CampaignLeaseAcquire,
    ) -> CampaignLeaseGrantResponse:
        scope = self._validate_name(scope, "Lease scope")
        with self.database.transaction() as session:
            now = self.clock()
            campaign = self._require_campaign(session, campaign_id)
            self._ensure_campaign_accepts_new_work(campaign)
            lease = session.get(CampaignLease, (campaign.id, scope))
            if lease is None:
                lease = CampaignLease(
                    campaign_id=campaign.id,
                    scope=scope,
                    generation=0,
                    updated_at=now,
                )
                session.add(lease)
                session.flush()

            if lease.holder_id is not None:
                if lease.expires_at is not None and lease.expires_at > now:
                    raise DomainError("Campaign lease is already held", 409)
                self._clear_lease(session, lease, now, event="lease_recovered")
                session.flush()

            lease.generation += 1
            lease.holder_id = body.holder_id
            lease.lease_token = new_id()
            lease.acquired_at = now
            lease.expires_at = now + timedelta(seconds=body.ttl_seconds)
            lease.updated_at = now
            session.flush()
            append_campaign_event(
                session,
                campaign.id,
                "lease_acquired",
                scope=scope,
                generation=lease.generation,
                holder_id=body.holder_id,
                expires_at=lease.expires_at.isoformat(),
            )
            return self._lease_grant(lease)

    @staticmethod
    def _require_lease_proof(
        lease: CampaignLease,
        *,
        holder_id: str,
        lease_token: str,
        generation: int,
    ) -> None:
        if (
            lease.holder_id != holder_id
            or lease.lease_token != lease_token
            or lease.generation != generation
        ):
            raise DomainError("Lease proof does not match the current holder", 409)

    def renew_lease(
        self,
        campaign_id: str,
        scope: str,
        body: CampaignLeaseRenew,
    ) -> CampaignLeaseGrantResponse:
        scope = self._validate_name(scope, "Lease scope")
        expired = False
        with self.database.transaction() as session:
            now = self.clock()
            campaign = self._require_campaign(session, campaign_id)
            self._ensure_campaign_accepts_new_work(campaign)
            lease = session.get(CampaignLease, (campaign.id, scope))
            if lease is None:
                raise DomainError("Campaign lease not found", 404)
            self._require_lease_proof(
                lease,
                holder_id=body.holder_id,
                lease_token=body.lease_token,
                generation=body.generation,
            )
            if lease.expires_at is None or lease.expires_at <= now:
                self._clear_lease(session, lease, now, event="lease_recovered")
                expired = True
            else:
                lease.expires_at = now + timedelta(seconds=body.ttl_seconds)
                lease.updated_at = now
                append_campaign_event(
                    session,
                    campaign.id,
                    "lease_renewed",
                    scope=scope,
                    generation=lease.generation,
                    holder_id=lease.holder_id,
                    expires_at=lease.expires_at.isoformat(),
                )
                return self._lease_grant(lease)
        if expired:
            raise DomainError("Campaign lease expired", 409)
        raise DomainError("Campaign lease renewal failed")

    def release_lease(
        self,
        campaign_id: str,
        scope: str,
        body: CampaignLeaseRelease,
    ) -> CampaignLeaseStatusResponse:
        scope = self._validate_name(scope, "Lease scope")
        with self.database.transaction() as session:
            now = self.clock()
            self._require_campaign(session, campaign_id)
            lease = session.get(CampaignLease, (campaign_id, scope))
            if lease is None:
                raise DomainError("Campaign lease not found", 404)
            self._require_lease_proof(
                lease,
                holder_id=body.holder_id,
                lease_token=body.lease_token,
                generation=body.generation,
            )
            expired = lease.expires_at is None or lease.expires_at <= now
            previous_holder = lease.holder_id
            previous_expiry = lease.expires_at
            lease.holder_id = None
            lease.lease_token = None
            lease.acquired_at = None
            lease.expires_at = None
            lease.updated_at = now
            append_campaign_event(
                session,
                campaign_id,
                "lease_released",
                scope=scope,
                generation=lease.generation,
                holder_id=previous_holder,
                expired=expired,
                previous_expires_at=previous_expiry.isoformat() if previous_expiry else None,
            )
            return self._lease_status(lease, now=now)

    def lease_status(self, campaign_id: str, scope: str) -> CampaignLeaseStatusResponse:
        scope = self._validate_name(scope, "Lease scope")
        with self.database.read() as session:
            self._require_campaign(session, campaign_id)
            lease = session.get(CampaignLease, (campaign_id, scope))
            if lease is None:
                raise DomainError("Campaign lease not found", 404)
            return self._lease_status(lease)

    @staticmethod
    def _usage(
        session: Session,
        campaign_id: str,
        resource: str,
        now: datetime,
    ) -> tuple[int, int]:
        spent = (
            session.scalar(
                select(func.sum(CampaignBudgetReservation.committed_units)).where(
                    CampaignBudgetReservation.campaign_id == campaign_id,
                    CampaignBudgetReservation.resource == resource,
                    CampaignBudgetReservation.status == "committed",
                )
            )
            or 0
        )
        reserved = (
            session.scalar(
                select(func.sum(CampaignBudgetReservation.units)).where(
                    CampaignBudgetReservation.campaign_id == campaign_id,
                    CampaignBudgetReservation.resource == resource,
                    CampaignBudgetReservation.status == "reserved",
                    CampaignBudgetReservation.expires_at > now,
                )
            )
            or 0
        )
        return int(spent), int(reserved)

    def _budget_response(
        self,
        session: Session,
        budget: CampaignBudgetLimit,
        *,
        now: datetime | None = None,
    ) -> CampaignBudgetResponse:
        current = now or self.clock()
        spent, reserved = self._usage(
            session,
            budget.campaign_id,
            budget.resource,
            current,
        )
        available = budget.limit_units - spent - reserved
        return CampaignBudgetResponse(
            campaign_id=budget.campaign_id,
            resource=budget.resource,
            limit_units=budget.limit_units,
            spent_units=spent,
            reserved_units=reserved,
            available_units=max(0, available),
            updated_at=budget.updated_at,
        )

    @staticmethod
    def _reservation_response(
        reservation: CampaignBudgetReservation,
    ) -> CampaignBudgetReservationResponse:
        return CampaignBudgetReservationResponse.model_validate(reservation)

    @staticmethod
    def _expire_reservation(
        session: Session,
        reservation: CampaignBudgetReservation,
        now: datetime,
    ) -> None:
        reservation.status = "expired"
        reservation.committed_units = 0
        reservation.updated_at = now
        append_campaign_event(
            session,
            reservation.campaign_id,
            "budget_reservation_expired",
            reservation_id=reservation.id,
            resource=reservation.resource,
            owner_id=reservation.owner_id,
            units=reservation.units,
        )

    def _recover_expired_budget_in_session(
        self,
        session: Session,
        now: datetime,
        *,
        campaign_id: str | None = None,
        resource: str | None = None,
    ) -> int:
        query = select(CampaignBudgetReservation).where(
            CampaignBudgetReservation.status == "reserved",
            CampaignBudgetReservation.expires_at <= now,
        )
        if campaign_id is not None:
            query = query.where(CampaignBudgetReservation.campaign_id == campaign_id)
        if resource is not None:
            query = query.where(CampaignBudgetReservation.resource == resource)
        reservations = list(session.scalars(query.order_by(CampaignBudgetReservation.id)))
        for reservation in reservations:
            self._expire_reservation(session, reservation, now)
        return len(reservations)

    def _recover_expired_leases_in_session(
        self,
        session: Session,
        now: datetime,
        *,
        campaign_id: str | None = None,
    ) -> int:
        query = select(CampaignLease).where(
            CampaignLease.holder_id.is_not(None),
            CampaignLease.expires_at.is_not(None),
            CampaignLease.expires_at <= now,
        )
        if campaign_id is not None:
            query = query.where(CampaignLease.campaign_id == campaign_id)
        leases = list(
            session.scalars(query.order_by(CampaignLease.campaign_id, CampaignLease.scope))
        )
        for lease in leases:
            self._clear_lease(session, lease, now, event="lease_recovered")
        return len(leases)

    def recover_expired(self, campaign_id: str | None = None) -> CampaignRecoveryResponse:
        with self.database.transaction() as session:
            now = self.clock()
            if campaign_id is not None:
                self._require_campaign(session, campaign_id)
            recovered_leases = self._recover_expired_leases_in_session(
                session,
                now,
                campaign_id=campaign_id,
            )
            expired_reservations = self._recover_expired_budget_in_session(
                session,
                now,
                campaign_id=campaign_id,
            )
            return CampaignRecoveryResponse(
                campaign_id=campaign_id,
                recovered_leases=recovered_leases,
                expired_budget_reservations=expired_reservations,
            )

    def set_budget(
        self,
        campaign_id: str,
        resource: str,
        body: CampaignBudgetSet,
    ) -> CampaignBudgetResponse:
        resource = self._validate_name(resource, "Budget resource")
        with self.database.transaction() as session:
            now = self.clock()
            campaign = self._require_campaign(session, campaign_id)
            self._ensure_campaign_budget_mutable(campaign)
            recovered = self._recover_expired_budget_in_session(
                session,
                now,
                campaign_id=campaign.id,
                resource=resource,
            )
            if recovered:
                session.flush()
            budget = session.get(CampaignBudgetLimit, (campaign.id, resource))
            spent, reserved = self._usage(session, campaign.id, resource, now)
            if body.limit_units < spent + reserved:
                raise DomainError("Budget limit is below current spent and reserved units", 409)
            previous_limit = budget.limit_units if budget is not None else None
            if budget is None:
                budget = CampaignBudgetLimit(
                    campaign_id=campaign.id,
                    resource=resource,
                    limit_units=body.limit_units,
                    created_at=now,
                    updated_at=now,
                )
                session.add(budget)
            else:
                budget.limit_units = body.limit_units
                budget.updated_at = now
            session.flush()
            append_campaign_event(
                session,
                campaign.id,
                "budget_limit_set",
                resource=resource,
                previous_limit_units=previous_limit,
                limit_units=body.limit_units,
            )
            return self._budget_response(session, budget, now=now)

    def get_budget(self, campaign_id: str, resource: str) -> CampaignBudgetResponse:
        resource = self._validate_name(resource, "Budget resource")
        with self.database.read() as session:
            self._require_campaign(session, campaign_id)
            budget = session.get(CampaignBudgetLimit, (campaign_id, resource))
            if budget is None:
                raise DomainError("Campaign budget not configured", 404)
            return self._budget_response(session, budget)

    def list_budgets(
        self,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> CampaignBudgetPage:
        with self.database.read() as session:
            self._require_campaign(session, campaign_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignBudgetLimit)
                    .where(CampaignBudgetLimit.campaign_id == campaign_id)
                )
                or 0
            )
            budgets = list(
                session.scalars(
                    select(CampaignBudgetLimit)
                    .where(CampaignBudgetLimit.campaign_id == campaign_id)
                    .order_by(CampaignBudgetLimit.resource)
                    .offset(offset)
                    .limit(limit)
                )
            )
            now = self.clock()
            return CampaignBudgetPage(
                items=[self._budget_response(session, item, now=now) for item in budgets],
                limit=limit,
                offset=offset,
                total=total,
            )

    def reserve_budget(
        self,
        campaign_id: str,
        body: CampaignBudgetReserve,
    ) -> CampaignBudgetReservationResponse:
        resource = self._validate_name(body.resource, "Budget resource")
        request_hash = fingerprint(
            {
                "resource": resource,
                "owner_id": body.owner_id,
                "units": body.units,
                "ttl_seconds": body.ttl_seconds,
            }
        )
        with self.database.transaction() as session:
            now = self.clock()
            campaign = self._require_campaign(session, campaign_id)
            self._ensure_campaign_accepts_new_work(campaign)
            recovered = self._recover_expired_budget_in_session(
                session,
                now,
                campaign_id=campaign.id,
                resource=resource,
            )
            if recovered:
                session.flush()
            existing = session.scalar(
                select(CampaignBudgetReservation).where(
                    CampaignBudgetReservation.campaign_id == campaign.id,
                    CampaignBudgetReservation.idempotency_key == body.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise DomainError("Budget reservation idempotency conflict", 409)
                return self._reservation_response(existing)

            budget = session.get(CampaignBudgetLimit, (campaign.id, resource))
            if budget is None:
                raise DomainError("Campaign budget not configured", 404)
            spent, reserved = self._usage(session, campaign.id, resource, now)
            if spent + reserved + body.units > budget.limit_units:
                raise DomainError("Campaign budget exceeded", 409)

            reservation = CampaignBudgetReservation(
                id=new_id(),
                campaign_id=campaign.id,
                resource=resource,
                owner_id=body.owner_id,
                idempotency_key=body.idempotency_key,
                request_hash=request_hash,
                units=body.units,
                committed_units=0,
                status="reserved",
                expires_at=now + timedelta(seconds=body.ttl_seconds),
                created_at=now,
                updated_at=now,
            )
            session.add(reservation)
            session.flush()
            append_campaign_event(
                session,
                campaign.id,
                "budget_reserved",
                reservation_id=reservation.id,
                resource=resource,
                owner_id=body.owner_id,
                units=body.units,
                expires_at=reservation.expires_at.isoformat(),
            )
            return self._reservation_response(reservation)

    def commit_budget(
        self,
        campaign_id: str,
        reservation_id: str,
        body: CampaignBudgetCommit,
    ) -> CampaignBudgetReservationResponse:
        expired = False
        with self.database.transaction() as session:
            now = self.clock()
            self._require_campaign(session, campaign_id)
            reservation = session.get(CampaignBudgetReservation, reservation_id)
            if reservation is None or reservation.campaign_id != campaign_id:
                raise DomainError("Campaign budget reservation not found", 404)
            if reservation.status == "committed":
                if reservation.committed_units != body.actual_units:
                    raise DomainError(
                        "Budget reservation already committed with different usage",
                        409,
                    )
                return self._reservation_response(reservation)
            if reservation.status != "reserved":
                raise DomainError(
                    f"Budget reservation is already {reservation.status}",
                    409,
                )
            if reservation.expires_at <= now:
                self._expire_reservation(session, reservation, now)
                expired = True
            else:
                if body.actual_units > reservation.units:
                    raise DomainError("Committed units exceed the reservation", 409)
                reservation.status = "committed"
                reservation.committed_units = body.actual_units
                reservation.updated_at = now
                append_campaign_event(
                    session,
                    campaign_id,
                    "budget_committed",
                    reservation_id=reservation.id,
                    resource=reservation.resource,
                    owner_id=reservation.owner_id,
                    reserved_units=reservation.units,
                    committed_units=body.actual_units,
                )
                return self._reservation_response(reservation)
        if expired:
            raise DomainError("Budget reservation expired", 409)
        raise DomainError("Budget commit failed")

    def release_budget(
        self,
        campaign_id: str,
        reservation_id: str,
    ) -> CampaignBudgetReservationResponse:
        with self.database.transaction() as session:
            now = self.clock()
            self._require_campaign(session, campaign_id)
            reservation = session.get(CampaignBudgetReservation, reservation_id)
            if reservation is None or reservation.campaign_id != campaign_id:
                raise DomainError("Campaign budget reservation not found", 404)
            if reservation.status in {"released", "expired"}:
                return self._reservation_response(reservation)
            if reservation.status == "committed":
                raise DomainError("Committed budget cannot be released", 409)
            if reservation.expires_at <= now:
                self._expire_reservation(session, reservation, now)
                return self._reservation_response(reservation)

            reservation.status = "released"
            reservation.committed_units = 0
            reservation.updated_at = now
            append_campaign_event(
                session,
                campaign_id,
                "budget_released",
                reservation_id=reservation.id,
                resource=reservation.resource,
                owner_id=reservation.owner_id,
                units=reservation.units,
            )
            return self._reservation_response(reservation)

    def list_reservations(
        self,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> CampaignBudgetReservationPage:
        with self.database.read() as session:
            self._require_campaign(session, campaign_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(CampaignBudgetReservation)
                    .where(CampaignBudgetReservation.campaign_id == campaign_id)
                )
                or 0
            )
            reservations = list(
                session.scalars(
                    select(CampaignBudgetReservation)
                    .where(CampaignBudgetReservation.campaign_id == campaign_id)
                    .order_by(CampaignBudgetReservation.created_at, CampaignBudgetReservation.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return CampaignBudgetReservationPage(
                items=[self._reservation_response(item) for item in reservations],
                limit=limit,
                offset=offset,
                total=total,
            )
