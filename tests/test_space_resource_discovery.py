"""Owner rediscovers Space records through normal Core, without new grants."""

from sqlalchemy import func, select

from app.models import AgentSpaceActivationRecord, AgentSpaceIntentRecord
from tests.test_agent_instances import create_instance, create_project
from tests.test_space_owner_provision import owner_env as _owner_env
from tests.test_space_owner_provision import provision_body

owner_env = _owner_env


def test_discovery_pages_existing_resources_without_implicit_access(owner_env):
    client, database, _settings, backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    before = list(backend.calls)
    expected = []
    for name in ("alpha", "bravo", "charlie"):
        response = client.post(
            agent + "/space-resources/provision", json=provision_body(resource_id=name)
        )
        assert response.status_code == 201, response.text
        expected.append(response.json()["id"])
    first = client.get(agent + "/space-resources", params={"limit": 2})
    second = client.get(agent + "/space-resources", params={"limit": 2, "offset": 2})
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["total"] == second.json()["total"] == 3
    assert first.json()["limit"] == 2 and second.json()["offset"] == 2
    entries = first.json()["items"] + second.json()["items"]
    assert [item["id"] for item in entries] == expected
    assert all(item["project_id"] == project["id"] for item in entries)
    assert all(item["root_attested"] and not item["effective_resource_access"] for item in entries)
    assert "owner-provided evidence" not in first.text + second.text
    assert backend.calls == before
    assert client.get("/tasks").json()["total"] == 0
    assert not client.get(agent + "/computer").json()["permissions"]["enabled"]
    with database.read() as session:
        for model in (AgentSpaceIntentRecord, AgentSpaceActivationRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_discovery_enforces_auth_filter_and_keeps_retired_records_visible(owner_env):
    client, _database, _settings, _backend, _project, instance = owner_env
    url = f"/agents/{instance['id']}/space-resources"
    response = client.post(url + "/provision", json=provision_body())
    assert response.status_code == 201
    resource_id = response.json()["id"]
    assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get(url, params={"space_id": "vault"}).status_code == 403
    assert client.get(url, params={"space_id": "../workroom"}).status_code == 422
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert client.get(url, params=params).status_code == 422
    assert client.post(f"{url}/{resource_id}/retire").status_code == 200
    listing = client.get(url, params={"space_id": "workroom"})
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == resource_id
    assert listing.json()["items"][0]["retired"] is True
    assert listing.json()["items"][0]["effective_resource_access"] is False


def test_discovery_count_and_page_cannot_leak_another_project(owner_env):
    client, _database, _settings, _backend, project, instance = owner_env
    own_url = f"/agents/{instance['id']}/space-resources"
    own = client.post(own_url + "/provision", json=provision_body())
    assert own.status_code == 201
    other_project = create_project(client, "Other private scope")
    other_instance = create_instance(client, other_project["id"], "computer_observer")
    other_url = f"/agents/{other_instance['id']}/space-resources"
    other = client.post(other_url + "/provision", json=provision_body(resource_id="private"))
    assert other.status_code == 201
    listing = client.get(own_url)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert [item["id"] for item in listing.json()["items"]] == [own.json()["id"]]
    assert listing.json()["items"][0]["project_id"] == project["id"]
    assert other.json()["id"] not in listing.text
    assert other.json()["root_path"] not in listing.text
    empty_page = client.get(own_url, params={"offset": 1})
    assert empty_page.json()["total"] == 1 and empty_page.json()["items"] == []


def test_only_trusted_spaces_are_counted_before_pagination(database, settings):
    import pytest

    from app.agent_factory import AgentFactory, AgentRegistry
    from app.agent_instances import AgentInstanceService
    from app.agent_space_resources import AgentSpaceResourceService
    from app.computer_observer import computer_observer_spec
    from app.memory import MemoryService
    from app.policy import DomainError
    from app.projects import ProjectService
    from app.schemas import AgentInstanceCreate, ProjectCreate

    base = computer_observer_spec()
    broad = base.model_copy(
        update={
            "agent_id": "broad",
            "memory_namespace": "agent/broad",
            "space_permissions": ("archive", "workroom"),
        }
    )
    empty = base.model_copy(
        update={"agent_id": "empty", "memory_namespace": "agent/empty", "space_permissions": ()}
    )
    no_files = base.model_copy(
        update={"agent_id": "nofiles", "memory_namespace": "agent/nofiles", "computer": None}
    )
    factory = AgentFactory(AgentRegistry([base, broad, empty, no_files]))
    agents = AgentInstanceService(database, factory, MemoryService(database))
    resources = AgentSpaceResourceService(database, factory, settings.runtime_dir)
    project = ProjectService(database).create(
        ProjectCreate(name="Two Spaces", summary="Scope filtering")
    )
    instances = {
        spec.agent_id: agents.create(
            AgentInstanceCreate(agent_id=spec.agent_id, project_id=project.id)
        )
        for spec in (base, broad, empty, no_files)
    }
    records = []
    for space in ("archive", "workroom"):
        records.append(
            resources.provision(
                instance_id=instances["broad"].id,
                space_id=space,
                resource_id="documents",
                operations=("read",),
                files=(("note.txt", space),),
            )
        )
    narrow = resources.list_resources(instance_id=instances["computer_observer"].id, limit=1)
    assert narrow.total == 1 and [item.id for item in narrow.items] == [records[1].id]
    assert (
        resources.list_resources(instance_id=instances["computer_observer"].id, offset=1).items
        == ()
    )
    for space, record in zip(("archive", "workroom"), records, strict=True):
        scoped = resources.list_resources(instance_id=instances["broad"].id, space_id=space)
        assert scoped.total == 1 and scoped.items == (record,)
    assert resources.list_resources(instance_id=instances["empty"].id).total == 0
    with pytest.raises(DomainError, match="Space not in trusted AgentSpec"):
        resources.list_resources(instance_id=instances["computer_observer"].id, space_id="archive")
    with pytest.raises(DomainError, match="no file resource capability"):
        resources.list_resources(instance_id=instances["nofiles"].id)


def test_discovery_revalidates_agent_and_project_lifecycle(owner_env):
    from app.models import Project

    client, database, _settings, _backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    assert client.get(agent + "/space-resources").status_code == 200
    assert client.post(agent + "/transition", json={"action": "pause"}).status_code == 200
    assert client.get(agent + "/space-resources").status_code == 409
    assert client.post(agent + "/transition", json={"action": "resume"}).status_code == 200
    assert client.get(agent + "/space-resources").status_code == 200
    with database.transaction() as session:
        session.get(Project, project["id"]).status = "paused"
    assert client.get(agent + "/space-resources").status_code == 409


def test_discovery_reports_changed_root_without_opening_seed_contents(owner_env, monkeypatch):
    from pathlib import Path

    client, _database, _settings, _backend, _project, instance = owner_env
    url = f"/agents/{instance['id']}/space-resources"
    created = client.post(url + "/provision", json=provision_body()).json()
    root = Path(created["root_path"])
    saved = root.with_name("test-owned-preserved-root")
    root.rename(saved)
    root.mkdir(mode=0o700)
    original_open = Path.open

    def forbid_seed_open(path, *args, **kwargs):
        assert path.name != "notes.txt", "resource discovery tried to read private content"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_seed_open)
    page = client.get(url)
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["id"] == created["id"]
    assert page.json()["items"][0]["root_attested"] is False
    assert page.json()["items"][0]["effective_resource_access"] is False
    assert "owner-provided evidence" not in page.text


