"""Approval, idempotency, concurrency, timeout, cancellation, and recovery tests."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import Approval, Task, utcnow
from app.policy import DomainError, State, validate_transition
from app.schemas import ApprovalDemoInput, EchoInput
from app.tasks import TaskService
from app.workers import execute
from tests.conftest import AUTH
from tests.test_api import submit, wait_for


async def blocked_worker(execution):
    await asyncio.sleep(20)
    return await execute(execution)


def decide(client, approval_id, decision="approve"):
    return client.post(f"/approvals/{approval_id}/decision", json={"decision": decision})


@pytest.mark.parametrize(
    ("previous", "target"),
    [
        (State.PENDING, State.RUNNING),
        (State.RUNNING, State.FAILED),
        (State.INTERRUPTED, State.PENDING),
        (State.AWAITING_APPROVAL, State.PENDING),
    ],
)
def test_valid_transitions(previous, target) -> None:
    validate_transition(previous, target)


@pytest.mark.parametrize(
    ("previous", "target"),
    [
        (State.SUCCEEDED, State.PENDING),
        (State.PENDING, State.SUCCEEDED),
        (State.AWAITING_APPROVAL, State.RUNNING),
        (State.RUNNING, State.RUNNING),
    ],
)
def test_invalid_transitions(previous, target) -> None:
    with pytest.raises(DomainError):
        validate_transition(previous, target)


def test_idempotency_and_conflicts(client: TestClient) -> None:
    body = {"kind": "echo", "payload": {"text": "same"}}
    headers = {"Idempotency-Key": "task-1"}
    first = client.post("/tasks", json=body, headers=headers)
    assert first.status_code == 201
    original = wait_for(client, first.json()["id"], "succeeded")
    # Explicit defaults and field order normalize identically.
    second = client.post("/tasks", json={**body, "timeout_seconds": 10.0}, headers=headers)
    assert second.status_code == 200
    assert second.json()["id"] == original["id"]
    assert len(second.json()["attempts"]) == 1
    for changed in (
        {**body, "payload": {"text": "different"}},
        {**body, "kind": "text_stats"},
        {**body, "timeout_seconds": 2},
    ):
        assert client.post("/tasks", json=changed, headers=headers).status_code == 409
    assert client.get("/tasks").json()["total"] == 1
    assert client.post("/tasks", json=body, headers={"Idempotency-Key": ""}).status_code == 422
    assert (
        client.post("/tasks", json=body, headers={"Idempotency-Key": "x" * 129}).status_code == 422
    )


def test_concurrent_duplicate_submissions(client: TestClient) -> None:
    def send(_):
        return client.post(
            "/tasks",
            json={"kind": "echo", "payload": {"text": "concurrent"}},
            headers={"Idempotency-Key": "concurrent"},
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(send, range(6)))
    assert sorted(response.status_code for response in responses) == [200] * 5 + [201]
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1
    task = wait_for(client, ids.pop(), "succeeded")
    assert len(task["attempts"]) == 1


def test_restart_preserves_result_history_and_idempotency(settings, database) -> None:
    body = {"kind": "echo", "payload": {"text": "persist"}}
    with TestClient(create_app(settings), headers=AUTH) as client:
        response = client.post("/tasks", json=body, headers={"Idempotency-Key": "persistent"})
        task = wait_for(client, response.json()["id"], "succeeded")
        events = client.get(f"/tasks/{task['id']}/events").json()
        gated = submit(client, "approval_demo")
    with TestClient(create_app(settings), headers=AUTH) as client:
        assert client.get(f"/tasks/{task['id']}").json() == task
        assert client.get(f"/tasks/{task['id']}/events").json() == events
        duplicate = client.post("/tasks", json=body, headers={"Idempotency-Key": "persistent"})
        assert duplicate.status_code == 200 and duplicate.json()["id"] == task["id"]
        assert decide(client, gated["approvals"][0]["id"]).status_code == 200
        wait_for(client, gated["id"], "succeeded")


def test_approval_success_duplicate_and_conflicting_decision(client) -> None:
    task = submit(client, "approval_demo")
    assert task["status"] == "awaiting_approval" and task["attempts"] == []
    approval_id = task["approvals"][0]["id"]
    assert decide(client, approval_id).status_code == 200
    task = wait_for(client, task["id"], "succeeded")
    assert task["result"]["approved"] is True
    events_before = client.get(f"/tasks/{task['id']}/events").json()
    assert decide(client, approval_id).status_code == 200
    assert decide(client, approval_id, "reject").status_code == 409
    assert client.get(f"/tasks/{task['id']}/events").json() == events_before
    assert len(client.get(f"/tasks/{task['id']}").json()["attempts"]) == 1


def test_concurrent_duplicate_decisions(client) -> None:
    task = submit(client, "approval_demo")
    approval_id = task["approvals"][0]["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: decide(client, approval_id), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len(wait_for(client, task["id"], "succeeded")["attempts"]) == 1


def test_rejection_and_retry_require_new_approval(client) -> None:
    task = submit(client, "approval_demo")
    old_id = task["approvals"][0]["id"]
    assert decide(client, old_id, "reject").status_code == 200
    rejected = client.get(f"/tasks/{task['id']}").json()
    assert rejected["status"] == "cancelled" and rejected["attempts"] == []
    assert rejected["error"] == "approval_rejected"
    assert decide(client, old_id, "reject").status_code == 200
    retried = client.post(f"/tasks/{task['id']}/retry").json()
    assert retried["status"] == "awaiting_approval"
    assert retried["action_version"] == 2
    assert decide(client, old_id).status_code == 409
    assert decide(client, retried["approvals"][-1]["id"]).status_code == 200
    wait_for(client, task["id"], "succeeded")


def test_expiration(client, database) -> None:
    task = submit(client, "approval_demo")
    approval_id = task["approvals"][0]["id"]
    with database.transaction() as session:
        session.get(Approval, approval_id).expires_at = utcnow() - timedelta(seconds=1)
    assert decide(client, approval_id).status_code == 409
    expired = wait_for(client, task["id"], "cancelled")
    assert expired["error"] == "approval_expired"
    assert expired["approvals"][0]["status"] == "expired"
    assert expired["attempts"] == []
    assert "approval_expired" in [
        e["event"] for e in client.get(f"/tasks/{task['id']}/events").json()
    ]


def test_expiration_without_decision(settings, database) -> None:
    settings = settings.model_copy(update={"approval_ttl_seconds": 0.1})
    with TestClient(create_app(settings), headers=AUTH) as client:
        task = submit(client, "approval_demo")
        assert wait_for(client, task["id"], "cancelled")["error"] == "approval_expired"


def test_changed_input_invalidates_approval(client, database) -> None:
    task = submit(client, "approval_demo")
    with database.transaction() as session:
        session.get(Task, task["id"]).payload = {"text": "changed outside API"}
    assert decide(client, task["approvals"][0]["id"]).status_code == 409
    task = wait_for(client, task["id"], "cancelled")
    assert task["error"] == "approval_invalidated" and task["attempts"] == []


def test_pending_cancellation_and_bounded_capacity(settings, database) -> None:
    settings = settings.model_copy(update={"max_waiting_tasks": 1})
    with TestClient(create_app(settings, worker=blocked_worker), headers=AUTH) as client:
        active = submit(client)
        wait_for(client, active["id"], "running")
        pending = submit(client)
        response = client.post("/tasks", json={"kind": "echo", "payload": {"text": "full"}})
        assert response.status_code == 503
        response = client.post(f"/tasks/{pending['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled" and response.json()["attempts"] == []
        assert client.post(f"/tasks/{pending['id']}/cancel").status_code == 200
        assert client.post(f"/tasks/{active['id']}/cancel").status_code == 202
        cancelled = wait_for(client, active["id"], "cancelled")
        assert cancelled["attempts"][0]["cancellation_requested_at"] is not None


def test_cancel_approval_wait(client) -> None:
    task = submit(client, "approval_demo")
    cancelled = client.post(f"/tasks/{task['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["approvals"][0]["status"] == "cancelled"
    assert decide(client, task["approvals"][0]["id"]).status_code == 409


def test_timeout_and_retry_preserve_attempts(settings, database) -> None:
    calls = 0

    async def once_slow(execution):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(20)
        return await execute(execution)

    with TestClient(create_app(settings, worker=once_slow), headers=AUTH) as client:
        task = submit(client, timeout_seconds=0.03)
        failed = wait_for(client, task["id"], "failed")
        assert failed["error"] == "execution_timeout"
        assert client.post(f"/tasks/{task['id']}/retry").status_code == 202
        completed = wait_for(client, task["id"], "succeeded")
        assert [a["number"] for a in completed["attempts"]] == [1, 2]
        assert completed["attempts"][0] == failed["attempts"][0]


def test_interruption_recovery_does_not_replay(settings: Settings, database: Database) -> None:
    service = TaskService(database, settings)
    task, _ = service.submit(EchoInput(kind="echo", payload={"text": "interrupted"}), None)
    assert service.claim() is not None
    # Simulate records left behind by process death; no runner is alive.
    with TestClient(create_app(settings), headers=AUTH) as client:
        recovered = client.get(f"/tasks/{task.id}").json()
        assert recovered["status"] == "interrupted"
        assert recovered["attempts"][0]["outcome"] == "interrupted"
        time.sleep(0.05)
        assert len(client.get(f"/tasks/{task.id}").json()["attempts"]) == 1
        assert client.post(f"/tasks/{task.id}/retry").status_code == 202
        completed = wait_for(client, task.id, "succeeded")
        assert [a["number"] for a in completed["attempts"]] == [1, 2]
        events = client.get(f"/tasks/{task.id}/events").json()
        assert sum(e["event"] == "startup_interruption_recovery" for e in events) == 1


def test_clean_shutdown_interrupts_active_work(settings, database) -> None:
    with TestClient(create_app(settings, worker=blocked_worker), headers=AUTH) as client:
        task = submit(client)
        wait_for(client, task["id"], "running")
    with TestClient(create_app(settings), headers=AUTH) as client:
        recovered = client.get(f"/tasks/{task['id']}").json()
        assert recovered["status"] == "interrupted"
        assert recovered["attempts"][0]["error"] == "service_shutdown"


def test_one_active_attempt(settings, database) -> None:
    active = maximum = 0

    async def observed(execution):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.03)
            return await execute(execution)
        finally:
            active -= 1

    with TestClient(create_app(settings, worker=observed), headers=AUTH) as client:
        tasks = [submit(client) for _ in range(5)]
        for task in tasks:
            wait_for(client, task["id"], "succeeded")
        assert maximum == 1


def test_second_application_cannot_recover_live_runner(settings, database) -> None:
    with TestClient(create_app(settings, worker=blocked_worker), headers=AUTH) as first:
        task = submit(first)
        wait_for(first, task["id"], "running")
        with pytest.raises(RuntimeError, match="ONE Uvicorn worker"):
            with TestClient(create_app(settings)):
                pass
        assert first.get(f"/tasks/{task['id']}").json()["status"] == "running"


def test_worker_failure_is_redacted(settings, database) -> None:
    async def failing(execution):
        raise RuntimeError("private task text or secret")

    with TestClient(create_app(settings, worker=failing), headers=AUTH) as client:
        task = wait_for(client, submit(client)["id"], "failed")
        assert task["error"] == "worker_error"
        assert "private task" not in str(task)


def test_approved_input_revalidated_at_claim(settings, database) -> None:
    service = TaskService(database, settings)
    task, _ = service.submit(ApprovalDemoInput(kind="approval_demo", payload={"text": "x"}), None)
    service.decide(task.approvals[0].id, "approve")
    with database.transaction() as session:
        session.get(Task, task.id).action_version += 1
    assert service.claim() is None
    assert service.get(task.id).status == "cancelled"
    with database.read() as session:
        assert session.scalar(select(Approval.status)) == "invalidated"
