"""The disposable live-Core qualifier must bind and release its own loopback socket."""

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.application import create_app
from app.computer_observer import computer_read_team_registry
from app.schemas import AgentTaskCreate
from scripts import qualify_two_computer_space as qualifier
from tests.conftest import AUTH
from tests.test_agent_delegations import create_project_goal_campaign
from tests.test_agent_instances import create_instance
from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings
from tests.test_space_approval_discovery import _approval


def test_live_core_server_uses_tcp_loopback_and_releases_port() -> None:
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    with qualifier.loopback_core_server(app) as base_url:
        assert base_url.startswith("http://127.0.0.1:")
        response = httpx.get(base_url + "/health", timeout=2, trust_env=False)
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    with pytest.raises(httpx.ConnectError):
        httpx.get(base_url + "/health", timeout=2, trust_env=False)


def test_http_qualification_rejects_substitution_and_revoked_work() -> None:
    app = FastAPI()
    state: dict[str, object] = {
        "tasks": {},
        "revoked": False,
        "revoked_activations": set(),
        "refusals": 0,
    }

    def authorized(request: Request) -> None:
        if request.headers.get("authorization") != "Bearer test-token":
            raise HTTPException(401)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/tasks")
    def list_tasks(request: Request) -> dict[str, int]:
        authorized(request)
        return {"total": len(state["tasks"])}

    @app.post("/agents/child/tasks")
    async def submit(request: Request) -> JSONResponse:
        authorized(request)
        body = await request.json()
        payload = body["task"]["payload"]
        if (
            state["revoked"]
            or payload["space_activation_id"] == "parent"
            or payload["space_activation_id"] in state["revoked_activations"]
        ):
            state["refusals"] += 1
            reason = (
                "Agent delegation is not active"
                if state["revoked"]
                else "computer_space_activation_invalid"
            )
            raise HTTPException(409, reason)
        task_id = str(len(state["tasks"]) + 1)
        if payload["action"] == "files_read":
            result = {"files_read": {"text": "private fixture"}}
        else:
            result = {"files_list": {"entries": [{"path": "notes.txt"}]}}
        state["tasks"][task_id] = {
            "id": task_id,
            "status": "succeeded",
            "result": result,
            "action": payload["action"],
        }
        return JSONResponse(status_code=201, content=state["tasks"][task_id])

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, request: Request) -> dict:
        authorized(request)
        return state["tasks"][task_id]

    @app.get("/tasks/{task_id}/events")
    def task_events(task_id: str, request: Request) -> list[dict[str, str]]:
        authorized(request)
        action = state["tasks"][task_id]["action"]
        event = (
            "space_file_read_completed" if action == "files_read" else "space_file_list_completed"
        )
        return [{"event": event}]

    @app.post("/agents/delegations/delegation/revoke")
    def revoke(request: Request) -> dict[str, bool]:
        authorized(request)
        state["revoked"] = True
        return {"revoked": True}

    @app.post("/agents/child/space-activations/{activation_id}/revoke")
    def revoke_activation(activation_id: str, request: Request) -> dict[str, bool]:
        authorized(request)
        state["revoked_activations"].add(activation_id)
        return {"eligible_for_mediation": False}

    def body(action: str, activation: str) -> dict:
        return {"task": {"payload": {"action": action, "space_activation_id": activation}}}

    with qualifier.loopback_core_server(app) as base_url:
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": "Bearer test-token"},
            timeout=2,
            trust_env=False,
        ) as client:
            qualifier.qualify_http(
                client,
                child_id="child",
                delegation_id="delegation",
                read_body=body("files_read", "child"),
                list_body=body("files_list", "child"),
                substituted_body=body("files_read", "parent"),
                revocable_body=body("files_read", "revocable-child"),
                revocable_activation_id="revocable-child",
                expected_text="private fixture",
            )

    assert len(state["tasks"]) == 2
    assert state["refusals"] == 3
    assert state["revoked"] is True
    assert state["revoked_activations"] == {"revocable-child"}


