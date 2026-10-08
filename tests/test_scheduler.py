"""Bounded v0.12 Campaign scheduler and Mind wake regressions."""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.campaign_control import CampaignControlService
from app.config import Settings
from app.database import Database, migration_config
from app.gateway import ModelResult
from app.main import create_app
from app.policy import DomainError, fingerprint
from app.scheduler import WakeAuthorityUnavailable
from app.schemas import CampaignBudgetReserve, CampaignLeaseAcquire
from tests.conftest import AUTH
from tests.test_api import wait_for


class SchedulerMindProvider:
    def __init__(self, *actions) -> None:
        self.actions = list(actions)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if not self.actions:
            raise AssertionError("Unexpected scheduler Mind model call")
        action = self.actions.pop(0)
        return ModelResult(
            text=action if isinstance(action, str) else json.dumps(action),
            provider="fake",
            model_id=request.model_id,
            prompt_tokens=7,
            completion_tokens=5,
            total_tokens=12,
        )

    async def models(self):
        return ["qwen3:4b-instruct", "deepseek-r1:7b"]


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def reply(content: str) -> dict:
    return {"type": "reply", "content": content}


def scheduled_echo(content: str = "scheduled echo") -> dict:
    return {
        "type": "core_task",
        "content": "I will send the scheduled work to Core.",
        "task": {
            "kind": "echo",
            "payload": {"text": content},
        },
    }


def create_campaign(client: TestClient, *, title: str = "Scheduled campaign") -> dict:
    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": f"Zelo {title}", "summary": "Bounded scheduling proof."},
    )
    assert project.status_code == 201, project.text
    goal = client.post(
        f"/projects/{project.json()['id']}/goals",
        headers=AUTH,
        json={
            "title": f"{title} goal",
            "spec": {
                "objective": "Run bounded scheduled Zelo Mind wakes.",
                "success_criteria": ["Every wake is leased and budgeted."],
                "constraints": ["No free-running autonomy."],
            },
        },
    )
    assert goal.status_code == 201, goal.text
    campaign = client.post(
        f"/goals/{goal.json()['id']}/campaigns",
        headers=AUTH,
        json={"title": title, "summary": "Scheduler test campaign."},
    )
    assert campaign.status_code == 201, campaign.text
    return campaign.json()


def configure_wake_budget(
    client: TestClient,
    campaign_id: str,
    *,
    limit_units: int = 10,
) -> dict:
    response = client.put(
        f"/campaigns/{campaign_id}/budgets/mind_wakes",
        headers=AUTH,
        json={"limit_units": limit_units},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_conversation(client: TestClient) -> dict:
    response = client.post("/conversations", headers=AUTH, json={})
    assert response.status_code == 201, response.text
    return response.json()


def create_schedule(
    client: TestClient,
    campaign_id: str,
    conversation_id: str,
    *,
    prompt: str = "Perform one bounded scheduled check.",
    run_at: datetime | None = None,
    interval_seconds: int | None = None,
    max_runs: int = 1,
    mode: str = "cadence",
    idempotency_key: str = "schedule-create-1",
) -> dict:
    body = {
        "conversation_id": conversation_id,
        "prompt": prompt,
        "run_at": (run_at or datetime.now(UTC)).isoformat(),
        "max_runs": max_runs,
        "mode": mode,
    }
    if interval_seconds is not None:
        body["interval_seconds"] = interval_seconds
    response = client.post(
        f"/campaigns/{campaign_id}/schedules",
        headers={**AUTH, "Idempotency-Key": idempotency_key},
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_schedule(
    client: TestClient,
    schedule_id: str,
    expected: str,
    *,
    timeout: float = 3.0,
) -> dict:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/schedules/{schedule_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] == expected:
            return latest
        time.sleep(0.01)
    pytest.fail(f"Schedule did not reach {expected}: {latest}")


def test_scheduler_config_requires_control_headroom():
    with pytest.raises(ValueError, match="control headroom"):
        Settings(
            _env_file=None,
            scheduler_enabled=True,
            model_timeout_seconds=10,
            max_model_parse_retries=0,
            mind_admission_timeout_seconds=5,
            scheduler_wake_ttl_seconds=30,
            scheduler_control_headroom_seconds=30,
        )

    configured = Settings(
        _env_file=None,
        scheduler_enabled=True,
        model_timeout_seconds=10,
        max_model_parse_retries=0,
        mind_admission_timeout_seconds=5,
        scheduler_wake_ttl_seconds=60,
        scheduler_control_headroom_seconds=30,
    )
    assert configured.scheduler_mind_window_seconds == 30
    assert configured.scheduler_wake_ttl_seconds == 60


def test_due_selection_skips_campaign_with_active_wake_lease(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        first = create_campaign(client, title="Busy campaign")
        second = create_campaign(client, title="Free campaign")
        configure_wake_budget(client, first["id"])
        configure_wake_budget(client, second["id"])
        first_conversation = create_conversation(client)
        second_conversation = create_conversation(client)
        now = datetime.now(UTC)

        first_schedule = create_schedule(
            client,
            first["id"],
            first_conversation["id"],
            run_at=now - timedelta(seconds=2),
            idempotency_key="busy-schedule",
        )
        second_schedule = create_schedule(
            client,
            second["id"],
            second_conversation["id"],
            run_at=now - timedelta(seconds=1),
            idempotency_key="free-schedule",
        )

        controls: CampaignControlService = client.app.state.campaign_controls
        controls.acquire_lease(
            first["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id="external-holder", ttl_seconds=600),
        )

        scheduler = client.app.state.scheduler
        assert scheduler._next_due_id() == second_schedule["id"]
        assert scheduler._next_due_id() != first_schedule["id"]


def test_one_shot_wake_is_leased_budgeted_and_persisted(settings, database):
    provider = SchedulerMindProvider(reply("Scheduled wake complete."))
    configured = settings.model_copy(
        update={
            "scheduler_poll_seconds": 0.01,
            "scheduler_wake_ttl_seconds": 600,
        }
    )
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=3)
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])

        completed = wait_schedule(client, schedule["id"], "completed")
        assert completed["run_count"] == 1
        assert completed["next_run_at"] is None
        assert completed["last_error"] is None

        runs = client.get(f"/schedules/{schedule['id']}/runs", headers=AUTH).json()
        assert runs["total"] == 1
        run = runs["items"][0]
        assert run["sequence"] == 1
        assert run["status"] == "succeeded"
        assert run["lease_generation"] == 1
        assert run["budget_reservation_id"] is not None
        assert run["conversation_turn_id"] is not None
        assert "lease_token" not in run

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 1
        assert budget["reserved_units"] == 0
        assert budget["available_units"] == 2

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 1
        assert stored["turns"][0]["assistant_content"] == "Scheduled wake complete."
        assert stored["turns"][0]["core_task"] is None

        events = client.get(
            f"/campaigns/{campaign['id']}/events",
            headers=AUTH,
        ).json()["items"]
        names = [event["event"] for event in events]
        assert "schedule_created" in names
        assert "schedule_wake_started" in names
        assert "budget_reconciled" in names
        assert "schedule_wake_finished" in names


def test_scheduled_mind_core_task_is_forced_into_same_campaign(settings, database):
    provider = SchedulerMindProvider(scheduled_echo())
    configured = settings.model_copy(update={"scheduler_poll_seconds": 0.01})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            prompt="Echo scheduled work through Core.",
        )

        wait_schedule(client, schedule["id"], "completed")
        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 1
        task = stored["turns"][0]["core_task"]
        assert task is not None
        assert task["campaign_id"] == campaign["id"]

        finished = wait_for(client, task["id"], "succeeded")
        assert finished["campaign_id"] == campaign["id"]
        assert finished["result"] == {"text": "scheduled echo"}


