"""Normal Core admits a distinct, exact-pinned delegated Computer pilot."""

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings


def test_pilot_is_disabled_by_default_and_requires_exact_registry_pin(
    settings, database, monkeypatch
):
    from app.computer_observer import delegated_computer_registry

    assert Settings.model_fields["delegated_computer_approved_registry_sha256"].default is None
    _controlled_backend(monkeypatch)
    with TestClient(create_app(settings)) as client:
        assert {s.agent_id for s in client.app.state.agent_registry.list_agents()} == {
            "coding",
            "research",
        }
    selected = _enabled_settings(
        settings,
        computer_observer_approved_registry_sha256=None,
        delegated_computer_approved_registry_sha256=delegated_computer_registry().version,
    )
    from tests.conftest import AUTH

    with TestClient(create_app(selected), headers=AUTH) as client:
        specs = {s.agent_id: s for s in client.app.state.agent_registry.list_agents()}
        assert set(specs) == {"coding", "research", "computer_observer", "computer_delegate"}
        assert client.get("/agents").json()["total"] == 0
        parent = specs["computer_delegate"]
        child = specs["computer_observer"]
        assert parent.allow_subagents and parent.max_subagents == 1
        assert parent.allowed_task_kinds == child.allowed_task_kinds == ("computer_run",)
        assert parent.capabilities == child.capabilities == ("computer_read",)
        assert child.allow_subagents is False
        assert client.app.state.agent_registry.version == delegated_computer_registry().version


def test_pilot_refuses_stale_pin_conflicting_optins_and_missing_backend(
    settings, database, monkeypatch
):
    from app.computer_observer import computer_observer_registry, delegated_computer_registry

    _controlled_backend(monkeypatch)
    delegated_version = delegated_computer_registry().version
    for overrides, reason in (
        ({"delegated_computer_approved_registry_sha256": "0" * 64}, "operator_approval_stale"),
        (
            {
                "delegated_computer_approved_registry_sha256": delegated_version,
                "computer_observer_approved_registry_sha256": computer_observer_registry().version,
            },
            "conflicting",
        ),
    ):
        selected = _enabled_settings(
            settings, **{"computer_observer_approved_registry_sha256": None, **overrides}
        )
        with pytest.raises(ValueError, match=reason):
            with TestClient(create_app(selected)):
                pytest.fail("Normal Core admitted an unsafe pilot selection")
    monkeypatch.undo()
    selected = settings.model_copy(
        update={
            "delegated_computer_approved_registry_sha256": delegated_computer_registry().version
        }
    )
    with pytest.raises(ValueError, match="backend_required"):
        with TestClient(create_app(selected)):
            pytest.fail("Normal Core admitted a Computer pilot without a backend")


def test_pilot_refuses_disabling_with_live_parent(settings, database, monkeypatch):
    from app.computer_observer import delegated_computer_registry
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings,
        computer_observer_approved_registry_sha256=None,
        delegated_computer_approved_registry_sha256=delegated_computer_registry().version,
    )
    with TestClient(create_app(selected), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "computer_delegate")
    with pytest.raises(ValueError, match="disable_requires_retiring_live_instances"):
        with TestClient(create_app(settings)):
            pytest.fail("Disabling the pilot stranded a live manager")
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert client.get(f"/agents/{instance['id']}").json()["id"] == instance["id"]


