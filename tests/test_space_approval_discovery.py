"""Owner rediscovers approval history and can revoke after a lifecycle stop."""

import pytest

from app.models import AgentSpaceActivationRecord, AgentSpaceIntentRecord
from tests.test_space_owner_provision import owner_env as _owner_env
from tests.test_space_owner_provision import provision_body

owner_env = _owner_env


def _approval(client, agent, name="documents"):
    response = client.post(
        agent + "/space-resources/provision", json=provision_body(resource_id=name)
    )
    assert response.status_code == 201, response.text
    resource = response.json()
    response = client.post(
        agent + "/space-intents",
        json={
            "space_id": "workroom",
            "resource_kind": "files",
            "resource_id": name,
            "operations": ["list", "read"],
            "ttl_seconds": 600,
        },
    )
    assert response.status_code == 201, response.text
    intent = response.json()
    response = client.post(
        agent + f"/space-intents/{intent['id']}/activate",
        json={
            "resource_record_id": resource["id"],
        },
    )
    assert response.status_code == 201, response.text
    return resource, intent, response.json()


@pytest.mark.parametrize("collection", ["space-intents", "space-activations"])
def test_owner_can_page_and_revoke_existing_approvals_while_agent_paused(owner_env, collection):
    client, database, _settings, backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    triples = [_approval(client, agent, name) for name in ("alpha", "bravo", "charlie")]
    index = 1 if collection == "space-intents" else 2
    model = AgentSpaceIntentRecord if index == 1 else AgentSpaceActivationRecord
    # Persisted ordering may differ from submission order when timestamps tie.
    with database.read() as session:
        expected = [
            row.id
            for row in sorted(
                (session.get(model, triple[index]["id"]) for triple in triples),
                key=lambda row: (row.created_at, row.id),
            )
        ]
    assert client.post(agent + "/transition", json={"action": "pause"}).status_code == 200
    before = list(backend.calls)
    first = client.get(agent + "/" + collection, params={"limit": 2})
    second = client.get(agent + "/" + collection, params={"limit": 2, "offset": 2})
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["total"] == second.json()["total"] == 3
    items = first.json()["items"] + second.json()["items"]
    assert [item["id"] for item in items] == expected
    flag = "eligible_for_review" if index == 1 else "eligible_for_mediation"
    assert all(not item[flag] and not item["effective_resource_access"] for item in items)
    selected = items[0]["id"]
    revoked = client.post(f"{agent}/{collection}/{selected}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked"]
    again = client.get(agent + "/" + collection).json()
    assert again["total"] == 3 and again["items"][0]["revoked"]
    assert backend.calls == before
    assert client.get("/tasks").json()["total"] == 0
    assert "owner-provided evidence" not in first.text + second.text


@pytest.mark.parametrize("collection", ["space-intents", "space-activations"])
def test_approval_discovery_requires_owner_and_valid_page(owner_env, collection):
    client, _database, _settings, _backend, _project, instance = owner_env
    url = f"/agents/{instance['id']}/{collection}"
    assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 1000001}):
        assert client.get(url, params=params).status_code == 422
    page = client.get(url)
    assert page.status_code == 200
    assert page.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}
    missing = client.get(f"/agents/00000000-0000-0000-0000-000000000000/{collection}")
    assert missing.status_code == 404


