"""Operator-selected Computer Specialist through the normal Core lifespan."""

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_settings_exposes_explicit_computer_registry_optin():
    assert "computer_observer_approved_registry_sha256" in Settings.model_fields
    assert Settings.model_fields["computer_observer_approved_registry_sha256"].default is None


def test_unrecognized_operator_pin_refuses_normal_startup(settings, database):
    selected = settings.model_copy(update={"computer_observer_approved_registry_sha256": "0" * 64})
    with pytest.raises(ValueError, match="computer_observer_operator_approval_stale"):
        with TestClient(create_app(selected)):
            pytest.fail("Normal Core accepted an unrecognized Computer startup approval")


def _enabled_settings(settings, **overrides):
    from app.computer_observer import computer_observer_registry

    values = settings.model_dump()
    values.update(
        computer_observer_approved_registry_sha256=computer_observer_registry().version,
        computer_supervisor_url="http://127.0.0.1:43111",
        computer_supervisor_token="synthetic-supervisor-token-for-tests-only",
        computer_token="synthetic-computer-token-for-tests-only",
        scheduler_enabled=False,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _controlled_backend(monkeypatch):
    from tests.test_agent_computer_service import FakeBackend
    from tests.test_computer_worker import FakeReadBackend

    class LifecycleBackend(FakeBackend):
        identity = "a" * 64

        async def read_action(self, instance_id, action, *, path=""):
            self.calls.append((action, instance_id))
            return FakeReadBackend().responses[action]

    backend = LifecycleBackend()
    monkeypatch.setattr("app.application.OpenBotComputerBackend", lambda _settings: backend)
    return backend


def _finished(client, task_id):
    import time

    until = time.monotonic() + 5
    while time.monotonic() < until:
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        result = response.json()
        if result["status"] in ("succeeded", "failed", "cancelled"):
            return result
        time.sleep(0.01)
    pytest.fail("Normal Core Runner did not finish the Computer Task")


def test_optin_normal_lifespan_creates_and_runs_owner_granted_computer(
    settings, database, monkeypatch
):
    from app.computer_observer import computer_observer_registry
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    backend = _controlled_backend(monkeypatch)
    selected = _enabled_settings(settings)
    with TestClient(create_app(selected), headers=AUTH) as client:
        state = client.app.state
        assert state.agent_registry.version == computer_observer_registry().version
        assert state.agent_activation_report.status == "accepted"
        assert {s.agent_id for s in state.agent_registry.list_agents()} == {
            "coding",
            "research",
            "computer_observer",
        }
        assert client.get("/agents").json()["total"] == 0
        project = create_project(client)
        instance = create_instance(client, project["id"], "computer_observer")
        base = f"/agents/{instance['id']}/computer"
        status = client.get(base).json()
        assert not status["permissions"]["enabled"]
        assert client.post(base + "/start").status_code == 409
        assert (
            client.post(base + "/start", headers={"Authorization": "Bearer invalid"}).status_code
            == 401
        )
        body = {"task": {"kind": "computer_run", "payload": {"action": "files_list"}}}
        assert client.post(f"/agents/{instance['id']}/tasks", json=body).status_code == 422
        assert client.get("/tasks").json()["total"] == 0
        assert (
            client.patch(
                base + "/permissions", json={"enabled": True, "browser": True, "files": True}
            ).status_code
            == 200
        )
        assert client.patch(base + "/permissions", json={"shell": True}).status_code == 409
        assert client.post(base + "/start").json()["state"] == "running"
        submitted = client.post(f"/agents/{instance['id']}/tasks", json=body)
        assert submitted.status_code == 201, submitted.text
        done = _finished(client, submitted.json()["id"])
        assert done["status"] == "succeeded", done
        assert done["result"]["files_list"]["entries"][0]["path"] == "notes.txt"
        assert ("files_list", instance["id"]) in backend.calls
        assert client.post(base + "/take").status_code == 200
        before = client.get("/tasks").json()["total"]
        refused = client.post(f"/agents/{instance['id']}/tasks", json=body)
        assert refused.status_code == 409
        assert refused.json()["detail"] == "computer_human_control_active"
        assert client.get("/tasks").json()["total"] == before
        assert client.post(base + "/release").status_code == 200
        resumed = client.post(f"/agents/{instance['id']}/tasks", json=body)
        assert resumed.status_code == 201
        assert _finished(client, resumed.json()["id"])["status"] == "succeeded"
        assert client.post(base + "/stop").json()["state"] == "stopped"

    # Same approved registry survives restart without rebinding the stored Agent.
    with TestClient(create_app(selected), headers=AUTH) as client:
        persisted = client.get(f"/agents/{instance['id']}").json()
        assert persisted["registry_version"] == instance["registry_version"]
        assert client.get(f"/tasks/{done['id']}").json()["result"] == done["result"]
    with pytest.raises(ValueError, match="disable_requires_retiring_live_instances"):
        with TestClient(create_app(settings)):
            pytest.fail("Disabling the opt-in silently stranded live Agent bindings")


def test_exact_pin_without_backend_refuses(settings, database):
    from app.computer_observer import computer_observer_registry

    selected = settings.model_copy(
        update={"computer_observer_approved_registry_sha256": computer_observer_registry().version}
    )
    with pytest.raises(ValueError, match="computer_observer_backend_required"):
        with TestClient(create_app(selected)):
            pytest.fail("Computer Specialist admitted with no configured backend")


def test_enabling_refuses_existing_baseline_agent_without_mutation(settings, database, monkeypatch):
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    with TestClient(create_app(settings), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "research")
    _controlled_backend(monkeypatch)
    with pytest.raises(ValueError, match="existing_live_registry_incompatible"):
        with TestClient(create_app(_enabled_settings(settings))):
            pytest.fail("Computer opt-in silently invalidated the baseline Agent")
    with TestClient(create_app(settings), headers=AUTH) as client:
        assert client.get(f"/agents/{instance['id']}").json() == instance


def test_readonly_internal_computer_opens_no_navigation_proxy(settings, database, monkeypatch):
    from tests.conftest import AUTH

    _controlled_backend(monkeypatch)

    def no_proxy(*args, **kwargs):
        pytest.fail("Read-only Computer startup opened an unnecessary network listener")

    monkeypatch.setattr("app.application.ComputerEgressProxy", no_proxy)
    selected = _enabled_settings(
        settings,
        computer_internal_network_cidr="172.30.254.0/24",
        computer_egress_proxy_host="172.30.254.1",
    )
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert client.app.state.computer_egress_proxy is None
        assert not client.app.state.computer_browser_endpoint_registry.list()
        assert all(
            "computer_navigate" not in item.capabilities
            for item in client.app.state.computer_worker_registry.list()
        )


def test_registry_selection_preserves_other_agents_and_only_known_read_permissions():
    from app.agent_factory import default_agent_registry
    from app.computer_observer import computer_observer_registry, computer_observer_spec

    baseline = default_agent_registry()
    candidate = computer_observer_registry()
    spec = computer_observer_spec()
    assert candidate.version != baseline.version
    for existing in baseline.list_agents():
        assert candidate.get_agent(existing.agent_id) == existing
    assert candidate.list_departments() == baseline.list_departments()
    assert spec.capabilities == ("computer_read",)
    assert spec.allowed_task_kinds == ("computer_run",)
    assert spec.skill_ids == ("computer_observation",)
    assert spec.space_permissions == ("workroom",)
    assert not spec.allowed_tools and not spec.permissions
    assert not spec.computer.shell and not spec.computer.browser_endpoint_ids
    assert not spec.allow_subagents and spec.skill_learning == "disabled"


def test_configured_backend_alone_does_not_admit_specialist(settings, database, monkeypatch):
    from app.agent_factory import default_agent_registry

    _controlled_backend(monkeypatch)
    with TestClient(create_app(settings)) as client:
        assert client.app.state.agent_registry.version == default_agent_registry().version
        assert client.app.state.agent_registry.get_agent("computer_observer") is None


def test_computer_and_future_company_optins_cannot_silently_combine(
    settings, database, monkeypatch
):
    from app.youtube_research_specialist import youtube_research_spec_digest

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(
        settings, youtube_research_approved_spec_sha256=youtube_research_spec_digest()
    )
    with pytest.raises(ValueError, match="computer_observer_other_optins_incompatible"):
        with TestClient(create_app(selected)):
            pytest.fail("Independent registry approvals were silently combined")


def test_disable_requires_retiring_all_live_bindings_but_preserves_history(
    settings, database, monkeypatch
):
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(settings)
    with TestClient(create_app(selected), headers=AUTH) as client:
        project = create_project(client)
        observer = create_instance(client, project["id"], "computer_observer")
        research = create_instance(client, project["id"], "research")
        assert (
            client.post(
                f"/agents/{observer['id']}/transition", json={"action": "retire"}
            ).status_code
            == 200
        )
    with pytest.raises(ValueError, match="disable_requires_retiring_live_instances"):
        with TestClient(create_app(settings)):
            pytest.fail("A baseline role bound to the expanded registry was silently stranded")
    with TestClient(create_app(selected), headers=AUTH) as client:
        assert (
            client.post(
                f"/agents/{research['id']}/transition", json={"action": "retire"}
            ).status_code
            == 200
        )
    with TestClient(create_app(settings), headers=AUTH) as client:
        assert client.get(f"/agents/{observer['id']}").json()["status"] == "retired"
        assert client.get(f"/agents/{research['id']}").json()["status"] == "retired"


def test_preview_is_offline_and_returns_exact_reviewable_registry():
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from app.computer_observer import computer_observer_registry, computer_observer_spec

    env = os.environ.copy()
    env.update(ZELO_OPERATOR_TOKEN="invalid-ambient-token", ZELO_RUNTIME_DIR="/not-allowed")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.preview_computer_observer"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    view = json.loads(result.stdout)
    assert view["approved_registry_sha256"] == computer_observer_registry().version
    assert view["candidate_spec"] == computer_observer_spec().model_dump(mode="json")
    assert view["runtime_started"] is False and view["instance_created"] is False


def test_normal_core_space_confirmation_executes_and_revokes_without_backend_file_fallback(
    settings, database, monkeypatch
):
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project

    backend = _controlled_backend(monkeypatch)
    with TestClient(create_app(_enabled_settings(settings)), headers=AUTH) as client:
        project = create_project(client)
        instance = create_instance(client, project["id"], "computer_observer")
        agent = f"/agents/{instance['id']}"
        assert (
            client.patch(
                agent + "/computer/permissions",
                json={"enabled": True, "browser": True, "files": True},
            ).status_code
            == 200
        )
        assert client.post(agent + "/computer/start").status_code == 200
        root = settings.runtime_dir
        for part in ("spaces", project["id"], "workroom", "documents"):
            root = root / part
            root.mkdir(mode=0o700, exist_ok=True)
        fixture = root / "evidence.txt"
        fixture.write_text("approved-workroom-content")
        fixture.chmod(0o600)
        resource = client.post(
            agent + "/space-resources",
            json={
                "space_id": "workroom",
                "resource_id": "documents",
                "operations": ["list", "read"],
            },
        )
        assert resource.status_code == 201, resource.text
        intent = client.post(
            agent + "/space-intents",
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
            agent + f"/space-intents/{intent.json()['id']}/activate",
            json={
                "resource_record_id": resource.json()["id"],
            },
        )
        assert activation.status_code == 201, activation.text
        activation_id = activation.json()["id"]
        for action, path in (("files_list", ""), ("files_read", "evidence.txt")):
            response = client.post(
                agent + "/tasks",
                json={
                    "task": {
                        "kind": "computer_run",
                        "payload": {
                            "action": action,
                            "path": path,
                            "space_activation_id": activation_id,
                        },
                    }
                },
            )
            assert response.status_code == 201, response.text
            done = _finished(client, response.json()["id"])
            assert done["status"] == "succeeded", done
            if action == "files_read":
                assert done["result"][action]["text"] == "approved-workroom-content"
            else:
                assert [x["path"] for x in done["result"][action]["entries"]] == ["evidence.txt"]
        assert not any(call[0] in ("files_read", "files_list") for call in backend.calls)

        other = create_instance(
            client, create_project(client, "Other scope")["id"], "computer_observer"
        )
        other_url = f"/agents/{other['id']}"
        assert (
            client.patch(
                other_url + "/computer/permissions", json={"enabled": True, "files": True}
            ).status_code
            == 200
        )
        assert (
            client.get(other_url + f"/space-resources/{resource.json()['id']}").status_code == 404
        )
        payload = {
            "task": {
                "kind": "computer_run",
                "payload": {
                    "action": "files_read",
                    "path": "evidence.txt",
                    "space_activation_id": activation_id,
                },
            }
        }
        assert client.post(other_url + "/tasks", json=payload).status_code == 409
        revoked = client.post(agent + f"/space-activations/{activation_id}/revoke")
        assert revoked.status_code == 200 and revoked.json()["revoked"]
        before = client.get("/tasks").json()["total"]
        assert client.post(agent + "/tasks", json=payload).status_code == 409
        assert client.get("/tasks").json()["total"] == before
