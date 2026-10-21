"""The fixed read-only Manager/Specialist team is a separate exact opt-in."""

import pytest
from fastapi.testclient import TestClient

from app import computer_observer
from app.application import create_app
from tests.conftest import AUTH
from tests.test_agent_instances import create_instance, create_project
from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings


def test_existing_single_observer_registry_is_not_silently_expanded():
    registry = computer_observer.computer_observer_registry()
    assert {spec.agent_id for spec in registry.list_agents()} == {
        "coding",
        "research",
        "computer_observer",
    }
    assert registry.version == "5b11f0f79d53b4eb51432a3b18ada6165e0bf0f37b47cfca71d6df7ac91b35bc"
    assert hasattr(computer_observer, "computer_read_team_registry"), "Missing explicit team opt-in"
    team = computer_observer.computer_read_team_registry()
    assert team.version != registry.version
    assert team.get_agent("computer_observer") == registry.get_agent("computer_observer")
    manager = team.get_agent("space_read_manager")
    assert manager.allow_subagents and manager.max_subagents == 1
    assert manager.capabilities == ("computer_read",)
    assert manager.allowed_task_kinds == ("computer_run",)
    assert not manager.allowed_tools and not manager.computer.shell


def test_team_normal_startup_and_restart_require_exact_separate_pin(
    settings, database, monkeypatch
):
    assert hasattr(computer_observer, "computer_read_team_registry"), "Missing explicit team opt-in"
    registry = computer_observer.computer_read_team_registry()
    backend = _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings, computer_observer_approved_registry_sha256=registry.version
    )
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert client.app.state.agent_registry.version == registry.version
        assert client.get("/agents").json()["total"] == 0
        project = create_project(client)
        parent = create_instance(client, project["id"], "space_read_manager")
        child = create_instance(client, project["id"], "computer_observer")
        assert not backend.calls
        for agent in (parent, child):
            assert not client.get(f"/agents/{agent['id']}/computer").json()["permissions"][
                "enabled"
            ]
        assert client.get("/tasks").json()["total"] == 0
        assert backend.calls == [("existing", parent["id"]), ("existing", child["id"])]
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert client.get(f"/agents/{parent['id']}").json()["registry_version"] == registry.version
    with pytest.raises(ValueError, match="existing_live_registry_incompatible"):
        with TestClient(create_app(_enabled_settings(settings))):
            pytest.fail("Single-observer selection silently rebound Team identities")
    with pytest.raises(ValueError, match="disable_requires_retiring_live_instances"):
        with TestClient(create_app(settings)):
            pytest.fail("Default startup silently discarded live Team bindings")


def test_normal_core_http_runner_reads_dual_approved_space_then_honors_parent_takeover(
    settings, database, monkeypatch
):
    from tests.test_agent_delegations import create_project_goal_campaign
    from tests.test_computer_observer_startup import _finished
    from tests.test_space_approval_discovery import _approval

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings,
        computer_observer_approved_registry_sha256=computer_observer.computer_read_team_registry().version,
    )
    with TestClient(create_app(selected), headers=AUTH) as client:
        project, _goal, campaign = create_project_goal_campaign(client, "Normal dual-approved Core")
        parent = create_instance(client, project["id"], "space_read_manager")
        child = create_instance(client, project["id"], "computer_observer")
        for agent in (parent, child):
            base = f"/agents/{agent['id']}/computer"
            assert (
                client.patch(
                    base + "/permissions", json={"enabled": True, "files": True, "browser": True}
                ).status_code
                == 200
            )
            assert client.post(base + "/start").status_code == 200
        resource, _intent, parent_approval = _approval(client, f"/agents/{parent['id']}")
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
        assert intent.status_code == 201
        child_approval = client.post(
            f"/agents/{child['id']}/space-intents/{intent.json()['id']}/activate",
            json={"resource_record_id": resource["id"]},
        )
        assert child_approval.status_code == 201
        assert (
            client.put(
                f"/campaigns/{campaign['id']}/budgets/agent_steps", json={"limit_units": 10}
            ).status_code
            == 200
        )
        budget = client.post(
            f"/campaigns/{campaign['id']}/budget-reservations",
            json={
                "resource": "agent_steps",
                "owner_id": parent["id"],
                "idempotency_key": "http-budget",
                "units": 3,
                "ttl_seconds": 600,
            },
        )
        assert budget.status_code == 201, budget.text
        delegated = client.post(
            f"/agents/{parent['id']}/delegations",
            json={
                "child_instance_id": child["id"],
                "campaign_id": campaign["id"],
                "budget_reservation_id": budget.json()["id"],
                "idempotency_key": "http-delegation",
                "objective": "Read approved file",
                "allowed_task_kinds": ["computer_run"],
                "required_capabilities": ["computer_read"],
                "max_depth": 1,
                "budget_resource": "agent_steps",
                "budget_units": 3,
            },
        )
        assert delegated.status_code == 201, delegated.text
        body = {
            "delegation_id": delegated.json()["id"],
            "task": {
                "kind": "computer_run",
                "campaign_id": campaign["id"],
                "payload": {
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": child_approval.json()["id"],
                    "parent_space_activation_id": parent_approval["id"],
                },
            },
        }
        task = client.post(f"/agents/{child['id']}/tasks", json=body)
        assert task.status_code == 201, task.text
        done = _finished(client, task.json()["id"])
        assert done["status"] == "succeeded", done
        assert done["result"]["files_read"]["text"] == "owner-provided evidence"
        assert client.post(f"/agents/{parent['id']}/computer/take").status_code == 200
        before = client.get("/tasks").json()["total"]
        assert client.post(f"/agents/{child['id']}/tasks", json=body).status_code == 409
        assert client.get("/tasks").json()["total"] == before
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert client.post(f"/agents/{child['id']}/tasks", json=body).status_code == 409
        assert client.get(f"/tasks/{done['id']}").json()["result"] == done["result"]
        assert (
            client.post(
                f"/agents/delegations/{delegated.json()['id']}/revoke",
                json={"reason": "Explicit operator withdrawal"},
            ).status_code
            == 200
        )


def test_read_team_preview_is_offline_and_separate_from_default_selection():
    import json
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "-m", "scripts.preview_space_read_team"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    view = json.loads(result.stdout)
    assert (
        view["approved_registry_sha256"] == computer_observer.computer_read_team_registry().version
    )
    assert not view["runtime_started"] and not view["permissions_granted"]
    assert {spec["agent_id"] for spec in view["candidate_specs"]} == {
        "space_read_manager",
        "computer_observer",
    }
