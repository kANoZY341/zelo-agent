"""v0.12 Campaign lease, recovery, and shared-budget tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.campaign_control import CampaignControlService
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import (
    CampaignBudgetCommit,
    CampaignBudgetReserve,
    CampaignBudgetSet,
    CampaignCreate,
    CampaignLeaseAcquire,
    CampaignLeaseRelease,
    CampaignLeaseRenew,
    GoalCreate,
    GoalSpec,
    ProjectCreate,
)
from app.campaigns import CampaignService
from tests.conftest import AUTH


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def campaign_fixture(database):
    projects = ProjectService(database)
    project = projects.create(ProjectCreate(name="Zelo", summary="Persistent personal AI."))
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Controlled campaign",
            spec=GoalSpec(
                objective="Run bounded work under Core authority.",
                success_criteria=["Leases fence execution and budgets cannot overspend."],
                constraints=["No scheduler yet."],
            ),
        ),
    )
    campaign = CampaignService(database).create(
        goal.id,
        CampaignCreate(title="Controlled execution", summary="Lease and budget proof."),
    )
    return campaign


def test_campaign_lease_fences_holders_and_advances_generation(database):
    campaign = campaign_fixture(database)
    clock = MutableClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC))
    controls = CampaignControlService(database, clock=clock)

    first = controls.acquire_lease(
        campaign.id,
        "execution",
        CampaignLeaseAcquire(holder_id="scheduler-a", ttl_seconds=60),
    )
    assert first.generation == 1

    with pytest.raises(DomainError, match="already held"):
        controls.acquire_lease(
            campaign.id,
            "execution",
            CampaignLeaseAcquire(holder_id="scheduler-b", ttl_seconds=60),
        )

    with pytest.raises(DomainError, match="proof"):
        controls.renew_lease(
            campaign.id,
            "execution",
            CampaignLeaseRenew(
                holder_id="scheduler-a",
                lease_token=first.lease_token,
                generation=2,
                ttl_seconds=120,
            ),
        )

    clock.advance(seconds=10)
    renewed = controls.renew_lease(
        campaign.id,
        "execution",
        CampaignLeaseRenew(
            holder_id="scheduler-a",
            lease_token=first.lease_token,
            generation=1,
            ttl_seconds=120,
        ),
    )
    assert renewed.generation == 1
    assert renewed.lease_token == first.lease_token
    assert renewed.expires_at > first.expires_at

    released = controls.release_lease(
        campaign.id,
        "execution",
        CampaignLeaseRelease(
            holder_id="scheduler-a",
            lease_token=first.lease_token,
            generation=1,
        ),
    )
    assert released.status == "free"
    assert released.generation == 1

    second = controls.acquire_lease(
        campaign.id,
        "execution",
        CampaignLeaseAcquire(holder_id="scheduler-b", ttl_seconds=60),
    )
    assert second.generation == 2
    assert second.lease_token != first.lease_token


def test_campaign_recovery_expires_lease_and_budget_reservation(database):
    campaign = campaign_fixture(database)
    clock = MutableClock(datetime(2026, 9, 26, 11, 0, tzinfo=UTC))
    controls = CampaignControlService(database, clock=clock)

    controls.acquire_lease(
        campaign.id,
        "execution",
        CampaignLeaseAcquire(holder_id="scheduler-a", ttl_seconds=10),
    )
    controls.set_budget(
        campaign.id,
        "model_tokens",
        CampaignBudgetSet(limit_units=100),
    )
    reservation = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="model_tokens",
            owner_id="wake-1",
            idempotency_key="wake-1:model",
            units=60,
            ttl_seconds=10,
        ),
    )

    clock.advance(seconds=11)
    assert controls.lease_status(campaign.id, "execution").status == "expired"
    assert controls.get_budget(campaign.id, "model_tokens").reserved_units == 0

    restarted = CampaignControlService(database, clock=clock)
    recovered = restarted.recover_expired(campaign.id)
    assert recovered.recovered_leases == 1
    assert recovered.expired_budget_reservations == 1
    assert restarted.lease_status(campaign.id, "execution").status == "free"

    reservations = restarted.list_reservations(campaign.id, 100, 0)
    recovered_reservation = next(item for item in reservations.items if item.id == reservation.id)
    assert recovered_reservation.status == "expired"

    event_names = [
        event.event for event in CampaignService(database).events(campaign.id, 100, 0).items
    ]
    assert "lease_recovered" in event_names
    assert "budget_reservation_expired" in event_names


def test_campaign_budget_reserve_commit_release_and_idempotency(database):
    campaign = campaign_fixture(database)
    controls = CampaignControlService(database)

    budget = controls.set_budget(
        campaign.id,
        "model_tokens",
        CampaignBudgetSet(limit_units=100),
    )
    assert budget.available_units == 100

    first = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="model_tokens",
            owner_id="wake-1",
            idempotency_key="wake-1:model",
            units=60,
            ttl_seconds=3600,
        ),
    )
    duplicate = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="model_tokens",
            owner_id="wake-1",
            idempotency_key="wake-1:model",
            units=60,
            ttl_seconds=3600,
        ),
    )
    assert duplicate.id == first.id

    with pytest.raises(DomainError, match="idempotency conflict"):
        controls.reserve_budget(
            campaign.id,
            CampaignBudgetReserve(
                resource="model_tokens",
                owner_id="wake-1",
                idempotency_key="wake-1:model",
                units=50,
                ttl_seconds=3600,
            ),
        )

    with pytest.raises(DomainError, match="budget exceeded"):
        controls.reserve_budget(
            campaign.id,
            CampaignBudgetReserve(
                resource="model_tokens",
                owner_id="wake-2",
                idempotency_key="wake-2:model",
                units=50,
                ttl_seconds=3600,
            ),
        )

    committed = controls.commit_budget(
        campaign.id,
        first.id,
        CampaignBudgetCommit(actual_units=40),
    )
    assert committed.status == "committed"
    assert committed.committed_units == 40
    current = controls.get_budget(campaign.id, "model_tokens")
    assert current.spent_units == 40
    assert current.reserved_units == 0
    assert current.available_units == 60

    assert (
        controls.commit_budget(
            campaign.id,
            first.id,
            CampaignBudgetCommit(actual_units=40),
        ).id
        == first.id
    )
    with pytest.raises(DomainError, match="different usage"):
        controls.commit_budget(
            campaign.id,
            first.id,
            CampaignBudgetCommit(actual_units=41),
        )

    second = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="model_tokens",
            owner_id="wake-2",
            idempotency_key="wake-2:model",
            units=60,
            ttl_seconds=3600,
        ),
    )
    assert controls.get_budget(campaign.id, "model_tokens").available_units == 0
    released = controls.release_budget(campaign.id, second.id)
    assert released.status == "released"
    assert controls.get_budget(campaign.id, "model_tokens").available_units == 60

    with pytest.raises(DomainError, match="below current"):
        controls.set_budget(
            campaign.id,
            "model_tokens",
            CampaignBudgetSet(limit_units=39),
        )
    lowered = controls.set_budget(
        campaign.id,
        "model_tokens",
        CampaignBudgetSet(limit_units=40),
    )
    assert lowered.available_units == 0


def test_campaign_budget_database_guards_preserve_capacity_and_history(database):
    campaign = campaign_fixture(database)
    controls = CampaignControlService(database)
    controls.set_budget(
        campaign.id,
        "model_tokens",
        CampaignBudgetSet(limit_units=100),
    )
    reservation = controls.reserve_budget(
        campaign.id,
        CampaignBudgetReserve(
            resource="model_tokens",
            owner_id="wake-1",
            idempotency_key="wake-1:model",
            units=60,
            ttl_seconds=3600,
        ),
    )

    statements = (
        (
            "UPDATE campaign_budget_limits SET limit_units=50 "
            "WHERE campaign_id=:campaign_id AND resource='model_tokens'",
            {"campaign_id": campaign.id},
        ),
        (
            "UPDATE campaign_budget_reservations SET owner_id='other' WHERE id=:id",
            {"id": reservation.id},
        ),
        (
            "DELETE FROM campaign_budget_reservations WHERE id=:id",
            {"id": reservation.id},
        ),
        (
            "DELETE FROM campaign_budget_limits "
            "WHERE campaign_id=:campaign_id AND resource='model_tokens'",
            {"campaign_id": campaign.id},
        ),
    )
    for statement, parameters in statements:
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(text(statement), parameters)

    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO campaign_budget_reservations (
                        id, campaign_id, resource, owner_id, idempotency_key,
                        request_hash, units, committed_units, status,
                        expires_at, created_at, updated_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000099',
                        :campaign_id, 'model_tokens', 'direct-writer',
                        'direct-writer:model', :request_hash, 50, 0, 'reserved',
                        '2099-01-01 00:00:00', '2026-09-26 00:00:00',
                        '2026-09-26 00:00:00'
                    )
                    """
                ),
                {"campaign_id": campaign.id, "request_hash": "f" * 64},
            )