def test_recurring_schedule_advances_from_completion_without_catch_up(settings, database):
    provider = SchedulerMindProvider(reply("first"), reply("second"))
    configured = settings.model_copy(
        update={
            "scheduler_enabled": False,
            "scheduler_poll_seconds": 0.01,
        }
    )
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        clock = MutableClock(datetime(2026, 9, 26, 12, 0, tzinfo=UTC))
        scheduler.clock = clock

        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
            interval_seconds=60,
            max_runs=2,
        )

        asyncio.run(scheduler._execute_due(schedule["id"]))
        first = client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()
        assert first["status"] == "active"
        assert first["run_count"] == 1
        assert datetime.fromisoformat(first["next_run_at"]) == clock.value + timedelta(seconds=60)

        clock.advance(seconds=600)
        asyncio.run(scheduler._execute_due(schedule["id"]))
        second = client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()
        assert second["status"] == "completed"
        assert second["run_count"] == 2
        assert second["next_run_at"] is None

        runs = client.get(f"/schedules/{schedule['id']}/runs", headers=AUTH).json()
        assert [item["sequence"] for item in runs["items"]] == [1, 2]
        assert [item["status"] for item in runs["items"]] == ["succeeded", "succeeded"]

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 2


def test_cadence_schedule_retry_preserves_legacy_idempotency_hash(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client, title="Legacy cadence idempotency")
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        run_at = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        prompt = "Preserve the pre-mode cadence request hash."
        legacy_hash = fingerprint(
            {
                "conversation_id": conversation["id"],
                "prompt": prompt,
                "run_at": run_at.isoformat().replace("+00:00", "Z"),
                "interval_seconds": None,
                "max_runs": 1,
            }
        )

        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO campaign_schedules (
                        id, campaign_id, conversation_id, prompt,
                        idempotency_key, request_hash, status, next_run_at,
                        interval_seconds, max_runs, run_count,
                        last_run_at, last_error, created_at, updated_at
                    ) VALUES (
                        '72000000-0000-0000-0000-000000000072',
                        :campaign_id, :conversation_id, :prompt,
                        'legacy-cadence-retry', :request_hash, 'active', :run_at,
                        NULL, 1, 0, NULL, NULL, :run_at, :run_at
                    )
                    """
                ),
                {
                    "campaign_id": campaign["id"],
                    "conversation_id": conversation["id"],
                    "prompt": prompt,
                    "request_hash": legacy_hash,
                    "run_at": run_at,
                },
            )

        response = client.post(
            f"/campaigns/{campaign['id']}/schedules",
            headers={**AUTH, "Idempotency-Key": "legacy-cadence-retry"},
            json={
                "conversation_id": conversation["id"],
                "prompt": prompt,
                "run_at": run_at.isoformat(),
                "max_runs": 1,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["id"] == "72000000-0000-0000-0000-000000000072"
        assert response.json()["mode"] == "cadence"


def test_continuation_schedule_idempotent_create_preserves_mode(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client, title="Continuation idempotency")
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        run_at = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

        first = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=run_at,
            interval_seconds=3600,
            max_runs=2,
            mode="continuation",
            idempotency_key="continuation-idempotent",
        )
        second = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=run_at,
            interval_seconds=3600,
            max_runs=2,
            mode="continuation",
            idempotency_key="continuation-idempotent",
        )

        assert second["id"] == first["id"]
        assert first["mode"] == "continuation"
        assert second["mode"] == "continuation"


def test_continuation_schedule_accelerates_then_uses_timer_fallback_for_reply(
    settings,
    database,
):
    provider = SchedulerMindProvider(
        scheduled_echo("continuation step"),
        reply("No next Core task yet."),
        reply("Still no next Core task."),
    )
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client, title="Continuation campaign")
        configure_wake_budget(client, campaign["id"], limit_units=3)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        clock = MutableClock(datetime(2026, 9, 26, 12, 0, tzinfo=UTC))
        scheduler.clock = clock

        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            prompt="Continue this Campaign within the configured wake bound.",
            run_at=clock.value,
            interval_seconds=3600,
            max_runs=3,
            mode="continuation",
            idempotency_key="continuation-schedule",
        )
        assert schedule["mode"] == "continuation"

        asyncio.run(scheduler._execute_due(schedule["id"]))
        first = client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()
        assert first["status"] == "active"
        assert first["run_count"] == 1
        assert datetime.fromisoformat(first["next_run_at"]) == clock.value + timedelta(hours=1)

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        task = stored["turns"][0]["core_task"]
        assert task is not None
        wait_for(client, task["id"], "succeeded")

        assert scheduler._next_due_id() == schedule["id"]
        asyncio.run(scheduler._execute_due(schedule["id"]))

        second = client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()
        assert second["status"] == "active"
        assert second["run_count"] == 2
        assert datetime.fromisoformat(second["next_run_at"]) == clock.value + timedelta(hours=1)
        assert scheduler._next_due_id() is None

        clock.advance(seconds=3600)
        assert scheduler._next_due_id() == schedule["id"]
        asyncio.run(scheduler._execute_due(schedule["id"]))

        completed = client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()
        assert completed["status"] == "completed"
        assert completed["mode"] == "continuation"
        assert completed["run_count"] == 3
        assert completed["next_run_at"] is None
        assert len(provider.requests) == 3

        events = client.get(
            f"/campaigns/{campaign['id']}/events",
            headers=AUTH,
        ).json()["items"]
        assert not any(event["event"] == "schedule_continuation_completed" for event in events)


def test_repeated_continuation_cancellation_preserves_mode(settings, database):
    provider = SchedulerMindProvider(reply("Cancellation should not wake Mind."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client, title="Continuation cancellation")
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        run_at = datetime(2026, 9, 26, 13, 0, tzinfo=UTC)
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=run_at,
            interval_seconds=3600,
            max_runs=2,
            mode="continuation",
            idempotency_key="continuation-cancel",
        )

        first = client.post(f"/schedules/{schedule['id']}/cancel", headers=AUTH)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "cancelled"
        assert first.json()["mode"] == "continuation"

        second = client.post(f"/schedules/{schedule['id']}/cancel", headers=AUTH)
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "cancelled"
        assert second.json()["mode"] == "continuation"
        assert provider.requests == []


def test_cadence_schedule_is_not_accelerated_by_task_completion(settings, database):
    provider = SchedulerMindProvider(scheduled_echo("cadence step"))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client, title="Cadence campaign")
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        clock = MutableClock(datetime(2026, 9, 26, 12, 0, tzinfo=UTC))
        scheduler.clock = clock

        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
            interval_seconds=3600,
            max_runs=2,
            mode="cadence",
            idempotency_key="cadence-task-schedule",
        )
        asyncio.run(scheduler._execute_due(schedule["id"]))

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        task = stored["turns"][0]["core_task"]
        assert task is not None
        wait_for(client, task["id"], "succeeded")

        assert scheduler._next_due_id() is None
        current = client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()
        assert current["status"] == "active"
        assert current["mode"] == "cadence"
        assert current["run_count"] == 1
        assert datetime.fromisoformat(current["next_run_at"]) == clock.value + timedelta(hours=1)


def test_restart_retries_same_logical_occurrence_without_double_charge(settings, database):
    provider = SchedulerMindProvider(reply("Recovered occurrence."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=3)
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls

        holder = f"schedule:{schedule['id']}"
        first_lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=first_lease.lease_token,
            lease_generation=first_lease.generation,
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key=(
                    f"schedule:{schedule['id']}:{run.sequence}:lease:{first_lease.generation}"
                ),
                units=1,
                ttl_seconds=600,
            ),
        )
        scheduler._bind_budget(run.id, reservation.id)

        assert scheduler.recover() == 1
        interrupted = scheduler.get_run(run.id)
        assert interrupted.status == "interrupted"
        assert client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()["run_count"] == 0

        asyncio.run(scheduler._execute_due(schedule["id"]))

        runs = client.get(f"/schedules/{schedule['id']}/runs", headers=AUTH).json()
        assert runs["total"] == 1
        recovered = runs["items"][0]
        assert recovered["id"] == run.id
        assert recovered["sequence"] == 1
        assert recovered["status"] == "succeeded"
        assert recovered["lease_generation"] == first_lease.generation + 1

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 1
        assert budget["reserved_units"] == 0

        reservations = client.get(
            f"/campaigns/{campaign['id']}/budget-reservations",
            headers=AUTH,
        ).json()["items"]
        assert [item["status"] for item in reservations] == ["released", "committed"]


def test_restart_reconciles_persisted_turn_charge_before_generic_cleanup(
    settings,
    database,
):
    provider = SchedulerMindProvider(reply("Persisted before restart."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=1)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )
        holder = f"schedule:{schedule['id']}"

        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(
                holder_id=holder,
                ttl_seconds=configured.scheduler_wake_ttl_seconds,
            ),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=lease.lease_token,
            lease_generation=lease.generation,
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="restart-persisted-turn",
                units=1,
                ttl_seconds=configured.scheduler_wake_ttl_seconds,
            ),
        )
        scheduler._bind_budget(run.id, reservation.id)

        turn = asyncio.run(
            scheduler.mind.send_turn(
                conversation["id"],
                schedule["prompt"],
                f"schedule:{schedule['id']}:1",
                campaign_id=campaign["id"],
            )
        )
        assert turn.id is not None

        clock.advance(seconds=configured.scheduler_wake_ttl_seconds + 1)

        assert scheduler.recover() == 1
        recovered = scheduler.get_run(run.id)
        assert recovered.status == "interrupted"

        budget = controls.get_budget(campaign["id"], "mind_wakes")
        assert budget.spent_units == 1
        assert budget.reserved_units == 0

        reservations = client.get(
            f"/campaigns/{campaign['id']}/budget-reservations",
            headers=AUTH,
        ).json()["items"]
        assert len(reservations) == 1
        assert reservations[0]["id"] == reservation.id
        assert reservations[0]["status"] == "committed"
        assert reservations[0]["committed_units"] == 1

        controls.recover_expired()

        after_generic_cleanup = controls.get_budget(campaign["id"], "mind_wakes")
        assert after_generic_cleanup.spent_units == 1
        assert after_generic_cleanup.reserved_units == 0


def test_conversation_wait_rechecks_authority_before_mind(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(reply("This must not run after waiting."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )

        first_authority_check = threading.Event()
        original_require = scheduler._require_live_mind_authority

        def require_and_signal(run_id: str, **kwargs) -> None:
            original_require(run_id, **kwargs)
            first_authority_check.set()

        monkeypatch.setattr(
            scheduler,
            "_require_live_mind_authority",
            require_and_signal,
        )

        async def exercise_wait() -> None:
            lock = scheduler.mind._lock(conversation["id"])
            await lock.acquire()
            task = asyncio.create_task(scheduler._execute_due(schedule["id"]))
            try:
                assert await asyncio.to_thread(first_authority_check.wait, 1)
                clock.advance(seconds=configured.scheduler_wake_ttl_seconds + 1)
            finally:
                lock.release()
            await task

        asyncio.run(exercise_wait())

        assert provider.requests == []
        runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert runs["total"] == 1
        assert runs["items"][0]["status"] == "interrupted"

        current = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert current["status"] == "active"
        assert current["run_count"] == 0

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 0
        assert budget["reserved_units"] == 0


def test_post_model_authority_check_blocks_persistence(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(scheduled_echo("must not persist"))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )

        original_generate = scheduler.mind._generate_action

        async def generate_then_age(*args, **kwargs):
            result = await original_generate(*args, **kwargs)
            clock.advance(seconds=configured.scheduler_wake_ttl_seconds + 1)
            return result

        monkeypatch.setattr(scheduler.mind, "_generate_action", generate_then_age)
        asyncio.run(scheduler._execute_due(schedule["id"]))

        assert len(provider.requests) == 1
        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 0

        runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert runs["total"] == 1
        assert runs["items"][0]["status"] == "interrupted"

        current = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert current["run_count"] == 0
        assert current["status"] == "active"

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 0
        assert budget["reserved_units"] == 0


def test_mind_is_not_invoked_without_full_authority_window(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(reply("This must not run."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )

        original_bind = scheduler._bind_budget

        def bind_then_age(run_id: str, reservation_id: str) -> None:
            original_bind(run_id, reservation_id)
            age_seconds = int(
                configured.scheduler_wake_ttl_seconds - configured.scheduler_mind_window_seconds + 1
            )
            clock.advance(seconds=age_seconds)

        monkeypatch.setattr(scheduler, "_bind_budget", bind_then_age)
        asyncio.run(scheduler._execute_due(schedule["id"]))

        assert provider.requests == []
        runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert runs["total"] == 1
        assert runs["items"][0]["status"] == "interrupted"

        current = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert current["status"] == "active"
        assert current["run_count"] == 0

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 0
        assert budget["reserved_units"] == 0


def test_post_mind_authority_failure_retries_same_occurrence_without_double_charge(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(scheduled_echo("reconciled scheduled echo"))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=3)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )

        original_send_turn = scheduler.mind.send_turn
        aged = False

        async def send_then_expire(*args, **kwargs):
            nonlocal aged
            result = await original_send_turn(*args, **kwargs)
            if not aged:
                clock.advance(seconds=configured.scheduler_wake_ttl_seconds + 1)
                aged = True
            return result

        monkeypatch.setattr(scheduler.mind, "send_turn", send_then_expire)

        asyncio.run(scheduler._execute_due(schedule["id"]))

        first_runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert first_runs["total"] == 1
        assert first_runs["items"][0]["status"] == "interrupted"
        run_id = first_runs["items"][0]["id"]

        first_schedule = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert first_schedule["run_count"] == 0
        assert first_schedule["status"] == "active"

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 1
        task = stored["turns"][0]["core_task"]
        assert task is not None
        assert task["campaign_id"] == campaign["id"]
        assert len(provider.requests) == 1

        first_budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert first_budget["spent_units"] == 1
        assert first_budget["reserved_units"] == 0

        asyncio.run(scheduler._execute_due(schedule["id"]))

        completed = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert completed["status"] == "completed"
        assert completed["run_count"] == 1

        final_runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert final_runs["total"] == 1
        assert final_runs["items"][0]["id"] == run_id
        assert final_runs["items"][0]["sequence"] == 1
        assert final_runs["items"][0]["status"] == "succeeded"

        final_stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert final_stored["turn_total"] == 1
        assert final_stored["turns"][0]["core_task"]["id"] == task["id"]
        assert len(provider.requests) == 1

        final_budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert final_budget["spent_units"] == 1
        assert final_budget["reserved_units"] == 0


def test_cancellation_joins_budget_reservation_before_cleanup(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(reply("Budget cancellation must not reach Mind."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=1)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        schedule = create_schedule(client, campaign["id"], conversation["id"])

        started = threading.Event()
        release = threading.Event()
        original_reserve = controls.reserve_budget

        def slow_reserve(campaign_id, body):
            started.set()
            if not release.wait(timeout=2):
                raise AssertionError("Timed out waiting to release budget reservation")
            return original_reserve(campaign_id, body)

        monkeypatch.setattr(controls, "reserve_budget", slow_reserve)

        async def exercise_cancellation() -> None:
            operation = asyncio.create_task(scheduler._execute_due(schedule["id"]))
            assert await asyncio.to_thread(started.wait, 1)
            operation.cancel()
            await asyncio.sleep(0.01)

            lease = controls.lease_status(campaign["id"], "mind-wake")
            assert lease.status == "held"
            assert not operation.done()

            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        asyncio.run(exercise_cancellation())

        assert provider.requests == []
        lease = controls.lease_status(campaign["id"], "mind-wake")
        budget = controls.get_budget(campaign["id"], "mind_wakes")
        assert lease.status == "free"
        assert budget.spent_units == 0
        assert budget.reserved_units == 0

        reservations = client.get(
            f"/campaigns/{campaign['id']}/budget-reservations",
            headers=AUTH,
        ).json()["items"]
        assert len(reservations) == 1
        assert reservations[0]["status"] == "released"

        runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert runs["total"] == 1
        assert runs["items"][0]["status"] == "interrupted"

        current = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert current["run_count"] == 0
        assert current["status"] == "active"


def test_cancellation_waits_for_threaded_core_task_submission(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(scheduled_echo("threaded scheduled echo"))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        schedule = create_schedule(client, campaign["id"], conversation["id"])

        started = threading.Event()
        release = threading.Event()
        original_submit = scheduler.mind.tasks.submit

        def slow_submit(request, key):
            started.set()
            if not release.wait(timeout=2):
                raise AssertionError("Timed out waiting to release task submission")
            return original_submit(request, key)

        monkeypatch.setattr(scheduler.mind.tasks, "submit", slow_submit)

        async def exercise_cancellation() -> None:
            operation = asyncio.create_task(scheduler._execute_due(schedule["id"]))
            assert await asyncio.to_thread(started.wait, 1)
            operation.cancel()
            await asyncio.sleep(0.01)

            lease = controls.lease_status(campaign["id"], "mind-wake")
            budget = controls.get_budget(campaign["id"], "mind_wakes")
            assert lease.status == "held"
            assert budget.spent_units == 1
            assert budget.reserved_units == 0
            assert not operation.done()

            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        asyncio.run(exercise_cancellation())

        lease = controls.lease_status(campaign["id"], "mind-wake")
        budget = controls.get_budget(campaign["id"], "mind_wakes")
        assert lease.status == "free"
        assert budget.spent_units == 1
        assert budget.reserved_units == 0

        runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert runs["total"] == 1
        assert runs["items"][0]["status"] == "interrupted"

        current = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert current["status"] == "active"
        assert current["run_count"] == 0

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 1
        assert stored["turns"][0]["core_task"] is not None
        assert stored["turns"][0]["core_task"]["campaign_id"] == campaign["id"]


def test_cancel_after_task_link_failure_keeps_committed_wake_charge(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(scheduled_echo("cancel after link failure"))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=1)
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        scheduler = client.app.state.scheduler

        submitted_task_id = None

        def fail_link(turn_id: str, task_id: str):
            nonlocal submitted_task_id
            submitted_task_id = task_id
            raise DomainError("synthetic permanent link failure", 500)

        monkeypatch.setattr(scheduler.mind, "_link_core_task", fail_link)

        asyncio.run(scheduler._execute_due(schedule["id"]))
        assert submitted_task_id is not None

        runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert runs["total"] == 1
        assert runs["items"][0]["status"] == "interrupted"

        before_cancel = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert before_cancel["spent_units"] == 1
        assert before_cancel["reserved_units"] == 0

        cancelled = client.post(
            f"/schedules/{schedule['id']}/cancel",
            headers=AUTH,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        after_cancel = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert after_cancel["spent_units"] == 1
        assert after_cancel["reserved_units"] == 0

        reservations = client.get(
            f"/campaigns/{campaign['id']}/budget-reservations",
            headers=AUTH,
        ).json()["items"]
        assert len(reservations) == 1
        assert reservations[0]["status"] == "committed"
        assert reservations[0]["committed_units"] == 1

        current = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert current["status"] == "cancelled"
        assert current["run_count"] == 0

        task = client.get(f"/tasks/{submitted_task_id}", headers=AUTH)
        assert task.status_code == 200, task.text
        assert task.json()["campaign_id"] == campaign["id"]


def test_task_link_failure_retries_same_persisted_occurrence(
    settings,
    database,
    monkeypatch,
):
    provider = SchedulerMindProvider(scheduled_echo("link retry echo"))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=3)
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        scheduler = client.app.state.scheduler

        original_link = scheduler.mind._link_core_task
        failed_once = False

        def fail_first_link(turn_id: str, task_id: str):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise DomainError("synthetic link failure", 500)
            return original_link(turn_id, task_id)

        monkeypatch.setattr(scheduler.mind, "_link_core_task", fail_first_link)

        asyncio.run(scheduler._execute_due(schedule["id"]))

        first_runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert first_runs["total"] == 1
        assert first_runs["items"][0]["status"] == "interrupted"
        run_id = first_runs["items"][0]["id"]

        first_schedule = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert first_schedule["run_count"] == 0
        assert first_schedule["status"] == "active"

        stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert stored["turn_total"] == 1
        assert stored["turns"][0]["core_task"] is None
        assert len(provider.requests) == 1

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 1
        assert budget["reserved_units"] == 0

        asyncio.run(scheduler._execute_due(schedule["id"]))

        completed = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert completed["status"] == "completed"
        assert completed["run_count"] == 1

        final_runs = client.get(
            f"/schedules/{schedule['id']}/runs",
            headers=AUTH,
        ).json()
        assert final_runs["total"] == 1
        assert final_runs["items"][0]["id"] == run_id
        assert final_runs["items"][0]["status"] == "succeeded"

        final_stored = client.get(
            f"/conversations/{conversation['id']}",
            headers=AUTH,
        ).json()
        assert final_stored["turn_total"] == 1
        assert final_stored["turns"][0]["core_task"] is not None
        assert final_stored["turns"][0]["core_task"]["campaign_id"] == campaign["id"]
        assert len(provider.requests) == 1

        final_budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert final_budget["spent_units"] == 1
        assert final_budget["reserved_units"] == 0


def test_success_fails_closed_after_wake_lease_is_replaced(settings, database):
    provider = SchedulerMindProvider(reply("Late wake result."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )
        holder = f"schedule:{schedule['id']}"

        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=30),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=lease.lease_token,
            lease_generation=lease.generation,
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="stale-lease-budget",
                units=1,
                ttl_seconds=600,
            ),
        )
        scheduler._bind_budget(run.id, reservation.id)
        turn = asyncio.run(
            scheduler.mind.send_turn(
                conversation["id"],
                schedule["prompt"],
                f"schedule:{schedule['id']}:1",
                campaign_id=campaign["id"],
            )
        )

        clock.advance(seconds=31)
        replacement = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id="replacement-holder", ttl_seconds=600),
        )
        assert replacement.generation == lease.generation + 1

        with pytest.raises(WakeAuthorityUnavailable, match="wake_lease_finalization_stale"):
            scheduler._finalize_run(
                run.id,
                status="succeeded",
                conversation_turn_id=turn.id,
                error=None,
            )

        assert scheduler.get_run(run.id).status == "running"
        assert client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()["run_count"] == 0


def test_success_fails_closed_after_wake_budget_expires(settings, database):
    provider = SchedulerMindProvider(reply("Late budget result."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )
        holder = f"schedule:{schedule['id']}"

        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=lease.lease_token,
            lease_generation=lease.generation,
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="expiring-wake-budget",
                units=1,
                ttl_seconds=30,
            ),
        )
        scheduler._bind_budget(run.id, reservation.id)
        turn = asyncio.run(
            scheduler.mind.send_turn(
                conversation["id"],
                schedule["prompt"],
                f"schedule:{schedule['id']}:1",
                campaign_id=campaign["id"],
            )
        )

        clock.advance(seconds=31)
        with pytest.raises(WakeAuthorityUnavailable, match="wake_budget_finalization_expired"):
            scheduler._finalize_run(
                run.id,
                status="succeeded",
                conversation_turn_id=turn.id,
                error=None,
            )

        assert scheduler.get_run(run.id).status == "running"
        assert client.get(f"/schedules/{schedule['id']}", headers=AUTH).json()["run_count"] == 0


def test_cancelled_schedule_allows_already_started_wake_to_close(settings, database):
    provider = SchedulerMindProvider(reply("Finished after cancellation."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"], limit_units=2)
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        holder = f"schedule:{schedule['id']}"

        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=lease.lease_token,
            lease_generation=lease.generation,
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key=f"cancelled:{schedule['id']}:1",
                units=1,
                ttl_seconds=600,
            ),
        )
        scheduler._bind_budget(run.id, reservation.id)

        cancelled = client.post(
            f"/schedules/{schedule['id']}/cancel",
            headers=AUTH,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        turn = asyncio.run(
            scheduler.mind.send_turn(
                conversation["id"],
                schedule["prompt"],
                f"schedule:{schedule['id']}:1",
                campaign_id=campaign["id"],
            )
        )
        scheduler._finalize_run(
            run.id,
            status="succeeded",
            conversation_turn_id=turn.id,
            error=None,
        )

        final_schedule = client.get(
            f"/schedules/{schedule['id']}",
            headers=AUTH,
        ).json()
        assert final_schedule["status"] == "cancelled"
        assert final_schedule["run_count"] == 0
        assert final_schedule["next_run_at"] is None

        final_run = scheduler.get_run(run.id)
        assert final_run.status == "succeeded"
        assert final_run.conversation_turn_id == turn.id

        budget = client.get(
            f"/campaigns/{campaign['id']}/budgets/mind_wakes",
            headers=AUTH,
        ).json()
        assert budget["spent_units"] == 1
        assert budget["reserved_units"] == 0


def test_schedule_requires_budget_and_bounded_cadence(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        conversation = create_conversation(client)

        missing_budget = client.post(
            f"/campaigns/{campaign['id']}/schedules",
            headers={**AUTH, "Idempotency-Key": "missing-budget"},
            json={
                "conversation_id": conversation["id"],
                "prompt": "Do not run without budget.",
                "run_at": datetime.now(UTC).isoformat(),
                "max_runs": 1,
            },
        )
        assert missing_budget.status_code == 409
        assert "mind_wakes budget" in missing_budget.json()["detail"]

        configure_wake_budget(client, campaign["id"])
        invalid = client.post(
            f"/campaigns/{campaign['id']}/schedules",
            headers={**AUTH, "Idempotency-Key": "invalid-recurrence"},
            json={
                "conversation_id": conversation["id"],
                "prompt": "Invalid recurrence.",
                "run_at": datetime.now(UTC).isoformat(),
                "interval_seconds": 30,
                "max_runs": 2,
            },
        )
        assert invalid.status_code == 422


def test_schedule_creation_is_idempotent_and_conflicts_fail_closed(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        run_at = datetime.now(UTC) + timedelta(hours=1)

        first = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=run_at,
            idempotency_key="durable-create",
        )
        duplicate = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=run_at,
            idempotency_key="durable-create",
        )
        assert duplicate["id"] == first["id"]

        conflict = client.post(
            f"/campaigns/{campaign['id']}/schedules",
            headers={**AUTH, "Idempotency-Key": "durable-create"},
            json={
                "conversation_id": conversation["id"],
                "prompt": "Different request under same key.",
                "run_at": run_at.isoformat(),
                "max_runs": 1,
            },
        )
        assert conflict.status_code == 409
        assert "idempotency" in conflict.json()["detail"].lower()


def test_external_chat_cannot_preempt_scheduled_turn_namespace(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        conversation = create_conversation(client)
        response = client.post(
            f"/conversations/{conversation['id']}/turns",
            headers={**AUTH, "Idempotency-Key": "schedule:reserved-key"},
            json={"text": "Try to preempt a scheduled turn."},
        )
        assert response.status_code == 422
        assert "reserved" in response.json()["detail"].lower()
        assert provider.requests == []


def test_database_rejects_terminal_schedule_run_insert(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        controls: CampaignControlService = client.app.state.campaign_controls
        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(
                holder_id=f"schedule:{schedule['id']}",
                ttl_seconds=600,
            ),
        )

        with pytest.raises(IntegrityError, match="must start running and unbound"):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO campaign_schedule_runs (
                            id, schedule_id, sequence, status,
                            lease_holder_id, lease_token, lease_generation,
                            started_at, completed_at
                        ) VALUES (
                            '70000000-0000-0000-0000-000000000007',
                            :schedule_id, 1, 'failed',
                            :holder_id, :lease_token, :generation,
                            :now, :now
                        )
                        """
                    ),
                    {
                        "schedule_id": schedule["id"],
                        "holder_id": lease.holder_id,
                        "lease_token": lease.lease_token,
                        "generation": lease.generation,
                        "now": datetime.now(UTC),
                    },
                )


