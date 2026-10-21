"""Owner creates a private, seeded Space without host-path or Agent write grants."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.application import create_app
from app.models import AgentSpaceActivationRecord, AgentSpaceIntentRecord, AgentSpaceResourceRecord
from tests.conftest import AUTH
from tests.test_agent_instances import create_instance, create_project
from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings, _finished


@pytest.fixture
def owner_env(settings, database, monkeypatch):
    backend = _controlled_backend(monkeypatch)
    with TestClient(create_app(_enabled_settings(settings)), headers=AUTH) as client:
        project = create_project(client, "Owner provisioned Space")
        instance = create_instance(client, project["id"], "computer_observer")
        yield client, database, settings, backend, project, instance


def provision_body(**changes):
    body = {
        "space_id": "workroom",
        "resource_id": "documents",
        "operations": ["list", "read"],
        "files": [{"name": "notes.txt", "text": "owner-provided evidence"}],
    }
    body.update(changes)
    return body


def test_owner_provision_seeds_private_files_and_mints_no_implicit_grants(owner_env):
    client, database, settings, backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    before = list(backend.calls)
    response = client.post(agent + "/space-resources/provision", json=provision_body())
    assert response.status_code == 201, response.text
    receipt = response.json()
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    assert receipt["root_attested"] and not receipt["effective_resource_access"]
    assert Path(receipt["root_path"]) == root
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "notes.txt").read_text() == "owner-provided evidence"
    assert (root / "notes.txt").stat().st_mode & 0o777 == 0o600
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 1
        for model in (AgentSpaceIntentRecord, AgentSpaceActivationRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert client.get("/tasks").json()["total"] == 0
    assert backend.calls == before
    assert not client.get(agent + "/computer").json()["permissions"]["enabled"]


def test_owner_provision_requires_authentication_before_creating_any_root(owner_env):
    client, _database, settings, _backend, _project, instance = owner_env
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision",
        headers={"Authorization": "Bearer invalid"},
        json=provision_body(),
    )
    assert response.status_code == 401
    assert not (settings.runtime_dir / "spaces").exists()


def test_provision_confirm_read_list_and_revoke_through_normal_core(owner_env):
    client, _database, _settings, backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    resource = client.post(agent + "/space-resources/provision", json=provision_body())
    assert resource.status_code == 201, resource.text
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
    activated = client.post(
        agent + f"/space-intents/{intent.json()['id']}/activate",
        json={
            "resource_record_id": resource.json()["id"],
        },
    )
    assert activated.status_code == 201, activated.text
    activation = activated.json()["id"]
    for action, path in (("files_list", ""), ("files_read", "notes.txt")):
        submitted = client.post(
            agent + "/tasks",
            json={
                "task": {
                    "kind": "computer_run",
                    "payload": {
                        "action": action,
                        "path": path,
                        "space_activation_id": activation,
                    },
                }
            },
        )
        assert submitted.status_code == 201, submitted.text
        done = _finished(client, submitted.json()["id"])
        assert done["status"] == "succeeded", done
        if action == "files_read":
            assert done["result"][action]["text"] == "owner-provided evidence"
        else:
            assert [x["path"] for x in done["result"][action]["entries"]] == ["notes.txt"]
    assert not any(call[0] in ("files_read", "files_list") for call in backend.calls)
    assert client.post(agent + f"/space-activations/{activation}/revoke").status_code == 200
    count = client.get("/tasks").json()["total"]
    denied = client.post(
        agent + "/tasks",
        json={
            "task": {
                "kind": "computer_run",
                "payload": {
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": activation,
                },
            }
        },
    )
    assert denied.status_code == 409
    assert client.get("/tasks").json()["total"] == count


@pytest.mark.parametrize(
    "files",
    [
        [{"name": "../escape.txt", "text": "x"}],
        [{"name": "/tmp/escape.txt", "text": "x"}],
        [{"name": "nested/notes.txt", "text": "x"}],
        [{"name": "run.py", "text": "print('x')"}],
        [{"name": "notes.txt", "text": "first"}, {"name": "notes.txt", "text": "second"}],
        [{"name": "notes.txt", "text": "☃" * 30000}],
        [{"name": f"file-{i}.txt", "text": "x" * 65536} for i in range(5)],
        [{"name": f"file-{i}.txt", "text": "x"} for i in range(17)],
        [{"name": "notes.txt", "text": "\ud800"}],
    ],
)
def test_invalid_or_oversized_seed_refused_before_any_root(owner_env, files):
    client, database, settings, _backend, _project, instance = owner_env
    # Explicit JSON ASCII encoding allows testing a malformed surrogate request.
    import json

    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision",
        content=json.dumps(provision_body(files=files)),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert not (settings.runtime_dir / "spaces").exists()
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 0


def test_existing_unregistered_root_and_registered_retry_never_overwrite(owner_env):
    client, _database, settings, _backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    root.mkdir(parents=True, mode=0o700)
    marker = root / "original.txt"
    marker.write_text("preserve existing owner data")
    response = client.post(agent + "/space-resources/provision", json=provision_body())
    assert response.status_code == 409
    assert marker.read_text() == "preserve existing owner data"
    assert not (root / "notes.txt").exists()
    new = client.post(agent + "/space-resources/provision", json=provision_body(resource_id="new"))
    assert new.status_code == 201
    rejected = client.post(
        agent + "/space-resources/provision",
        json=provision_body(
            resource_id="new",
            files=[{"name": "notes.txt", "text": "must not replace"}],
        ),
    )
    assert rejected.status_code == 409
    assert (root.parent / "new" / "notes.txt").read_text() == "owner-provided evidence"


def test_space_symlink_is_refused_without_writing_to_its_target(owner_env):
    client, _database, settings, _backend, _project, instance = owner_env
    outside = settings.runtime_dir / "test-owned-outside"
    outside.mkdir()
    (settings.runtime_dir / "spaces").symlink_to(outside, target_is_directory=True)
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
    )
    assert response.status_code == 409
    assert list(outside.iterdir()) == []


def test_unapproved_space_and_inactive_agent_create_no_directories(owner_env):
    client, _database, settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    assert (
        client.post(
            agent + "/space-resources/provision", json=provision_body(space_id="vault")
        ).status_code
        == 403
    )
    assert client.post(agent + "/transition", json={"action": "pause"}).status_code == 200
    assert (
        client.post(agent + "/space-resources/provision", json=provision_body()).status_code == 409
    )
    assert not (settings.runtime_dir / "spaces").exists()


def test_quota_is_checked_before_creating_another_resource(owner_env, monkeypatch):
    client, _database, settings, _backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    monkeypatch.setattr("app.agent_space_resources.MAX_RESOURCES_PER_PROJECT", 1)
    assert (
        client.post(agent + "/space-resources/provision", json=provision_body()).status_code == 201
    )
    response = client.post(
        agent + "/space-resources/provision", json=provision_body(resource_id="second")
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Space resource registry limit reached"
    assert not (settings.runtime_dir / "spaces" / project["id"] / "workroom" / "second").exists()


def test_partial_write_failure_preserves_data_and_cannot_auto_retry(owner_env, monkeypatch):
    import os

    client, database, settings, _backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    write = os.write

    def refuse_selected_write(fd, content):
        if content == b"fail-only-this-owned-fixture":
            raise OSError("synthetic test failure")
        return write(fd, content)

    monkeypatch.setattr("app.space_owner_provision.os.write", refuse_selected_write)
    response = client.post(
        agent + "/space-resources/provision",
        json=provision_body(
            files=[
                {"name": "a.txt", "text": "first content preserved"},
                {"name": "b.txt", "text": "fail-only-this-owned-fixture"},
            ]
        ),
    )
    assert response.status_code == 409
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    assert not root.exists()
    prepared = [p for p in root.parent.glob(".provision-documents-*") if p.is_dir()]
    assert len(prepared) == 1
    assert (prepared[0] / "a.txt").read_text() == "first content preserved"
    assert (prepared[0] / "b.txt").exists()
    assert (root.parent / ".provision-documents").is_file()
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 0
    monkeypatch.setattr("app.space_owner_provision.os.write", write)
    retry = client.post(agent + "/space-resources/provision", json=provision_body())
    assert retry.status_code == 409
    assert not (root / "notes.txt").exists()


def test_seed_validation_errors_do_not_echo_owner_document_content(owner_env):
    client, _database, settings, _backend, _project, instance = owner_env
    private = "PRIVATE-OWNER-SEED-CONTENT-NOT-FOR-ERRORS"
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision",
        json=provision_body(
            files=[{"name": "notes.txt", "text": {"document": private}}],
        ),
    )
    assert response.status_code == 422
    assert private not in response.text
    assert not (settings.runtime_dir / "spaces").exists()


def test_parallel_owner_provision_has_one_winner_without_overwrite(owner_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, database, settings, _backend, project, instance = owner_env
    barrier = Barrier(2)

    def create(text):
        barrier.wait(timeout=5)
        return client.post(
            f"/agents/{instance['id']}/space-resources/provision",
            json=provision_body(
                files=[{"name": "notes.txt", "text": text}],
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create, value) for value in ("first", "second")]
        responses = [future.result(timeout=15) for future in futures]
    assert sorted(response.status_code for response in responses) == [201, 409]
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    assert (root / "notes.txt").read_text() in ("first", "second")
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 1


def test_changed_root_before_registration_refuses_without_adopting_replacement(
    owner_env, monkeypatch
):
    from app import space_owner_provision

    client, database, settings, _backend, project, instance = owner_env
    create = space_owner_provision.provision_seeded_root
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    moved = root.parent / "preserved-original"

    def replace_after_creation(*args, **kwargs):
        identity = create(*args, **kwargs)
        root.rename(moved)
        root.mkdir(mode=0o700)
        return identity

    monkeypatch.setattr(space_owner_provision, "provision_seeded_root", replace_after_creation)
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
    )
    assert response.status_code == 409
    assert (moved / "notes.txt").read_text() == "owner-provided evidence"
    assert list(root.iterdir()) == []
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 0


@pytest.mark.parametrize(
    "files",
    [
        [],
        [{"name": "limit.txt", "text": "x" * 65536}],
        [{"name": "arabic.txt", "text": "ملاحظات المشروع"}],
    ],
)
def test_empty_exact_limit_and_unicode_seed_preserve_content(owner_env, files):
    client, _database, settings, _backend, project, instance = owner_env
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body(files=files)
    )
    assert response.status_code == 201, response.text
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    assert {p.name for p in root.iterdir()} == {item["name"] for item in files}
    for item in files:
        assert (root / item["name"]).read_text() == item["text"]


def test_shared_directory_write_permissions_are_refused(owner_env):
    client, _database, settings, _backend, _project, instance = owner_env
    spaces = settings.runtime_dir / "spaces"
    spaces.mkdir(mode=0o700)
    spaces.chmod(0o777)
    try:
        response = client.post(
            f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
        )
        assert response.status_code == 409
        assert list(spaces.iterdir()) == []
    finally:
        spaces.chmod(0o700)


def test_model_task_and_unknown_host_path_cannot_invoke_owner_provision(owner_env):
    client, _database, settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    assert (
        client.post(
            agent + "/space-resources/provision",
            json={
                **provision_body(),
                "root_path": "/tmp/not-an-approved-space",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            agent + "/tasks",
            json={
                "task": {
                    "kind": "computer_run",
                    "payload": {"action": "space_resources_provision"},
                }
            },
        ).status_code
        == 422
    )
    assert not (settings.runtime_dir / "spaces").exists()


def test_provisioned_resource_and_text_survive_normal_core_restart(settings, database, monkeypatch):
    _controlled_backend(monkeypatch)
    config = _enabled_settings(settings)
    with TestClient(create_app(config), headers=AUTH) as client:
        project = create_project(client)
        instance = create_instance(client, project["id"], "computer_observer")
        response = client.post(
            f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
        )
        assert response.status_code == 201
        receipt = response.json()
    with TestClient(create_app(config), headers=AUTH) as client:
        loaded = client.get(f"/agents/{instance['id']}/space-resources/{receipt['id']}")
        assert loaded.status_code == 200 and loaded.json() == receipt
        assert (Path(receipt["root_path"]) / "notes.txt").read_text() == "owner-provided evidence"
        assert client.get("/tasks").json()["total"] == 0


def test_published_root_is_never_adopted_or_seeded_after_directory_swap(owner_env, monkeypatch):
    import os

    client, database, settings, _backend, project, instance = owner_env
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    original_open = os.open
    swapped = False

    def replace_public_root_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "documents" and flags & os.O_DIRECTORY and not swapped:
            swapped = True
            root.rename(root.parent / "original-new-root")
            root.mkdir(mode=0o700)
            (root / "preexisting.txt").write_text("existing data must stay untouched")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("app.space_owner_provision.os.open", replace_public_root_before_open)
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
    )
    if swapped:
        assert response.status_code == 409, response.text
        assert not (root / "notes.txt").exists()
        assert (root / "preexisting.txt").read_text() == "existing data must stay untouched"
        expected_count = 0
    else:
        # Preparing privately without opening the published name eliminates
        # this race, rather than merely detecting it after writing into it.
        assert response.status_code == 201, response.text
        assert (root / "notes.txt").read_text() == "owner-provided evidence"
        expected_count = 1
    with database.read() as session:
        assert (
            session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord))
            == expected_count
        )


@pytest.mark.parametrize("change", ["replace", "modify", "extra"])
def test_earlier_seed_changes_during_later_write_cannot_publish_success(
    owner_env, monkeypatch, change
):
    import os

    client, database, _settings, _backend, _project, instance = owner_env
    original_write = os.write
    changed = False

    def change_first_file_when_second_is_written(fd, content):
        nonlocal changed
        if content == b"second-test-file" and not changed:
            changed = True
            root = Path(os.readlink(f"/proc/self/fd/{fd}")).parent
            first = root / "a.txt"
            if change == "replace":
                first.rename(root / "preserved-original-a.txt")
                first.write_text("substitute owner input")
                first.chmod(0o600)
            elif change == "modify":
                first.write_text("same-inode mutation")
            else:
                (root / "unexpected.txt").write_text("unrequested resource content")
        return original_write(fd, content)

    monkeypatch.setattr(
        "app.space_owner_provision.os.write", change_first_file_when_second_is_written
    )
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision",
        json=provision_body(
            files=[
                {"name": "a.txt", "text": "original owner input"},
                {"name": "b.txt", "text": "second-test-file"},
            ]
        ),
    )
    assert changed
    assert response.status_code == 409, response.text
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 0


def test_atomic_publish_refuses_even_an_empty_destination_created_concurrently(
    owner_env, monkeypatch
):
    from app import space_owner_provision

    client, database, settings, _backend, project, instance = owner_env
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    original = space_owner_provision._publish_noreplace
    existing_inode = None

    def insert_empty_destination(parent_fd, prepared, destination):
        nonlocal existing_inode
        root.mkdir(mode=0o700)
        existing_inode = root.stat().st_ino
        original(parent_fd, prepared, destination)

    monkeypatch.setattr(space_owner_provision, "_publish_noreplace", insert_empty_destination)
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
    )
    assert response.status_code == 409
    assert root.stat().st_ino == existing_inode and list(root.iterdir()) == []
    prepared = [p for p in root.parent.glob(".provision-documents-*") if p.is_dir()]
    assert len(prepared) == 1
    assert (prepared[0] / "notes.txt").read_text() == "owner-provided evidence"
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 0


def test_unavailable_atomic_publish_preserves_staging_without_unsafe_fallback(
    owner_env, monkeypatch
):
    from types import SimpleNamespace

    client, database, settings, _backend, project, instance = owner_env
    monkeypatch.setattr("app.space_owner_provision.ctypes.CDLL", lambda *a, **k: SimpleNamespace())
    response = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
    )
    assert response.status_code == 409
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    assert not root.exists()
    prepared = [p for p in root.parent.glob(".provision-documents-*") if p.is_dir()]
    assert len(prepared) == 1
    assert (prepared[0] / "notes.txt").read_text() == "owner-provided evidence"
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(AgentSpaceResourceRecord)) == 0


def test_database_commit_failure_preserves_published_files_without_resource_or_grant(owner_env):
    from sqlalchemy import event

    client, database, settings, _backend, project, instance = owner_env
    store = client.app.state.storage

    def commit_failure(connection):
        raise RuntimeError("synthetic-owner-provision-commit-failure")

    event.listen(store.engine, "commit", commit_failure)
    try:
        with pytest.raises(RuntimeError, match="synthetic-owner-provision-commit-failure"):
            client.post(
                f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
            )
    finally:
        event.remove(store.engine, "commit", commit_failure)
    root = settings.runtime_dir / "spaces" / project["id"] / "workroom" / "documents"
    assert (root / "notes.txt").read_text() == "owner-provided evidence"
    with database.read() as session:
        for model in (AgentSpaceResourceRecord, AgentSpaceIntentRecord, AgentSpaceActivationRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    retry = client.post(
        f"/agents/{instance['id']}/space-resources/provision", json=provision_body()
    )
    assert retry.status_code == 409
    assert (root / "notes.txt").read_text() == "owner-provided evidence"


def test_resume_snapshot_barrier_requires_explicit_observation_before_space_retry(
    owner_env, monkeypatch
):
    client, _database, _settings, backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    resource = client.post(agent + "/space-resources/provision", json=provision_body())
    assert resource.status_code == 201
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
    intent = client.post(
        agent + "/space-intents",
        json={
            "space_id": "workroom",
            "resource_kind": "files",
            "resource_id": "documents",
            "operations": ["read"],
            "ttl_seconds": 600,
        },
    )
    assert intent.status_code == 201
    activation = client.post(
        agent + f"/space-intents/{intent.json()['id']}/activate",
        json={
            "resource_record_id": resource.json()["id"],
        },
    )
    assert activation.status_code == 201
    backend.control_state = backend.control_state.model_copy(
        update={"resume_snapshot_required": True}
    )
    submitted = client.post(
        agent + "/tasks",
        json={
            "task": {
                "kind": "computer_run",
                "payload": {
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": activation.json()["id"],
                },
            }
        },
    )
    assert submitted.status_code == 201
    task_id = submitted.json()["id"]
    denied = _finished(client, task_id)
    assert denied["status"] == "failed" and denied["error"] == "computer_space_read_denied"
    assert denied["result"] is None
    assert "space_file_read_completed" not in {
        item["event"] for item in client.get(f"/tasks/{task_id}/events").json()
    }

    original_read_action = backend.read_action

    async def observe_fresh_snapshot(instance_id, action, *, path="", authorize_action=None):
        result = await original_read_action(
            instance_id, action, path=path, authorize_action=authorize_action
        )
        if action == "browser_snapshot":
            # External backend state transition observed in native qualification;
            # the real Core gateway and permission checks are not replaced.
            backend.control_state = backend.control_state.model_copy(
                update={"resume_snapshot_required": False}
            )
        return result

    monkeypatch.setattr(backend, "read_action", observe_fresh_snapshot)
    snapshot = client.post(
        agent + "/tasks",
        json={
            "task": {
                "kind": "computer_run",
                "payload": {"action": "browser_snapshot"},
            }
        },
    )
    assert snapshot.status_code == 201
    assert _finished(client, snapshot.json()["id"])["status"] == "succeeded"
    retried = client.post(f"/tasks/{task_id}/retry")
    assert retried.status_code == 202, retried.text
    completed = _finished(client, task_id)
    assert completed["status"] == "succeeded", completed
    assert completed["result"]["files_read"]["text"] == "owner-provided evidence"