def test_campaign_control_api_round_trip(client):
    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Zelo", "summary": "Persistent personal AI."},
    ).json()
    goal = client.post(
        f"/projects/{project['id']}/goals",
        headers=AUTH,
        json={
            "title": "Controlled campaign",
            "spec": {
                "objective": "Prove campaign controls through the authenticated API.",
                "success_criteria": ["Lease and budget state persist."],
                "constraints": ["No scheduler yet."],
            },
        },
    ).json()
    campaign = client.post(
        f"/goals/{goal['id']}/campaigns",
        headers=AUTH,
        json={"title": "Control API proof", "summary": "Lease and budget routes."},
    ).json()

    budget = client.put(
        f"/campaigns/{campaign['id']}/budgets/model_tokens",
        headers=AUTH,
        json={"limit_units": 1000},
    )
    assert budget.status_code == 200, budget.text
    assert budget.json()["available_units"] == 1000

    reservation = client.post(
        f"/campaigns/{campaign['id']}/budget-reservations",
        headers=AUTH,
        json={
            "resource": "model_tokens",
            "owner_id": "wake-api",
            "idempotency_key": "wake-api:model",
            "units": 250,
            "ttl_seconds": 3600,
        },
    )
    assert reservation.status_code == 201, reservation.text
    assert reservation.json()["status"] == "reserved"

    lease = client.post(
        f"/campaigns/{campaign['id']}/leases/execution/acquire",
        headers=AUTH,
        json={"holder_id": "scheduler-api", "ttl_seconds": 60},
    )
    assert lease.status_code == 201, lease.text
    assert lease.json()["generation"] == 1

    status = client.get(
        f"/campaigns/{campaign['id']}/leases/execution",
        headers=AUTH,
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "held"
