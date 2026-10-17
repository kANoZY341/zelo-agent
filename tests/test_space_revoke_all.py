"""One explicit owner action withdraws existing Space approvals atomically."""

import pytest
from sqlalchemy import func, select

from app.models import AgentSpaceActivationRevocation, AgentSpaceIntentRevocation
from tests.test_space_approval_discovery import _approval
from tests.test_space_owner_provision import owner_env as _owner_env

owner_env = _owner_env
BODY = {"confirm": True}


def _revocations(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                AgentSpaceIntentRevocation,
                AgentSpaceActivationRevocation,
            )
        )


def test_owner_revokes_all_existing_intents_and_confirmations_without_deleting_data(owner_env):
    client, database, _settings, backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    first = _approval(client, agent, "alpha")
    second = _approval(client, agent, "bravo")
    assert client.post(agent + f"/space-activations/{first[2]['id']}/revoke").status_code == 200
    before = list(backend.calls)
    response = client.post(agent + "/space-approvals/revoke-all", json=BODY)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["agent_instance_id"] == instance["id"] and receipt["project_id"] == project["id"]
    assert set(receipt["newly_revoked_intent_ids"]) == {first[1]["id"], second[1]["id"]}
    assert receipt["newly_revoked_activation_ids"] == [second[2]["id"]]
    assert receipt["total_intents"] == receipt["total_activations"] == 2
    assert receipt["future_owner_approvals_blocked"] is False
    assert _revocations(database) == (2, 2)
    for collection in ("space-intents", "space-activations"):
        page = client.get(agent + "/" + collection).json()
        assert page["total"] == 2 and all(row["revoked"] for row in page["items"])
    assert client.get(agent + "/space-resources").json()["total"] == 2
    assert backend.calls == before and client.get("/tasks").json()["total"] == 0
    repeated = client.post(agent + "/space-approvals/revoke-all", json=BODY)
    assert repeated.status_code == 200
    assert repeated.json()["newly_revoked_intent_ids"] == []
    assert repeated.json()["newly_revoked_activation_ids"] == []
    assert _revocations(database) == (2, 2)


def test_bulk_revocation_requires_owner_confirmation_and_exact_agent(owner_env):
    client, database, _settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    _approval(client, agent)
    url = agent + "/space-approvals/revoke-all"
    assert (
        client.post(url, json=BODY, headers={"Authorization": "Bearer invalid"}).status_code == 401
    )
    for body in (
        {},
        {"confirm": False},
        {"confirm": "true"},
        {"confirm": True, "project_id": "other"},
    ):
        assert client.post(url, json=body).status_code == 422
    assert (
        client.post(
            "/agents/00000000-0000-0000-0000-000000000000/space-approvals/revoke-all", json=BODY
        ).status_code
        == 404
    )
    assert _revocations(database) == (0, 0)


@pytest.mark.parametrize("state", ["paused", "retired", "stale", "project_paused", "root_missing"])
def test_emergency_revocation_remains_available_without_active_execution(
    owner_env, monkeypatch, state
):
    from pathlib import Path

    from app.agent_factory import AgentFactory, default_agent_registry
    from app.models import Project

    client, database, _settings, backend, project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    record = _approval(client, agent)
    if state in ("paused", "retired"):
        assert client.post(agent + "/transition", json={"action": state[:-1]}).status_code == 200
    elif state == "stale":
        # Model a configuration revision, not forbidden mutation of the
        # persisted immutable identity. The actual receipt checks stay intact.
        monkeypatch.setattr(
            client.app.state, "agent_factory", AgentFactory(default_agent_registry())
        )
    elif state == "project_paused":
        with database.transaction() as session:
            session.get(Project, project["id"]).status = "paused"
    else:
        root = Path(record[0]["root_path"])
        root.rename(root.with_name("preserved-owner-directory"))
    calls = list(backend.calls)
    response = client.post(agent + "/space-approvals/revoke-all", json=BODY)
    assert response.status_code == 200, response.text
    assert _revocations(database) == (1, 1)
    assert client.get(agent + f"/space-intents/{record[1]['id']}").json()["revoked"]
    assert client.get(agent + f"/space-activations/{record[2]['id']}").json()["revoked"]
    assert backend.calls == calls


