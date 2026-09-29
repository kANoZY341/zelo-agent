"""v0.14 persisted agent instance lifecycle and project-binding tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_factory import AgentFactory, AgentRegistry, coding_agent_spec
from app.agent_instances import AgentInstanceService
from app.memory import MemoryService
from app.policy import DomainError
from app.schemas import AgentInstanceTransition
from tests.conftest import AUTH


def create_project(client, name: str = "Zelo") -> dict:
    response = client.post(
        "/projects",
        headers=AUTH,
        json={"name": name, "summary": f"{name} project."},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_instance(client, project_id: str, agent_id: str = "coding") -> dict:
    response = client.post(
        "/agents",
        headers=AUTH,
        json={"agent_id": agent_id, "project_id": project_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_memory(client, scope: dict, content: str) -> dict:
    response = client.post(
        "/memories",
        headers=AUTH,
        json={
            "scope": scope,
            "kind": "fact",
            "content": content,
            "provenance": {"kind": "operator"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_list_and_created_event_bind_exact_registry_snapshot(client):
    project = create_project(client)
    created = create_instance(client, project["id"])

    assert created["agent_id"] == "coding"
    assert created["agent_version"] == 1
    assert len(created["registry_version"]) == 64
    assert created["project_id"] == project["id"]
    assert created["status"] == "active"
    assert created["spec"]["agent_id"] == "coding"
    assert created["spec"]["memory_namespace"] == "agent/coding"
    assert created["spec"]["worker_requirement"]["role"] == "developer"
    assert "worker_id" not in created["spec"]["worker_requirement"]
    assert "implementation" not in created["spec"]["worker_requirement"]

    fetched = client.get(f"/agents/{created['id']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.json() == created

    page = client.get(
        "/agents",
        headers=AUTH,
        params={"project_id": project["id"], "status": "active"},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == created["id"]

    events = client.get(f"/agents/{created['id']}/events", headers=AUTH)
    assert events.status_code == 200
    assert events.json()["total"] == 1
    event = events.json()["items"][0]
    assert event["sequence"] == 1
    assert event["event"] == "created"
    assert event["details"]["registry_version"] == created["registry_version"]
    assert event["details"]["project_id"] == project["id"]


def test_creation_fails_closed_for_unknown_agent_project_and_instance_limit(client):
    missing_agent = client.post(
        "/agents",
        headers=AUTH,
        json={
            "agent_id": "missing",
            "project_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert missing_agent.status_code == 404

    missing_project = client.post(
        "/agents",
        headers=AUTH,
        json={
            "agent_id": "coding",
            "project_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert missing_project.status_code == 404

    first_project = create_project(client, "First")
    create_instance(client, first_project["id"])
    duplicate = client.post(
        "/agents",
        headers=AUTH,
        json={"agent_id": "coding", "project_id": first_project["id"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Agent instance limit reached for project"

    second_project = create_project(client, "Second")
    second = create_instance(client, second_project["id"])
    assert second["project_id"] == second_project["id"]


def test_pause_resume_retire_is_explicit_and_retired_is_terminal(client):
    project = create_project(client)
    instance = create_instance(client, project["id"])

    paused = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "pause"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    pause_again = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "pause"},
    )
    assert pause_again.status_code == 409

    resumed = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "resume"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    retired = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "retire"},
    )
    assert retired.status_code == 200
    assert retired.json()["status"] == "retired"

    resume_retired = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "resume"},
    )
    assert resume_retired.status_code == 409
    assert resume_retired.json()["detail"] == "Only a paused agent instance can be resumed"

    events = client.get(f"/agents/{instance['id']}/events", headers=AUTH).json()["items"]
    assert [(item["sequence"], item["event"]) for item in events] == [
        (1, "created"),
        (2, "paused"),
        (3, "resumed"),
        (4, "retired"),
    ]


def test_stale_registry_snapshot_cannot_resume(database, client):
    project = create_project(client)
    instance = create_instance(client, project["id"])
    paused = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "pause"},
    )
    assert paused.status_code == 200

    changed = coding_agent_spec().model_copy(update={"version": 2})
    stale_service = AgentInstanceService(
        database,
        AgentFactory(AgentRegistry((changed,))),
        MemoryService(database),
    )
    with pytest.raises(DomainError, match="stale") as caught:
        stale_service.transition(
            instance["id"],
            AgentInstanceTransition(action="resume"),
        )
    assert caught.value.status_code == 409


def test_memory_context_is_only_project_and_specialist_scoped(client):
    project = create_project(client, "Bound Project")
    other_project = create_project(client, "Other Project")
    instance = create_instance(client, project["id"], "coding")

    project_memory = create_memory(
        client,
        {"kind": "project", "id": project["id"]},
        "Deployment checklist alpha belongs to this project.",
    )
    agent_memory = create_memory(
        client,
        {"kind": "agent", "id": "coding"},
        "Deployment checklist alpha is a coding procedure.",
    )
    personal_memory = create_memory(
        client,
        {"kind": "personal"},
        "Deployment checklist alpha is personal and must stay out.",
    )
    other_project_memory = create_memory(
        client,
        {"kind": "project", "id": other_project["id"]},
        "Deployment checklist alpha belongs to another project.",
    )
    other_agent_memory = create_memory(
        client,
        {"kind": "agent", "id": "research"},
        "Deployment checklist alpha belongs to research.",
    )

    response = client.post(
        f"/agents/{instance['id']}/memory-context",
        headers=AUTH,
        json={"query": "deployment checklist alpha", "limit": 10},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["instance"]["id"] == instance["id"]
    assert payload["scopes"] == [
        {"kind": "project", "id": project["id"]},
        {"kind": "agent", "id": "coding"},
    ]

    returned = {item["id"] for item in payload["retrieval"]["items"]}
    assert returned == {project_memory["id"], agent_memory["id"]}
    assert personal_memory["id"] not in returned
    assert other_project_memory["id"] not in returned
    assert other_agent_memory["id"] not in returned

    client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "pause"},
    )
    paused_context = client.post(
        f"/agents/{instance['id']}/memory-context",
        headers=AUTH,
        json={"query": "deployment checklist alpha"},
    )
    assert paused_context.status_code == 409


def test_database_protects_agent_identity_lifecycle_and_append_only_events(database, client):
    project = create_project(client)
    instance = create_instance(client, project["id"])

    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE agent_instances SET agent_id = 'research' WHERE id = :instance_id"),
                {"instance_id": instance["id"]},
            )

    with pytest.raises(IntegrityError, match="cannot be deleted"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM agent_instances WHERE id = :instance_id"),
                {"instance_id": instance["id"]},
            )

    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE agent_instance_events SET event = 'retired' "
                    "WHERE agent_instance_id = :instance_id"
                ),
                {"instance_id": instance["id"]},
            )

    retired = client.post(
        f"/agents/{instance['id']}/transition",
        headers=AUTH,
        json={"action": "retire"},
    )
    assert retired.status_code == 200

    with pytest.raises(IntegrityError, match="Invalid agent instance lifecycle"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE agent_instances SET status = 'active' WHERE id = :instance_id"),
                {"instance_id": instance["id"]},
            )

    with pytest.raises(IntegrityError, match="contiguous"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO agent_instance_events (
                        id, agent_instance_id, sequence, event, details, created_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000999',
                        :instance_id,
                        99,
                        'paused',
                        '{}',
                        '2026-09-30 00:00:00'
                    )
                    """
                ),
                {"instance_id": instance["id"]},
            )