def test_normal_core_tcp_runs_dual_approved_space_and_refuses_revocation(
    settings, database, monkeypatch
) -> None:
    _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings, computer_observer_approved_registry_sha256=computer_read_team_registry().version
    )
    with qualifier.loopback_core_server(create_app(selected)) as base_url:
        with httpx.Client(base_url=base_url, headers=AUTH, timeout=10, trust_env=False) as client:
            project, _goal, campaign = create_project_goal_campaign(client, "TCP Space proof")
            parent = create_instance(client, project["id"], "space_read_manager")
            child = create_instance(client, project["id"], "computer_observer")
            for instance in (parent, child):
                base = f"/agents/{instance['id']}/computer"
                permissions = client.patch(
                    base + "/permissions",
                    json={"enabled": True, "files": True, "browser": True},
                )
                assert permissions.status_code == 200, permissions.text
                started = client.post(base + "/start")
                assert started.status_code == 200, started.text
            resource, _intent, parent_activation = _approval(client, f"/agents/{parent['id']}")
            intent = client.post(
                f"/agents/{child['id']}/space-intents",
                json={
                    "space_id": "workroom",
                    "resource_kind": "files",
                    "resource_id": "documents",
                    "operations": ["list", "read"],
                    "ttl_seconds": 600,
                },
            )
            assert intent.status_code == 201, intent.text
            activation = client.post(
                f"/agents/{child['id']}/space-intents/{intent.json()['id']}/activate",
                json={"resource_record_id": resource["id"]},
            )
            assert activation.status_code == 201, activation.text
            revocable_intent = client.post(
                f"/agents/{child['id']}/space-intents",
                json={
                    "space_id": "workroom",
                    "resource_kind": "files",
                    "resource_id": "documents",
                    "operations": ["list", "read"],
                    "ttl_seconds": 600,
                },
            )
            assert revocable_intent.status_code == 201, revocable_intent.text
            revocable_activation = client.post(
                f"/agents/{child['id']}/space-intents/{revocable_intent.json()['id']}/activate",
                json={"resource_record_id": resource["id"]},
            )
            assert revocable_activation.status_code == 201, revocable_activation.text
            assert revocable_activation.json()["id"] != activation.json()["id"]
            wrong_intent = client.post(
                f"/agents/{parent['id']}/space-intents",
                json={
                    "space_id": "workroom",
                    "resource_kind": "files",
                    "resource_id": "documents",
                    "operations": ["list", "read"],
                    "ttl_seconds": 600,
                },
            )
            assert wrong_intent.status_code == 201, wrong_intent.text
            wrong_activation = client.post(
                f"/agents/{parent['id']}/space-intents/{wrong_intent.json()['id']}/activate",
                json={"resource_record_id": resource["id"]},
            )
            assert wrong_activation.status_code == 201, wrong_activation.text
            budget_limit = client.put(
                f"/campaigns/{campaign['id']}/budgets/agent_steps", json={"limit_units": 10}
            )
            assert budget_limit.status_code == 200, budget_limit.text
            budget = client.post(
                f"/campaigns/{campaign['id']}/budget-reservations",
                json={
                    "resource": "agent_steps",
                    "owner_id": parent["id"],
                    "idempotency_key": "tcp-budget",
                    "units": 5,
                    "ttl_seconds": 600,
                },
            )
            assert budget.status_code == 201, budget.text
            delegation = client.post(
                f"/agents/{parent['id']}/delegations",
                json={
                    "child_instance_id": child["id"],
                    "campaign_id": campaign["id"],
                    "budget_reservation_id": budget.json()["id"],
                    "idempotency_key": "tcp-delegation",
                    "objective": "Read approved file",
                    "allowed_task_kinds": ["computer_run"],
                    "required_capabilities": ["computer_read"],
                    "max_depth": 1,
                    "budget_resource": "agent_steps",
                    "budget_units": 5,
                },
            )
            assert delegation.status_code == 201, delegation.text

            def body(action: str) -> dict[str, object]:
                return AgentTaskCreate.model_validate(
                    {
                        "delegation_id": delegation.json()["id"],
                        "task": {
                            "kind": "computer_run",
                            "campaign_id": campaign["id"],
                            "payload": {
                                "action": action,
                                "path": "notes.txt" if action == "files_read" else "",
                                "space_activation_id": activation.json()["id"],
                                "parent_space_activation_id": parent_activation["id"],
                            },
                        },
                    }
                ).model_dump(mode="json")

            read_body = body("files_read")
            substituted_body = body("files_read")
            substituted_body["task"]["payload"]["space_activation_id"] = parent_activation["id"]
            substituted_body["task"]["payload"]["parent_space_activation_id"] = (
                wrong_activation.json()["id"]
            )
            revocable_body = body("files_read")
            revocable_body["task"]["payload"]["space_activation_id"] = revocable_activation.json()[
                "id"
            ]
            qualifier.qualify_http(
                client,
                child_id=child["id"],
                delegation_id=delegation.json()["id"],
                read_body=read_body,
                list_body=body("files_list"),
                substituted_body=substituted_body,
                revocable_body=revocable_body,
                revocable_activation_id=revocable_activation.json()["id"],
                expected_text="owner-provided evidence",
            )
            assert client.get("/tasks").json()["total"] == 2