@pytest.mark.parametrize("collection", ["space-intents", "space-activations"])
def test_counts_and_pages_are_private_to_one_agent_even_with_shared_project(owner_env, collection):
    from tests.test_agent_instances import create_instance, create_project

    client, _database, _settings, _backend, project, instance = owner_env
    own_url = f"/agents/{instance['id']}"
    own = _approval(client, own_url)
    assert client.post(own_url + "/transition", json={"action": "retire"}).status_code == 200
    successor = create_instance(client, project["id"], "computer_observer")
    successor_url = f"/agents/{successor['id']}"
    successor_records = _approval(client, successor_url, "successor")
    other = create_instance(
        client, create_project(client, "Private other project")["id"], "computer_observer"
    )
    other_records = _approval(client, f"/agents/{other['id']}")
    index = 1 if collection == "space-intents" else 2
    listing = client.get(own_url + "/" + collection, params={"limit": 1})
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert [row["id"] for row in listing.json()["items"]] == [own[index]["id"]]
    assert successor_records[index]["id"] not in listing.text
    assert other_records[index]["id"] not in listing.text
    assert client.get(own_url + "/" + collection, params={"offset": 1}).json()["items"] == []
    successor_listing = client.get(successor_url + "/" + collection).json()
    assert successor_listing["total"] == 1
    assert successor_listing["items"][0]["id"] == successor_records[index]["id"]
    assert client.post(f"{successor_url}/{collection}/{own[index]['id']}/revoke").status_code == 404
    assert client.post(f"{own_url}/{collection}/{own[index]['id']}/revoke").status_code == 200


@pytest.mark.parametrize(
    "stopped_by", ["project_pause", "expiry", "resource_retire", "intent_revoke"]
)
def test_history_is_visible_but_never_eligible_when_prerequisites_stop(
    owner_env, monkeypatch, stopped_by
):
    from datetime import timedelta

    from app.models import Project, utcnow

    client, database, _settings, backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    resource, intent, activation = _approval(client, agent)
    if stopped_by == "project_pause":
        with database.transaction() as session:
            session.get(Project, project["id"]).status = "paused"
    elif stopped_by == "expiry":
        expired = utcnow() + timedelta(hours=2)
        monkeypatch.setattr("app.agent_space_intents.utcnow", lambda: expired)
        monkeypatch.setattr("app.agent_space_resources.utcnow", lambda: expired)
    elif stopped_by == "resource_retire":
        assert client.post(agent + f"/space-resources/{resource['id']}/retire").status_code == 200
    else:
        assert client.post(agent + f"/space-intents/{intent['id']}/revoke").status_code == 200
    before = list(backend.calls)
    page = client.get(agent + "/space-activations")
    assert page.status_code == 200, page.text
    item = page.json()["items"][0]
    assert item["id"] == activation["id"] and not item["eligible_for_mediation"]
    assert item["revoked"] is (stopped_by == "intent_revoke")
    assert not item["effective_resource_access"]
    assert client.post(agent + f"/space-activations/{item['id']}/revoke").status_code == 200
    assert client.get(agent + "/space-activations").json()["items"][0]["revoked"]
    assert backend.calls == before
    assert client.get("/tasks").json()["total"] == 0


def test_stale_registry_keeps_owned_history_inspectable_without_current_grants(owner_env):
    from app.agent_factory import AgentFactory, default_agent_registry
    from app.agent_space_intents import AgentSpaceIntentService
    from app.agent_space_resources import AgentSpaceResourceService

    client, database, settings, _backend, _project, instance = owner_env
    _resource, intent, activation = _approval(client, f"/agents/{instance['id']}")
    changed_factory = AgentFactory(default_agent_registry())
    intents = AgentSpaceIntentService(database, changed_factory)
    resources = AgentSpaceResourceService(database, changed_factory, settings.runtime_dir)
    view = intents.list_intents(instance_id=instance["id"])
    assert view.total == 1 and view.items[0].id == intent["id"]
    assert not view.items[0].eligible_for_review and not view.items[0].current_spec
    confirmed = resources.list_activations(instance_id=instance["id"])
    assert confirmed.total == 1 and confirmed.items[0].id == activation["id"]
    assert not confirmed.items[0].eligible_for_mediation
    assert resources.revoke(instance_id=instance["id"], activation_id=activation["id"]).revoked


@pytest.mark.parametrize(
    "collection, digest",
    [
        ("space-intents", "app.agent_space_intents._intent_digest"),
        ("space-activations", "app.agent_space_resources._activation_digest"),
    ],
)
def test_collection_preserves_existing_receipt_integrity_denial(
    owner_env, monkeypatch, collection, digest
):
    client, _database, _settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    _approval(client, agent)
    monkeypatch.setattr(digest, lambda _record: "0" * 64)
    result = client.get(agent + "/" + collection)
    assert result.status_code == 503
    assert "integrity" in result.json()["detail"]