def test_revoke_all_cannot_withdraw_another_agent_or_project_approvals(owner_env):
    from tests.test_agent_instances import create_instance, create_project

    client, database, _settings, _backend, project, instance = owner_env
    first = f"/agents/{instance['id']}"
    _approval(client, first)
    assert client.post(first + "/transition", json={"action": "retire"}).status_code == 200
    successor = create_instance(client, project["id"], "computer_observer")
    successor_url = f"/agents/{successor['id']}"
    successor_approval = _approval(client, successor_url, "successor")
    other = create_instance(
        client, create_project(client, "Other Project")["id"], "computer_observer"
    )
    other_url = f"/agents/{other['id']}"
    other_approval = _approval(client, other_url)
    response = client.post(first + "/space-approvals/revoke-all", json=BODY)
    assert response.status_code == 200
    assert _revocations(database) == (1, 1)
    for url, record in ((successor_url, successor_approval), (other_url, other_approval)):
        view = client.get(url + f"/space-activations/{record[2]['id']}").json()
        assert not view["revoked"] and view["eligible_for_mediation"]


def test_database_failure_rolls_back_both_revocation_tables(owner_env):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.agent_space_resources import AgentSpaceResourceService

    client, database, settings, _backend, _project, instance = owner_env
    _approval(client, f"/agents/{instance['id']}")
    service = AgentSpaceResourceService(
        database, client.app.state.agent_factory, settings.runtime_dir
    )
    with database.transaction() as session:
        session.execute(
            text(
                "CREATE TRIGGER test_owned_revoke_abort BEFORE INSERT ON "
                "agent_space_intent_revocations BEGIN SELECT RAISE(ABORT, "
                "'synthetic-owner-revocation-failure'); END"
            )
        )
    try:
        with pytest.raises(IntegrityError):
            service.revoke_all(instance_id=instance["id"])
        assert _revocations(database) == (0, 0)
    finally:
        with database.transaction() as session:
            session.execute(text("DROP TRIGGER test_owned_revoke_abort"))
    receipt = service.revoke_all(instance_id=instance["id"])
    assert len(receipt.newly_revoked_intent_ids) == len(receipt.newly_revoked_activation_ids) == 1
    assert _revocations(database) == (1, 1)


def test_concurrent_revoke_all_records_each_revocation_once(owner_env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, database, _settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    _approval(client, agent)
    ready = Barrier(2)

    def revoke():
        ready.wait(timeout=5)
        return client.post(agent + "/space-approvals/revoke-all", json=BODY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(revoke) for _ in range(2)]
        responses = [future.result(timeout=15) for future in futures]
    assert all(response.status_code == 200 for response in responses)
    assert sorted(len(response.json()["newly_revoked_intent_ids"]) for response in responses) == [
        0,
        1,
    ]
    assert sorted(
        len(response.json()["newly_revoked_activation_ids"]) for response in responses
    ) == [0, 1]
    assert _revocations(database) == (1, 1)


def test_revocation_is_not_a_silent_ban_on_future_explicit_owner_approvals(owner_env):
    client, _database, _settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    _approval(client, agent)
    assert client.post(agent + "/space-approvals/revoke-all", json=BODY).status_code == 200
    fresh = _approval(client, agent, "fresh")
    assert fresh[2]["eligible_for_mediation"] and not fresh[2]["revoked"]
    again = client.post(agent + "/space-approvals/revoke-all", json=BODY).json()
    assert again["newly_revoked_intent_ids"] == [fresh[1]["id"]]
    assert again["newly_revoked_activation_ids"] == [fresh[2]["id"]]


@pytest.mark.parametrize("stage", ["pending", "claimed", "during_control"])
def test_revocation_blocks_pending_claimed_and_suspended_reads(
    database, settings, monkeypatch, stage
):
    import asyncio

    from app.policy import DomainError
    from app.runner import Runner
    from app.schemas import AgentTaskCreate, ComputerRunInput
    from tests.test_space_file_gateway import _fixture

    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("never return this after revocation")
    task, created = env["agent_submit"].submit(
        env["instance"].id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": env["activation"].id,
                },
            ),
        ),
        None,
    )
    assert created
    if stage == "pending":
        env["resources"].revoke_all(instance_id=env["instance"].id)
        assert env["tasks"].claim() is None
        stopped = env["tasks"].get(task.id)
        assert stopped.status == "cancelled" and not stopped.attempts
    else:
        claimed = env["tasks"].claim()
        assert claimed is not None and claimed.task_id == task.id
        if stage == "claimed":
            env["resources"].revoke_all(instance_id=env["instance"].id)
        else:
            original_control = env["backend"].control

            async def revoke_while_control_awaits(instance_id):
                state = await original_control(instance_id)
                env["resources"].revoke_all(instance_id=instance_id)
                return state

            monkeypatch.setattr(env["backend"], "control", revoke_while_control_awaits)
        runner = Runner(
            env["tasks"], computer_supervisor=env["supervisor"], space_file_gateway=env["gateway"]
        )
        asyncio.run(runner._run(claimed))
        stopped = env["tasks"].get(task.id)
        assert stopped.status == "failed"
    assert stopped.result is None
    assert all(
        item.event != "space_file_read_completed" for item in env["tasks"].events(task.id, 100, 0)
    )
    with pytest.raises(DomainError):
        env["tasks"].retry(task.id)
    assert _revocations(database) == (1, 1)


