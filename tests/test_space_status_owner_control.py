"""Space readiness must agree with Core's durable owner takeover fence."""

import pytest

from tests.test_space_owner_provision import owner_env as _owner_env
from tests.test_space_owner_provision import provision_body

owner_env = _owner_env


def _prepare(client, instance, name="documents"):
    agent = f"/agents/{instance['id']}"
    resource = client.post(
        agent + "/space-resources/provision", json=provision_body(resource_id=name)
    )
    assert resource.status_code == 201, resource.text
    intent = client.post(
        agent + "/space-intents",
        json={
            "space_id": "workroom",
            "resource_kind": "files",
            "resource_id": name,
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
    assert (
        client.patch(
            agent + "/computer/permissions",
            json={
                "enabled": True,
                "browser": True,
                "files": True,
            },
        ).status_code
        == 200
    )
    assert client.post(agent + "/computer/start").status_code == 200
    return agent, activation.json()


@pytest.mark.parametrize("failure", [None, "take", "release"])
def test_space_readiness_agrees_with_takeover_task_denial(owner_env, failure):
    client, _database, _settings, backend, _project, instance = owner_env
    agent, activation = _prepare(client, instance)
    url = agent + "/specialist-profile/space-access"
    before = client.get(url).json()["routes"][0]
    assert before["eligible_for_scoped_file_task"]
    if failure == "take":
        backend.fail["take"] = "computer_http_503"
    taken = client.post(agent + "/computer/take")
    assert taken.status_code == (503 if failure == "take" else 200), taken.text
    if failure == "release":
        backend.fail["release"] = "computer_http_503"
        assert client.post(agent + "/computer/release").status_code == 503

    payload = {
        "task": {
            "kind": "computer_run",
            "payload": {
                "action": "files_read",
                "path": "notes.txt",
                "space_activation_id": activation["id"],
            },
        }
    }
    refused = client.post(agent + "/tasks", json=payload)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "computer_space_activation_invalid"
    assert client.get("/tasks").json()["total"] == 0
    calls = list(backend.calls)
    view = client.get(url)
    assert view.status_code == 200
    route = view.json()["routes"][0]
    assert route["reason"] == "computer_human_control_active", route
    assert route["eligible_for_scoped_file_task"] is False
    assert view.json()["projection_is_authority"] is False
    assert backend.calls == calls  # Inspection never performs remote control or reads.
    assert client.get("/tasks").json()["total"] == 0

    backend.fail.clear()
    assert client.post(agent + "/computer/release").status_code == 200
    restored = client.get(url).json()["routes"][0]
    assert restored["reason"] == "eligible_for_scoped_file_task"
    assert restored["eligible_for_scoped_file_task"] is True


def test_pending_takeover_is_visible_before_backend_acknowledges(owner_env, monkeypatch):
    import httpx

    from tests.conftest import AUTH

    client, _database, _settings, backend, _project, instance = owner_env
    agent, _activation = _prepare(client, instance)
    original_take = backend.take
    captured = []

    async def inspect_before_ack(instance_id):
        assert backend.control_state.holder == "bot"
        calls = list(backend.calls)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://test", headers=AUTH
        ) as observer:
            reply = await observer.get(agent + "/specialist-profile/space-access")
            assert reply.status_code == 200
            captured.append(reply.json()["routes"][0])
        assert backend.calls == calls
        return await original_take(instance_id)

    monkeypatch.setattr(backend, "take", inspect_before_ack)
    assert client.post(agent + "/computer/take").status_code == 200
    assert len(captured) == 1
    assert captured[0]["reason"] == "computer_human_control_active"
    assert captured[0]["eligible_for_scoped_file_task"] is False


def test_takeover_status_is_scoped_to_the_exact_agent(owner_env):
    from tests.test_agent_instances import create_instance, create_project

    client, _database, _settings, backend, _project, instance = owner_env
    first, _activation = _prepare(client, instance)
    other = create_instance(
        client, create_project(client, "Other owner scope")["id"], "computer_observer"
    )
    second, _other_activation = _prepare(client, other)
    assert client.post(first + "/computer/take").status_code == 200
    calls = list(backend.calls)
    first_view = client.get(first + "/specialist-profile/space-access").json()
    second_view = client.get(second + "/specialist-profile/space-access").json()
    assert first_view["routes"][0]["reason"] == "computer_human_control_active"
    assert second_view["routes"][0]["reason"] == "eligible_for_scoped_file_task"
    assert backend.calls == calls
    assert client.get("/tasks").json()["total"] == 0


def test_owner_revocation_does_not_disappear_after_handback(owner_env):
    client, _database, _settings, _backend, _project, instance = owner_env
    agent, activation = _prepare(client, instance)
    url = agent + "/specialist-profile/space-access"
    assert client.post(agent + "/computer/take").status_code == 200
    assert client.post(agent + f"/space-activations/{activation['id']}/revoke").status_code == 200
    assert client.get(url).json()["routes"][0]["reason"] == "owner_revoked"
    assert client.post(agent + "/computer/release").status_code == 200
    route = client.get(url).json()["routes"][0]
    assert route["reason"] == "owner_revoked"
    assert route["eligible_for_scoped_file_task"] is False


def test_core_restart_preserves_takeover_status_and_anonymous_denial(
    settings, database, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project
    from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings

    backend = _controlled_backend(monkeypatch)
    config = _enabled_settings(settings)
    with TestClient(create_app(config), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "computer_observer")
        agent, _activation = _prepare(client, instance)
        assert client.post(agent + "/computer/take").status_code == 200
    calls = list(backend.calls)
    with TestClient(create_app(config), headers=AUTH) as client:
        url = agent + "/specialist-profile/space-access"
        denied = client.get(url, headers={"Authorization": "Bearer invalid"})
        assert denied.status_code == 401
        route = client.get(url).json()["routes"][0]
        assert route["reason"] == "computer_human_control_active"
        assert route["eligible_for_scoped_file_task"] is False
        assert backend.calls == calls
        assert client.get("/tasks").json()["total"] == 0