def test_database_rejects_future_schedule_run_sequence(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            interval_seconds=60,
            max_runs=2,
        )
        controls: CampaignControlService = client.app.state.campaign_controls
        holder = f"schedule:{schedule['id']}"
        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )

        with pytest.raises(IntegrityError, match="lease proof is not current"):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO campaign_schedule_runs (
                            id, schedule_id, sequence, status,
                            lease_holder_id, lease_token, lease_generation,
                            started_at, completed_at
                        ) VALUES (
                            '73000000-0000-0000-0000-000000000073',
                            :schedule_id, 2, 'running',
                            :holder_id, :lease_token, :generation,
                            :now, NULL
                        )
                        """
                    ),
                    {
                        "schedule_id": schedule["id"],
                        "holder_id": holder,
                        "lease_token": lease.lease_token,
                        "generation": lease.generation,
                        "now": datetime.now(UTC),
                    },
                )


def test_database_rejects_prebound_running_schedule_run_insert(settings, database):
    provider = SchedulerMindProvider(reply("Ordinary turn."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        controls: CampaignControlService = client.app.state.campaign_controls
        holder = f"schedule:{schedule['id']}"
        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="prebound-insert",
                units=1,
                ttl_seconds=600,
            ),
        )
        turn = client.post(
            f"/conversations/{conversation['id']}/turns",
            headers={**AUTH, "Idempotency-Key": "ordinary-prebound"},
            json={"text": "Ordinary turn."},
        )
        assert turn.status_code == 201, turn.text

        with pytest.raises(IntegrityError, match="start running and unbound"):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO campaign_schedule_runs (
                            id, schedule_id, sequence, status,
                            lease_holder_id, lease_token, lease_generation,
                            budget_reservation_id, conversation_turn_id,
                            started_at, completed_at
                        ) VALUES (
                            '71000000-0000-0000-0000-000000000071',
                            :schedule_id, 1, 'running',
                            :holder_id, :lease_token, :generation,
                            :reservation_id, :turn_id,
                            :now, NULL
                        )
                        """
                    ),
                    {
                        "schedule_id": schedule["id"],
                        "holder_id": holder,
                        "lease_token": lease.lease_token,
                        "generation": lease.generation,
                        "reservation_id": reservation.id,
                        "turn_id": turn.json()["id"],
                        "now": datetime.now(UTC),
                    },
                )