def test_empty_and_pending_only_revocation_needs_no_computer_permissions(owner_env):
    client, database, _settings, backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    empty = client.post(agent + "/space-approvals/revoke-all", json=BODY)
    assert empty.status_code == 200
    assert empty.json()["total_intents"] == empty.json()["total_activations"] == 0
    for kind in ("files", "memory"):
        intent = client.post(
            agent + "/space-intents",
            json={
                "space_id": "workroom",
                "resource_kind": kind,
                "resource_id": "unconfirmed",
                "operations": ["read"],
                "ttl_seconds": 600,
            },
        )
        assert intent.status_code == 201, intent.text
    calls = list(backend.calls)
    result = client.post(agent + "/space-approvals/revoke-all", json=BODY)
    assert result.status_code == 200
    assert result.json()["total_intents"] == 2 and result.json()["total_activations"] == 0
    assert _revocations(database) == (2, 0)
    assert backend.calls == calls
    assert not client.get(agent + "/computer").json()["permissions"]["enabled"]


def test_revocation_limit_refuses_without_partial_mutation(owner_env, monkeypatch):
    client, database, _settings, _backend, _project, instance = owner_env
    agent = f"/agents/{instance['id']}"
    _approval(client, agent, "first")
    _approval(client, agent, "second")
    monkeypatch.setattr("app.agent_space_resources.MAX_INTENT_HISTORY_PER_AGENT", 1)
    response = client.post(agent + "/space-approvals/revoke-all", json=BODY)
    assert response.status_code == 503
    assert _revocations(database) == (0, 0)


def test_revocation_and_history_survive_full_core_restart(settings, database, monkeypatch):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from tests.conftest import AUTH
    from tests.test_agent_instances import create_instance, create_project
    from tests.test_computer_observer_startup import _controlled_backend, _enabled_settings

    _controlled_backend(monkeypatch)
    selected = _enabled_settings(settings)
    with TestClient(create_app(selected), headers=AUTH) as client:
        instance = create_instance(client, create_project(client)["id"], "computer_observer")
        agent = f"/agents/{instance['id']}"
        original = _approval(client, agent)
        assert client.post(agent + "/space-approvals/revoke-all", json=BODY).status_code == 200
    with TestClient(create_app(selected), headers=AUTH) as client:
        history = client.get(agent + "/space-activations").json()
        assert history["total"] == 1
        assert history["items"][0]["id"] == original[2]["id"]
        assert history["items"][0]["revoked"] and not history["items"][0]["eligible_for_mediation"]
        blocked = client.post(
            agent + f"/space-intents/{original[1]['id']}/activate",
            json={
                "resource_record_id": original[0]["id"],
            },
        )
        assert blocked.status_code == 409
        repeat = client.post(agent + "/space-approvals/revoke-all", json=BODY).json()
        assert repeat["newly_revoked_intent_ids"] == repeat["newly_revoked_activation_ids"] == []
    assert _revocations(database) == (1, 1)
