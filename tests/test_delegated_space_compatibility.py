"""Pre-v3 nondelegated request identity and persisted snapshot compatibility."""

import pytest

from app.computer_contracts import ComputerExecutionSpec
from app.models import Task
from app.policy import DomainError
from app.schemas import AgentTaskCreate, ComputerPayload, ComputerRunInput
from tests.test_delegated_space_files import _delegated_env

PARENT_FIELDS = {
    "parent_space_activation_id",
    "parent_agent_instance_id",
    "parent_permission_version",
}


def test_preupgrade_nondelegated_idempotency_key_replays_after_upgrade(
    database, settings, client, monkeypatch
):
    env = _delegated_env(database, settings, client)
    request = AgentTaskCreate(
        task=ComputerRunInput(kind="computer_run", payload={"action": "browser_snapshot"})
    )
    original_request = ComputerRunInput.model_dump
    original_payload = ComputerPayload.model_dump
    original_spec = ComputerExecutionSpec.model_dump

    # Model only the exact pre-v3 wire representation at the serialization
    # boundary. Actual Core submission, fingerprints, persistence and replay
    # checks run unchanged; no authority check or DB trigger is patched.
    def old_request(self, *args, **kwargs):
        data = original_request(self, *args, **kwargs)
        data["payload"].pop("parent_space_activation_id", None)
        return data

    def old_payload(self, *args, **kwargs):
        data = original_payload(self, *args, **kwargs)
        data.pop("parent_space_activation_id", None)
        return data

    def old_spec(self, *args, **kwargs):
        data = original_spec(self, *args, **kwargs)
        for field in PARENT_FIELDS:
            data.pop(field, None)
        return data

    with monkeypatch.context() as legacy:
        legacy.setattr(ComputerRunInput, "model_dump", old_request)
        legacy.setattr(ComputerPayload, "model_dump", old_payload)
        legacy.setattr(ComputerExecutionSpec, "model_dump", old_spec)
        initial, created = env["submits"].submit(env["child"].id, request, "pre-v3-key")
        assert created
    try:
        replay, created = env["submits"].submit(env["child"].id, request, "pre-v3-key")
    except DomainError as error:
        pytest.fail(f"Identical legacy request no longer replays: {error}")
    assert not created and replay.id == initial.id
    assert env["tasks"].list_tasks(limit=100, offset=0).total == 1


def test_ordinary_v2_task_never_persists_v3_only_fields(database, settings, client):
    env = _delegated_env(database, settings, client)
    request = AgentTaskCreate(
        task=ComputerRunInput(kind="computer_run", payload={"action": "browser_snapshot"})
    )
    submitted, _ = env["submits"].submit(env["child"].id, request, None)
    with database.read() as session:
        task = session.get(Task, submitted.id)
        assert task.computer_spec["schema_version"] == "zelo-computer-execution-v2"
        assert not PARENT_FIELDS.intersection(task.computer_spec), task.computer_spec.keys()
        assert "parent_space_activation_id" not in task.payload
    assert "parent_space_activation_id" not in request.task.model_dump(mode="json")["payload"]
