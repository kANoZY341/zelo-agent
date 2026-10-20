"""A delegated Space read needs both current grants at every execution boundary."""

import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models import (
    AgentComputerPermission,
    AuditEvent,
    Campaign,
    utcnow,
)
from app.policy import DomainError, State
from app.schemas import AgentDelegationRevoke, AgentInstanceTransition
from tests.test_delegated_space_files import _delegated_env, _request


def _change(env, database, cause, monkeypatch=None):
    if cause in ("parent_revoke", "child_revoke"):
        key = "parent" if cause.startswith("parent") else "child"
        env["resources"].revoke(instance_id=env[key].id, activation_id=env[key + "_approval"].id)
    elif cause == "parent_pause":
        env["agents"].transition(env["parent"].id, AgentInstanceTransition(action="pause"))
    elif cause == "delegation_revoke":
        env["delegations"].revoke(
            env["delegation"].id, AgentDelegationRevoke(reason="test owner withdrawal")
        )
    elif cause == "budget_expired":
        future = env["budget"].expires_at + timedelta(seconds=1)
        monkeypatch.setattr("app.tasks.utcnow", lambda: future)
        monkeypatch.setattr("app.space_file_gateway.utcnow", lambda: future)
    else:
        with database.transaction() as session:
            if cause == "campaign_pause":
                session.get(Campaign, env["campaign"]["id"]).status = "paused"
            elif cause == "parent_permission":
                row = session.get(AgentComputerPermission, env["parent"].id)
                row.files = False
                row.updated_at = utcnow()
            else:
                raise AssertionError(cause)


@pytest.mark.parametrize(
    "cause",
    [
        "parent_revoke",
        "child_revoke",
        "parent_pause",
        "delegation_revoke",
        "campaign_pause",
        "budget_expired",
        "parent_permission",
    ],
)
@pytest.mark.parametrize("stage", ["submit", "claim", "during_control"])
def test_delegated_work_rechecks_live_authority(
    database, settings, client, monkeypatch, cause, stage
):
    env = _delegated_env(database, settings, client)
    body = _request(env)
    if stage == "submit":
        _change(env, database, cause, monkeypatch)
        with pytest.raises(DomainError):
            env["submits"].submit(env["child"].id, body, None)
        assert env["tasks"].list_tasks(limit=100, offset=0).total == 0
        assert env["backend"].calls == []
        return
    task, _ = env["submits"].submit(env["child"].id, body, None)
    if stage == "claim":
        _change(env, database, cause, monkeypatch)
        assert env["tasks"].claim() is None
        cancelled = env["tasks"].get(task.id)
        assert cancelled.status == State.CANCELLED and cancelled.attempts == []
        assert env["backend"].calls == []
        return
    claimed = env["tasks"].claim()
    assert claimed is not None
    original = env["backend"].control
    injected = False

    async def interrupt(instance_id):
        nonlocal injected
        captured = await original(instance_id)
        if not injected:
            injected = True
            _change(env, database, cause, monkeypatch)
        return captured

    monkeypatch.setattr(env["backend"], "control", interrupt)
    asyncio.run(env["runner"]._run(claimed))
    done = env["tasks"].get(task.id)
    assert done.status == State.FAILED and done.result is None
    assert injected, "The intended authority change must occur inside the backend await"
    assert done.error == "computer_space_read_denied"
    assert all(call[0] == "control" for call in env["backend"].calls)
    with database.read() as session:
        completed = session.scalars(
            select(AuditEvent).where(
                AuditEvent.task_id == task.id,
                AuditEvent.event.in_(("space_file_read_completed", "space_file_list_completed")),
            )
        ).all()
        assert completed == []


def test_missing_or_wrong_parent_confirmation_cannot_inherit_space(database, settings, client):
    env = _delegated_env(database, settings, client)
    for parent in (None, "00000000-0000-4000-8000-000000000001"):
        with pytest.raises(DomainError):
            env["submits"].submit(
                env["child"].id, _request(env, parent_space_activation_id=parent), None
            )
    with pytest.raises(ValidationError):
        _request(env, parent_space_activation_id=env["child_approval"].id)
    with pytest.raises(ValidationError):
        _request(env, "browser_snapshot")
    assert env["tasks"].list_tasks(limit=100, offset=0).total == 0
    assert env["backend"].calls == []


def test_two_valid_parent_and_child_resources_cannot_be_combined(database, settings, client):
    env = _delegated_env(database, settings, client)
    other = env["resources"].provision(
        instance_id=env["parent"].id,
        space_id="workroom",
        resource_id="other",
        operations=("read",),
        files=(("notes.txt", "not the child resource"),),
    )
    intent = env["resources"].intents.create(
        instance_id=env["parent"].id,
        space_id="workroom",
        resource_kind="files",
        resource_id="other",
        operations=("read",),
        ttl_seconds=600,
    )
    parent = env["resources"].activate(
        instance_id=env["parent"].id, intent_id=intent.id, resource_record_id=other.id
    )
    with pytest.raises(DomainError):
        env["submits"].submit(
            env["child"].id, _request(env, parent_space_activation_id=parent.id), None
        )
    assert env["tasks"].list_tasks(limit=100, offset=0).total == 0


def test_retry_after_parent_withdrawal_does_not_create_new_attempt(database, settings, client):
    env = _delegated_env(database, settings, client)
    task, _ = env["submits"].submit(env["child"].id, _request(env), None)
    env["tasks"].cancel(task.id)
    before = env["tasks"].get(task.id)
    _change(env, database, "parent_revoke")
    with pytest.raises(DomainError):
        env["tasks"].retry(task.id)
    after = env["tasks"].get(task.id)
    assert after.status == State.CANCELLED and after.action_version == before.action_version
    assert after.attempts == before.attempts == []