def test_history_service_rejects_noncanonical_pagination(owner_env):
    from app.agent_space_intents import AgentSpaceIntentService
    from app.agent_space_resources import AgentSpaceResourceService
    from app.policy import DomainError

    client, database, settings, _backend, _project, instance = owner_env
    intents = AgentSpaceIntentService(database, client.app.state.agent_factory)
    resources = AgentSpaceResourceService(
        database, client.app.state.agent_factory, settings.runtime_dir
    )
    for method in (intents.list_intents, resources.list_activations):
        for kwargs in ({"limit": True}, {"limit": 1.5}, {"offset": False}, {"offset": "1"}):
            with pytest.raises(DomainError, match="pagination invalid"):
                method(instance_id=instance["id"], **kwargs)


def test_restart_discovery_uses_saved_confirmation_then_emergency_revoke(
    settings, database, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project
    from tests.test_computer_observer_startup import (
        _controlled_backend,
        _enabled_settings,
        _finished,
    )

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(settings)
    with TestClient(create_app(selected), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "computer_observer")
        agent = f"/agents/{instance['id']}"
        resource, intent, activation = _approval(client, agent)
        assert (
            client.patch(
                agent + "/computer/permissions",
                json={
                    "enabled": True,
                    "files": True,
                    "browser": True,
                },
            ).status_code
            == 200
        )
        assert client.post(agent + "/computer/start").status_code == 200

    with TestClient(create_app(selected), headers=AUTH) as client:
        intents = client.get(agent + "/space-intents").json()
        approvals = client.get(agent + "/space-activations").json()
        assert intents["total"] == approvals["total"] == 1
        found = approvals["items"][0]
        assert found["id"] == activation["id"] and found["intent_id"] == intent["id"]
        assert found["resource_record_id"] == resource["id"] and found["eligible_for_mediation"]
        body = {
            "task": {
                "kind": "computer_run",
                "payload": {
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": found["id"],
                },
            }
        }
        request = client.post(agent + "/tasks", json=body)
        assert request.status_code == 201, request.text
        done = _finished(client, request.json()["id"])
        assert done["status"] == "succeeded", done
        assert done["result"]["files_read"]["text"] == "owner-provided evidence"
        assert client.post(agent + "/transition", json={"action": "pause"}).status_code == 200
        found_paused = client.get(agent + "/space-activations").json()["items"][0]
        assert found_paused["id"] == found["id"] and not found_paused["eligible_for_mediation"]
        assert (
            client.post(agent + f"/space-activations/{found_paused['id']}/revoke").status_code
            == 200
        )
        assert client.post(agent + "/transition", json={"action": "resume"}).status_code == 200
        count = client.get("/tasks").json()["total"]
        assert client.post(agent + "/tasks", json=body).status_code == 409
        assert client.get("/tasks").json()["total"] == count
        assert client.get(agent + "/space-activations").json()["items"][0]["revoked"]


@pytest.mark.parametrize("collection", ["space-intents", "space-activations"])
def test_equal_creation_timestamps_have_stable_id_order(owner_env, monkeypatch, collection):
    from app.models import utcnow

    client, _database, _settings, _backend, _project, instance = owner_env
    now = utcnow()
    monkeypatch.setattr("app.agent_space_intents.utcnow", lambda: now)
    monkeypatch.setattr("app.agent_space_resources.utcnow", lambda: now)
    agent = f"/agents/{instance['id']}"
    triples = [_approval(client, agent, name) for name in ("first", "second", "third")]
    index = 1 if collection == "space-intents" else 2
    expected = sorted(triple[index]["id"] for triple in triples)
    ids = [
        client.get(agent + "/" + collection, params={"limit": 1, "offset": n}).json()["items"][0][
            "id"
        ]
        for n in range(3)
    ]
    assert ids == expected