def test_discovery_rejects_out_of_bounds_and_unknown_identity(owner_env):
    client, _database, _settings, _backend, _project, instance = owner_env
    url = f"/agents/{instance['id']}/space-resources"
    for params in (
        {"offset": 1_000_001},
        {"offset": "9" * 40},
        {"limit": "false"},
        {"space_id": ""},
    ):
        assert client.get(url, params=params).status_code == 422
    for unknown in ("not-an-id", "00000000-0000-4000-8000-000000000001"):
        assert client.get(f"/agents/{unknown}/space-resources").status_code == 404
    beyond_end = client.get(url, params={"offset": 1_000_000})
    assert beyond_end.status_code == 200
    assert beyond_end.json()["items"] == [] and beyond_end.json()["total"] == 0


def test_discovery_survives_core_restart_with_resource_retirement(settings, database, monkeypatch):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from tests.conftest import AUTH
    from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings

    _controlled_backend(monkeypatch)
    config = _enabled_settings(settings)
    with TestClient(create_app(config), headers=AUTH) as client:
        project = create_project(client)
        instance = create_instance(client, project["id"], "computer_observer")
        url = f"/agents/{instance['id']}/space-resources"
        resource = client.post(url + "/provision", json=provision_body()).json()
        assert client.post(f"{url}/{resource['id']}/retire").status_code == 200
        before = client.get(url)
        assert before.status_code == 200
    with TestClient(create_app(config), headers=AUTH) as client:
        after = client.get(url)
        assert after.status_code == 200 and after.json() == before.json()
        assert after.json()["items"][0]["retired"] is True
        assert client.get("/tasks").json()["total"] == 0