def test_database_rejects_expired_lease_on_run_insert(settings, database):
    provider = SchedulerMindProvider()
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        clock = MutableClock(datetime.now(UTC))
        scheduler.clock = clock
        controls.clock = clock
        schedule = create_schedule(
            client,
            campaign["id"],
            conversation["id"],
            run_at=clock.value,
        )
        holder = f"schedule:{schedule['id']}"
        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=30),
        )
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    UPDATE campaign_leases
                    SET expires_at = datetime('now', '-1 second')
                    WHERE campaign_id = :campaign_id AND scope = 'mind-wake'
                    """
                ),
                {"campaign_id": campaign["id"]},
            )

        with pytest.raises(IntegrityError, match="lease proof is not current"):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO campaign_schedule_runs (
                            id, schedule_id, sequence, status,
                            lease_holder_id, lease_token, lease_generation,
                            started_at, completed_at
                        ) VALUES (
                            '72000000-0000-0000-0000-000000000072',
                            :schedule_id, 1, 'running',
                            :holder_id, :lease_token, :generation,
                            :now, NULL
                        )
                        """
                    ),
                    {
                        "schedule_id": schedule["id"],
                        "holder_id": holder,
                        "lease_token": lease.lease_token,
                        "generation": lease.generation,
                        "now": clock.value,
                    },
                )


