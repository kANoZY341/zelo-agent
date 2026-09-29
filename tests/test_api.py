"""HTTP contracts, operator protection, validation, and deterministic execution."""

import inspect
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.task_api import decide_approval
from tests.conftest import AUTH
from tests.developer_helpers import FakeWorker


def submit(client: TestClient, kind: str = "echo", **overrides) -> dict:
    body = {"kind": kind, "payload": {"text": "hello world\nsecond line"}, **overrides}
    response = client.post("/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def wait_for(client: TestClient, task_id: str, expected: str, timeout: float = 3) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] == expected:
            return task
        time.sleep(0.01)
    pytest.fail(f"Task did not reach {expected}: {task}")


def test_approval_route_is_synchronous_threadpool_boundary() -> None:
    assert not inspect.iscoroutinefunction(decide_approval)


def test_developer_approval_succeeds_through_asgi_threadpool(
    settings: Settings, database: Database, monkeypatch
) -> None:
    configured = settings.model_copy(update={"runner_poll_seconds": 1.0})
    with TestClient(
        create_app(configured, developer_worker=FakeWorker()),
        headers=AUTH,
    ) as client:
        runtime = client.app.state.tasks.developer_runtime
        monkeypatch.setattr(runtime, "validation_identity", lambda: "test-validation-identity")

        created = client.post(
            "/tasks",
            json={
                "kind": "developer_run",
                "payload": {
                    "instruction": "Verify the approval route threadpool boundary",
                    "validation_profile": "zelo_core",
                },
                "timeout_seconds": 30,
            },
        )
        assert created.status_code == 201, created.text
        task = created.json()
        approval_id = task["approvals"][0]["id"]

        approved = client.post(
            f"/approvals/{approval_id}/decision",
            json={"decision": "approve"},
        )

        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"


def test_root_and_health_are_public(client: TestClient) -> None:
    client.headers.pop("Authorization")
    assert client.get("/").json() == {"name": "Zelo Agent", "version": "0.13.0", "status": "online"}
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_environment_name(settings: Settings, database: Database, monkeypatch) -> None:
    monkeypatch.setenv("ZELO_APP_NAME", "Zelo Test")
    configured = Settings(
        _env_file=None, operator_token=settings.operator_token, runtime_dir=settings.runtime_dir
    )
    with TestClient(create_app(configured)) as client:
        assert client.get("/").json()["name"] == "Zelo Test"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/tasks"),
        ("GET", "/tasks"),
        ("GET", "/tasks/missing"),
        ("POST", "/tasks/missing/cancel"),
        ("POST", "/tasks/missing/retry"),
        ("GET", "/tasks/missing/events"),
        ("POST", "/approvals/missing/decision"),
    ],
)
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic dGVzdA=="])
def test_authentication(client: TestClient, method: str, path: str, authorization) -> None:
    client.headers.pop("Authorization")
    headers = {"Authorization": authorization} if authorization else {}
    response = client.request(method, path, headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_missing_token_fails_closed(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="ZELO_OPERATOR_TOKEN"):
        with TestClient(create_app(settings.model_copy(update={"operator_token": None}))):
            pass


@pytest.mark.parametrize(
    "token", ["short", "REPLACE_WITH_A_RANDOM_LOCAL_OPERATOR_TOKEN", "x " * 32]
)
def test_invalid_token_configuration(token: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, operator_token=token)


def test_runtime_must_stay_in_runtime_directory() -> None:
    with pytest.raises(ValueError, match="runtime"):
        Settings(_env_file=None, runtime_dir="memory")


@pytest.mark.parametrize(
    ("kind", "text", "result"),
    [
        ("echo", "hello", {"text": "hello"}),
        ("text_stats", "hello world\nsecond line", {"characters": 23, "words": 4, "lines": 2}),
        ("text_stats", "", {"characters": 0, "words": 0, "lines": 0}),
        ("text_stats", "é 🙂\n", {"characters": 4, "words": 2, "lines": 1}),
    ],
)
def test_deterministic_execution(client: TestClient, kind: str, text: str, result: dict) -> None:
    task = submit(client, kind, payload={"text": text})
    task = wait_for(client, task["id"], "succeeded")
    assert task["result"] == result
    assert len(task["attempts"]) == 1
    attempt = task["attempts"][0]
    assert attempt["number"] == 1 and attempt["outcome"] == "succeeded"
    assert attempt["ended_at"] >= attempt["started_at"]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "shell", "payload": {"text": "ls"}},
        {"kind": "echo", "payload": {"text": "x", "command": "ls"}},
        {"kind": "echo", "payload": {"text": 123}},
        {"kind": "echo", "payload": {"text": "x" * 65537}},
        {"kind": "echo", "payload": {"text": "x"}, "timeout_seconds": 0},
        {"kind": "echo", "payload": {"text": "x"}, "status": "succeeded"},
    ],
)
def test_invalid_task_inputs(client: TestClient, body: dict) -> None:
    assert client.post("/tasks", json=body).status_code == 422
    assert client.get("/tasks").json()["total"] == 0


def test_listing_pagination_and_not_found(client: TestClient) -> None:
    created = [submit(client, "approval_demo") for _ in range(3)]
    page = client.get("/tasks?limit=1&offset=1").json()
    assert page["total"] == 3 and page["limit"] == 1 and page["offset"] == 1
    assert [task["id"] for task in page["items"]] == [created[1]["id"]]
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(f"/tasks?{query}").status_code == 422
    assert client.get("/tasks/missing").status_code == 404
    assert client.get("/tasks/missing/events").status_code == 404
    assert (
        client.post("/approvals/missing/decision", json={"decision": "approve"}).status_code == 404
    )


def test_health_fails_without_leaking_configuration(client: TestClient, monkeypatch) -> None:
    def unavailable() -> None:
        raise RuntimeError("private path or credential")

    monkeypatch.setattr(client.app.state.database, "check_schema", unavailable)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_cannot_retry_or_cancel_success(client: TestClient) -> None:
    task = wait_for(client, submit(client)["id"], "succeeded")
    assert client.post(f"/tasks/{task['id']}/retry").status_code == 409
    assert client.post(f"/tasks/{task['id']}/cancel").status_code == 409
    assert len(client.get(f"/tasks/{task['id']}").json()["attempts"]) == 1


def test_openapi_contract(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/tasks"]["post"]["security"]
    assert "security" not in spec["paths"]["/health"]["get"]
    assert client.get("/tasks", headers=AUTH).status_code == 200
