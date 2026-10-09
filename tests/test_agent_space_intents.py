"""OpenDots-inspired, owner-only immutable Space intentions; no authority."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_factory import (
    AgentComputerSpec,
    AgentFactory,
    AgentRegistry,
    coding_agent_spec,
)
from app.agent_instances import AgentInstanceService
from app.agent_space_intents import AgentSpaceIntentService
from app.memory import MemoryService
from app.models import Project
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import AgentInstanceCreate, AgentInstanceTransition, ProjectCreate
from tests.conftest import AUTH


def _fixture(database):
    base_spec = coding_agent_spec()
    spec = base_spec.model_copy(
        update={"space_permissions": ("workroom",), "computer": AgentComputerSpec(files=True)}
    )
    factory = AgentFactory(AgentRegistry([spec]))
    project = ProjectService(database).create(
        ProjectCreate(name="Scoped Agent", summary="Isolated agent Space intent tests")
    )
    agents = AgentInstanceService(database, factory, MemoryService(database))
    instance = agents.create(AgentInstanceCreate(agent_id="coding", project_id=project.id))
    service = AgentSpaceIntentService(database, factory)
    return service, factory, agents, spec, project, instance


def _create(service, instance_id, **overrides):
    return service.create(
        instance_id=instance_id,
        space_id=overrides.pop("space_id", "workroom"),
        resource_kind=overrides.pop("resource_kind", "files"),
        resource_id=overrides.pop("resource_id", "documents"),
        operations=overrides.pop("operations", ("list", "read")),
        ttl_seconds=overrides.pop("ttl_seconds", 600),
        **overrides,
    )


def test_intent_is_immutable_metadata_and_read_does_not_grant_access(database):
    service, _, _, _, project, instance = _fixture(database)
    receipt = _create(service, instance.id)
    assert receipt.agent_instance_id == instance.id
    assert receipt.project_id == project.id
    assert receipt.operations == ("list", "read")
    assert receipt.current_spec and receipt.eligible_for_review
    assert receipt.worker_authority is False
    assert receipt.tool_authority is False
    assert receipt.model_context_admission is False
    assert receipt.effective_resource_access is False
    assert len(receipt.intent_sha256) == 64
    assert service.get(instance_id=instance.id, intent_id=receipt.id) == receipt


def test_intent_revocation_is_terminal_and_does_not_ungrant_live_authority(database):
    service, _, _, _, _, instance = _fixture(database)
    intent = _create(service, instance.id)
    revoked = service.revoke(instance_id=instance.id, intent_id=intent.id)
    assert revoked.revoked is True
    assert revoked.eligible_for_review is False
    assert revoked.effective_resource_access is False
    assert service.revoke(instance_id=instance.id, intent_id=intent.id) == revoked
    assert service.get(instance_id=instance.id, intent_id=intent.id) == revoked
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text("UPDATE agent_space_intents SET resource_id='forged' WHERE id=:id"),
                {"id": intent.id},
            )
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM agent_space_intent_revocations WHERE intent_id=:id"),
                {"id": intent.id},
            )


def test_insert_or_replace_cannot_resurrect_revoked_intent_or_revoke_history(database):
    service, _, _, _, _, instance = _fixture(database)
    intent = _create(service, instance.id)
    service.revoke(instance_id=instance.id, intent_id=intent.id)

    # SQLite defaults to recursive_triggers=OFF: UPDATE/DELETE triggers alone
    # do not prevent REPLACE's implicit delete. Duplicate INSERT must abort.
    for table, key in (
        ("agent_space_intents", "id"),
        ("agent_space_intent_revocations", "intent_id"),
    ):
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(
                    text(
                        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} "
                        f"WHERE {key}=:intent_id"
                    ),
                    {"intent_id": intent.id},
                )
    persisted = service.get(instance_id=instance.id, intent_id=intent.id)
    assert persisted.revoked is True
    assert persisted.eligible_for_review is False
    assert persisted.effective_resource_access is False


def test_cross_agent_and_invalid_path_or_unsafe_operation_refused(database):
    service, _, _, _, _, instance = _fixture(database)
    receipt = _create(service, instance.id)
    with pytest.raises(DomainError, match="Agent instance not found"):
        service.get(
            instance_id="00000000-0000-4000-8000-000000000099",
            intent_id=receipt.id,
        )
    for resource_id in ("../escape", "file/path", "a%2fb", ".hidden", "Tést", "C_drive"):
        with pytest.raises(DomainError, match="canonical read-only metadata"):
            _create(service, instance.id, resource_id=resource_id)
    with pytest.raises(DomainError, match="canonical read-only metadata"):
        _create(service, instance.id, resource_kind="tool")
    with pytest.raises(DomainError, match="canonical read-only metadata"):
        _create(service, instance.id, operations=("execute",))
    with pytest.raises(DomainError, match="Space is not allowed"):
        _create(service, instance.id, space_id="private")
    with pytest.raises(DomainError, match="does not include resource"):
        _create(service, instance.id, resource_kind="browser", operations=("read",))
    with pytest.raises(DomainError, match="Only files support list"):
        _create(service, instance.id, resource_kind="memory")


def test_pause_spec_revision_change_project_pause_and_expiry_revoke_eligibility(
    database, monkeypatch
):
    service, factory, agents, spec, project, instance = _fixture(database)
    intent = _create(service, instance.id)
    agents.transition(instance.id, AgentInstanceTransition(action="pause"))
    assert service.get(instance_id=instance.id, intent_id=intent.id).current_spec is False
    agents.transition(instance.id, AgentInstanceTransition(action="resume"))
    old_intent = service.get(instance_id=instance.id, intent_id=intent.id)
    assert old_intent.current_spec is False
    assert old_intent.eligible_for_review is False
    assert old_intent.effective_resource_access is False
    new_intent = _create(service, instance.id, resource_id="post_resume")
    assert new_intent.eligible_for_review is True
    assert service.get(instance_id=instance.id, intent_id=intent.id).eligible_for_review is False
    next_spec = spec.model_copy(update={"version": 2})
    stale = AgentSpaceIntentService(database, AgentFactory(AgentRegistry([next_spec])))
    assert stale.get(instance_id=instance.id, intent_id=intent.id).current_spec is False
    with database.transaction() as session:
        session.get(Project, project.id).status = "paused"
    assert service.get(instance_id=instance.id, intent_id=intent.id).current_spec is False
    with database.transaction() as session:
        session.get(Project, project.id).status = "active"
    later = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr("app.agent_space_intents.utcnow", lambda: later)
    assert service.get(instance_id=instance.id, intent_id=intent.id).current_spec is False


def test_only_owner_spec_and_bounded_lifetime_can_propose(database):
    service, _, _, _, _, instance = _fixture(database)
    with pytest.raises(DomainError, match="lifetime invalid"):
        _create(service, instance.id, ttl_seconds=3601)
    with pytest.raises(DomainError, match="lifetime invalid"):
        _create(service, instance.id, ttl_seconds=True)
    with pytest.raises(DomainError, match="lifetime invalid"):
        _create(service, instance.id, ttl_seconds=59)


def test_revoke_frees_active_intent_slot_while_immutable_history_stays(database, monkeypatch):
    import app.agent_space_intents as module

    service, _, _, _, _, instance = _fixture(database)
    monkeypatch.setattr(module, "MAX_ACTIVE_INTENTS_PER_AGENT", 1)
    first = _create(service, instance.id)
    with pytest.raises(DomainError, match="limit reached"):
        _create(service, instance.id, resource_id="other")
    service.revoke(instance_id=instance.id, intent_id=first.id)
    second = _create(service, instance.id, resource_id="other")
    assert first.id != second.id


def test_live_operator_endpoints_refuse_unapproved_space_and_bad_auth(client):
    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Default", "summary": "No approved Spaces"},
    ).json()
    instance = client.post(
        "/agents",
        headers=AUTH,
        json={"agent_id": "coding", "project_id": project["id"]},
    ).json()
    url = f"/agents/{instance['id']}/space-intents"
    payload = {
        "space_id": "workroom",
        "resource_kind": "memory",
        "resource_id": "notes",
        "operations": ["read"],
        "ttl_seconds": 600,
    }
    assert (
        client.post(url, json=payload, headers={"Authorization": "Bearer bad"}).status_code == 401
    )
    denied = client.post(url, json=payload, headers=AUTH)
    assert denied.status_code == 403, denied.text
    assert not client.app.state.agent_factory.registry.get_agent("coding").space_permissions
    assert (
        client.post(url, headers=AUTH, json={**payload, "operations": ["execute"]}).status_code
        == 422
    )
    assert client.post(url, headers=AUTH, json={**payload, "ttl_seconds": "600"}).status_code == 422
    assert (
        client.post(url, headers=AUTH, json={**payload, "permissions": ["root"]}).status_code == 422
    )


def test_operator_api_creates_and_revokes_only_inert_intent(client, database):
    service, factory, _, _, _, instance = _fixture(database)
    client.app.state.agent_factory = factory
    payload = {
        "space_id": "workroom",
        "resource_kind": "files",
        "resource_id": "documents",
        "operations": ["list", "read"],
        "ttl_seconds": 300,
    }
    url = f"/agents/{instance.id}/space-intents"
    created = client.post(url, headers=AUTH, json=payload)
    assert created.status_code == 201, created.text
    intent = created.json()
    assert intent["effective_resource_access"] is False
    assert intent["worker_authority"] is False and intent["tool_authority"] is False
    path = f"{url}/{intent['id']}"
    assert client.get(path, headers=AUTH).json() == intent
    assert client.get(path, headers={"Authorization": "Bearer bad"}).status_code == 401
    revoked = client.post(f"{path}/revoke", headers=AUTH)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True
    assert revoked.json()["eligible_for_review"] is False
    assert revoked.json()["effective_resource_access"] is False
    assert service.get(instance_id=instance.id, intent_id=intent["id"]).revoked is True