def test_database_requires_committed_budget_for_success(settings, database):
    provider = SchedulerMindProvider(reply("Scheduled result."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        campaign = create_campaign(client)
        configure_wake_budget(client, campaign["id"])
        conversation = create_conversation(client)
        schedule = create_schedule(client, campaign["id"], conversation["id"])
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        holder = f"schedule:{schedule['id']}"
        lease = controls.acquire_lease(
            campaign["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=lease.lease_token,
            lease_generation=lease.generation,
        )
        reservation = controls.reserve_budget(
            campaign["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="reserved-not-committed",
                units=1,
                ttl_seconds=600,
            ),
        )
        scheduler._bind_budget(run.id, reservation.id)
        turn = asyncio.run(
            scheduler.mind.send_turn(
                conversation["id"],
                schedule["prompt"],
                f"schedule:{schedule['id']}:1",
                campaign_id=campaign["id"],
            )
        )

        with pytest.raises(IntegrityError, match="budget reservation is stale"):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        UPDATE campaign_schedule_runs
                        SET status = 'succeeded',
                            conversation_turn_id = :turn_id,
                            completed_at = :now
                        WHERE id = :run_id
                        """
                    ),
                    {
                        "turn_id": turn.id,
                        "now": datetime.now(UTC),
                        "run_id": run.id,
                    },
                )


def test_database_rejects_cross_campaign_wake_budget_binding(settings, database):
    provider = SchedulerMindProvider(reply("Ordinary turn."))
    configured = settings.model_copy(update={"scheduler_enabled": False})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        first = create_campaign(client, title="First")
        second = create_campaign(client, title="Second")
        configure_wake_budget(client, first["id"])
        configure_wake_budget(client, second["id"])
        conversation = create_conversation(client)
        schedule = create_schedule(client, first["id"], conversation["id"])
        scheduler = client.app.state.scheduler
        controls: CampaignControlService = client.app.state.campaign_controls
        holder = f"schedule:{schedule['id']}"

        lease = controls.acquire_lease(
            first["id"],
            "mind-wake",
            CampaignLeaseAcquire(holder_id=holder, ttl_seconds=600),
        )
        run = scheduler._begin_run(
            schedule["id"],
            holder_id=holder,
            lease_token=lease.lease_token,
            lease_generation=lease.generation,
        )
        wrong = controls.reserve_budget(
            second["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="wrong-campaign-wake",
                units=1,
                ttl_seconds=600,
            ),
        )

        with pytest.raises(IntegrityError):
            scheduler._bind_budget(run.id, wrong.id)

        correct = controls.reserve_budget(
            first["id"],
            CampaignBudgetReserve(
                resource="mind_wakes",
                owner_id=holder,
                idempotency_key="correct-campaign-wake",
                units=1,
                ttl_seconds=600,
            ),
        )
        scheduler._bind_budget(run.id, correct.id)

        ordinary = client.post(
            f"/conversations/{conversation['id']}/turns",
            headers={**AUTH, "Idempotency-Key": "ordinary-turn"},
            json={"text": "This is not the scheduled occurrence."},
        )
        assert ordinary.status_code == 201, ordinary.text
        ordinary_turn_id = ordinary.json()["id"]

        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(
                    text(
                        """
                        UPDATE campaign_schedule_runs
                        SET conversation_turn_id = :turn_id
                        WHERE id = :run_id
                        """
                    ),
                    {"turn_id": ordinary_turn_id, "run_id": run.id},
                )


def test_scheduler_migration_preserves_existing_control_state(settings):
    database = Database(settings.database_path)
    try:
        config = migration_config()
        with database.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0015_campaign_controls")

        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO projects (
                        id, name, summary, status, created_at, updated_at
                    ) VALUES (
                        '10000000-0000-0000-0000-000000000001',
                        'Existing project', '', 'active',
                        '2026-09-26 00:00:00', '2026-09-26 00:00:00'
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO goals (
                        id, project_id, title, spec, status, created_at, updated_at
                    ) VALUES (
                        '20000000-0000-0000-0000-000000000002',
                        '10000000-0000-0000-0000-000000000001',
                        'Existing goal', '{"objective":"preserve"}', 'active',
                        '2026-09-26 00:00:00', '2026-09-26 00:00:00'
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO campaigns (
                        id, goal_id, title, summary, status, created_at, updated_at
                    ) VALUES (
                        '30000000-0000-0000-0000-000000000003',
                        '20000000-0000-0000-0000-000000000002',
                        'Existing campaign', '', 'active',
                        '2026-09-26 00:00:00', '2026-09-26 00:00:00'
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    INSERT INTO campaign_budget_limits (
                        campaign_id, resource, limit_units, created_at, updated_at
                    ) VALUES (
                        '30000000-0000-0000-0000-000000000003',
                        'mind_wakes', 5,
                        '2026-09-26 00:00:00', '2026-09-26 00:00:00'
                    )
                    """
                )
            )

        database.migrate()

        with database.read() as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == "0038_rea_target_operator_selection"
            )
            assert session.scalar(text("SELECT COUNT(*) FROM campaigns")) == 1
            assert session.scalar(text("SELECT COUNT(*) FROM campaign_budget_limits")) == 1
            assert session.scalar(text("SELECT COUNT(*) FROM campaign_schedules")) == 0
            assert session.scalar(text("SELECT COUNT(*) FROM campaign_schedule_continuations")) == 0
            assert session.scalar(text("SELECT COUNT(*) FROM campaign_schedule_runs")) == 0
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        database.close()