def test_normal_core_child_uses_own_space_under_live_specialist_delegation(
    settings, database, monkeypatch
):
    from app.computer_observer import delegated_computer_registry
    from app.projects import ProjectService
    from app.schemas import (
        AgentDelegationCreate,
        AgentDelegationRevoke,
        CampaignBudgetReserve,
        CampaignBudgetSet,
        CampaignCreate,
        GoalCreate,
        GoalSpec,
    )
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project
    from tests.test_computer_observer_startup import _finished
    from tests.test_space_owner_provision import provision_body

    backend = _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings,
        computer_observer_approved_registry_sha256=None,
        delegated_computer_approved_registry_sha256=delegated_computer_registry().version,
    )
    with TestClient(create_app(selected), headers=AUTH) as client:
        project = create_project(client, "Delegated Space pilot")
        parent = create_instance(client, project["id"], "computer_delegate")
        child = create_instance(client, project["id"], "computer_observer")
        child_path = f"/agents/{child['id']}"
        resource = client.post(child_path + "/space-resources/provision", json=provision_body())
        assert resource.status_code == 201, resource.text
        assert (
            client.patch(
                child_path + "/computer/permissions",
                json={"enabled": True, "browser": True, "files": True},
            ).status_code
            == 200
        )
        assert client.post(child_path + "/computer/start").status_code == 200
        intent = client.post(
            child_path + "/space-intents",
            json={
                "space_id": "workroom",
                "resource_kind": "files",
                "resource_id": "documents",
                "operations": ["read"],
                "ttl_seconds": 600,
            },
        )
        assert intent.status_code == 201, intent.text
        activated = client.post(
            child_path + f"/space-intents/{intent.json()['id']}/activate",
            json={"resource_record_id": resource.json()["id"]},
        )
        assert activated.status_code == 201, activated.text
        activation_id = activated.json()["id"]
        state = client.app.state
        goal = ProjectService(state.database).create_goal(
            project["id"],
            GoalCreate(title="Child file evidence", spec=GoalSpec(objective="Read approved text")),
        )
        campaign = state.campaigns.create(goal.id, CampaignCreate(title="Bounded child read"))
        state.campaign_controls.set_budget(
            campaign.id, "agent_steps", CampaignBudgetSet(limit_units=10)
        )
        reservation = state.campaign_controls.reserve_budget(
            campaign.id,
            CampaignBudgetReserve(
                resource="agent_steps",
                owner_id=parent["id"],
                idempotency_key="pilot-budget",
                units=1,
                ttl_seconds=600,
            ),
        )
        delegation = state.agent_delegations.create(
            parent["id"],
            AgentDelegationCreate(
                child_instance_id=child["id"],
                campaign_id=campaign.id,
                budget_reservation_id=reservation.id,
                idempotency_key="pilot-delegation",
                objective="Read one approved file",
                allowed_task_kinds=["computer_run"],
                required_capabilities=["computer_read"],
                max_depth=1,
                budget_resource="agent_steps",
                budget_units=1,
            ),
        )
        body = {
            "delegation_id": delegation.id,
            "task": {
                "kind": "computer_run",
                "campaign_id": campaign.id,
                "payload": {
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": activation_id,
                },
            },
        }
        submitted = client.post(child_path + "/tasks", json=body)
        assert submitted.status_code == 201, submitted.text
        done = _finished(client, submitted.json()["id"])
        assert done["status"] == "succeeded", done
        assert done["result"]["files_read"]["text"] == "owner-provided evidence"
        assert all(call[0] != "files_read" for call in backend.calls)
        state.agent_delegations.revoke(delegation.id, AgentDelegationRevoke(reason="Owner revoked"))
        refused = client.post(child_path + "/tasks", json=body)
        assert refused.status_code in (403, 409), refused.text


def test_existing_observer_cannot_switch_catalog_with_live_instance(
    settings, database, monkeypatch
):
    from app.computer_observer import delegated_computer_registry
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    _controlled_backend(monkeypatch)
    with TestClient(create_app(_enabled_settings(settings)), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "computer_observer")
    selected = _enabled_settings(
        settings,
        computer_observer_approved_registry_sha256=None,
        delegated_computer_approved_registry_sha256=delegated_computer_registry().version,
    )
    with pytest.raises(ValueError, match="existing_live_registry_incompatible"):
        with TestClient(create_app(selected)):
            pytest.fail("Registry switch silently rebound a live observer")
    with TestClient(create_app(_enabled_settings(settings)), headers=AUTH) as client:
        assert client.get(f"/agents/{instance['id']}").json() == instance


def test_disabling_pilot_refuses_live_baseline_agent_bound_to_pilot_registry(
    settings, database, monkeypatch
):
    from app.computer_observer import delegated_computer_registry
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings,
        computer_observer_approved_registry_sha256=None,
        delegated_computer_approved_registry_sha256=delegated_computer_registry().version,
    )
    with TestClient(create_app(selected), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "coding")
    with pytest.raises(ValueError, match="disable_requires_retiring_live_instances"):
        with TestClient(create_app(settings)):
            pytest.fail("Disabling the pilot stranded a baseline Agent bound to its registry")
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert client.get(f"/agents/{instance['id']}").json() == instance
