"""Actual bounded file reads require an exact, running Core Computer task."""

import asyncio
from dataclasses import replace
from hashlib import sha256

import pytest
from sqlalchemy import select

from app.agent_computer_service import AgentComputerService
from app.agent_computers import ComputerControlState
from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, AgentRegistry
from app.agent_instances import AgentInstanceService
from app.agent_space_intents import AgentSpaceIntentService
from app.agent_space_resources import AgentSpaceResourceService
from app.computer_worker import ComputerRuntime
from app.memory import MemoryService
from app.models import AgentComputerPermission, AuditEvent, Project, utcnow
from app.policy import DomainError, State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    ComputerRunInput,
    ProjectCreate,
)
from app.space_file_gateway import MAX_SPACE_TEXT_BYTES, SpaceFileReadGateway
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_computer_worker
from tests.test_computer_worker import FakeReadBackend, computer_agent_spec


def _fixture(
    database,
    settings,
    *,
    path="notes.txt",
    root_resource="documents",
    space_permissions=("workroom",),
):
    spec = computer_agent_spec().model_copy(update={"space_permissions": space_permissions})
    factory = AgentFactory(AgentRegistry([spec]))
    backend = FakeReadBackend()
    computer = ComputerRuntime(database, backend)
    supervisor = Supervisor(WorkerRegistry(((descriptor_for_computer_worker(), computer),)))
    tasks = TaskService(database, settings, agent_factory=factory, computer_supervisor=supervisor)
    execution_service = AgentExecutionService(database, factory, tasks)
    agents = AgentInstanceService(database, factory, MemoryService(database))
    project = ProjectService(database).create(
        ProjectCreate(name="Space file reader", summary="Bounded read-only Computer task")
    )
    instance = agents.create(AgentInstanceCreate(agent_id=spec.agent_id, project_id=project.id))
    root = settings.runtime_dir / "spaces" / project.id / "workroom" / root_resource
    root.mkdir(parents=True)
    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, instance.id)
        assert permission is not None
        permission.enabled = True
        permission.files = True
        permission.updated_at = utcnow()
    resources = AgentSpaceResourceService(database, factory, settings.runtime_dir)
    resource = resources.register(
        instance_id=instance.id,
        space_id="workroom",
        resource_id=root_resource,
        operations=("list", "read"),
    )
    intents = AgentSpaceIntentService(database, factory)
    intent = intents.create(
        instance_id=instance.id,
        space_id="workroom",
        resource_kind="files",
        resource_id=root_resource,
        operations=("read",),
        ttl_seconds=600,
    )
    activation = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    gateway = SpaceFileReadGateway(database, factory, settings.runtime_dir, computer)
    return {
        "factory": factory,
        "backend": backend,
        "computer": computer,
        "supervisor": supervisor,
        "tasks": tasks,
        "agent_submit": execution_service,
        "agents": agents,
        "project": project,
        "instance": instance,
        "root": root,
        "resources": resources,
        "resource": resource,
        "intents": intents,
        "intent": intent,
        "activation": activation,
        "gateway": gateway,
        "path": path,
    }


def _claim(env, *, path=None):
    relative = env["path"] if path is None else path
    env["agent_submit"].submit(
        env["instance"].id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={
                    "action": "files_read",
                    "path": relative,
                    "space_activation_id": env["activation"].id,
                },
            )
        ),
        None,
    )
    execution = env["tasks"].claim()
    assert execution is not None and execution.kind == "computer_run"
    assert execution.computer_request is not None
    return execution


def _read(env, execution, *, activation_id=None, path=None):
    return asyncio.run(
        env["gateway"].read(
            execution=execution,
            activation_id=activation_id or env["activation"].id,
            relative_path=path or env["path"],
        )
    )


def test_claimed_current_task_reads_only_attested_utf8_file_and_audits_no_content(
    database, settings
):
    env = _fixture(database, settings)
    content = "authorized 📋\n".encode()
    (env["root"] / "notes.txt").write_bytes(content)
    claimed = _claim(env)
    result = _read(env, claimed)
    assert result.text == "authorized 📋\n"
    assert result.bytes_read == len(content)
    assert result.content_sha256 == sha256(content).hexdigest()
    assert result.model_context_admitted is False
    assert result.task_id == claimed.task_id and result.attempt_id == claimed.attempt_id
    assert env["backend"].calls == [
        ("control", env["instance"].id, "")
    ]  # Human-control check, never a backend file read.
    with database.read() as session:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.task_id == claimed.task_id)
        ).all()
        reads = [event for event in events if event.event == "space_file_read_completed"]
        assert len(reads) == 1
        assert reads[0].details["content_sha256"] == result.content_sha256
        assert reads[0].details["bytes"] == len(content)
        assert "text" not in reads[0].details
        assert "notes.txt" not in str(reads[0].details)


def test_distinct_valid_space_activation_cannot_retarget_claimed_task(database, settings):
    """Same AgentInstance's different Space grant cannot substitute for Task-bound grant."""
    env = _fixture(database, settings, space_permissions=("vault", "workroom"))
    (env["root"] / "notes.txt").write_text("workroom-only")

    vault_root = settings.runtime_dir / "spaces" / env["project"].id / "vault" / "documents"
    vault_root.mkdir(parents=True)
    (vault_root / "notes.txt").write_text("vault-only")
    vault_resource = env["resources"].register(
        instance_id=env["instance"].id,
        space_id="vault",
        resource_id="documents",
        operations=("list", "read"),
    )
    vault_intent = env["intents"].create(
        instance_id=env["instance"].id,
        space_id="vault",
        resource_kind="files",
        resource_id="documents",
        operations=("read",),
        ttl_seconds=600,
    )
    vault_activation = env["resources"].activate(
        instance_id=env["instance"].id,
        intent_id=vault_intent.id,
        resource_record_id=vault_resource.id,
    )
    assert vault_activation.id != env["activation"].id

    claimed = _claim(env)
    assert _read(env, claimed).text == "workroom-only"
    with pytest.raises(DomainError, match="activation not bound to Task action"):
        _read(env, claimed, activation_id=vault_activation.id)
    assert all(action == "control" for action, _, _ in env["backend"].calls)
    with database.read() as session:
        events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.task_id == claimed.task_id,
                AuditEvent.event == "space_file_read_completed",
            )
        ).all()
    assert len(events) == 1

    # The second Space is independently usable only with its own claimed Task.
    runner = Runner(
        env["tasks"], computer_supervisor=env["supervisor"], space_file_gateway=env["gateway"]
    )
    asyncio.run(runner._run(claimed))
    assert env["tasks"].get(claimed.task_id).status == State.SUCCEEDED
    env["agent_submit"].submit(
        env["instance"].id,
        AgentTaskCreate(
            task=ComputerRunInput(
                kind="computer_run",
                payload={
                    "action": "files_read",
                    "path": "notes.txt",
                    "space_activation_id": vault_activation.id,
                },
            ),
        ),
        None,
    )
    vault_execution = env["tasks"].claim()
    assert vault_execution is not None
    asyncio.run(runner._run(vault_execution))
    vault_task = env["tasks"].get(vault_execution.task_id)
    assert vault_task.status == State.SUCCEEDED
    assert vault_task.result["files_read"]["text"] == "vault-only"
    assert all(action == "control" for action, _, _ in env["backend"].calls)


def test_missing_revoked_or_retired_confirmation_never_opens_a_file(
    database, settings, monkeypatch
):
    import app.space_file_gateway as module

    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("never read")
    execution = _claim(env)
    openings = []
    real_open = module._open_bounded_utf8

    def spy(*args, **kwargs):
        openings.append(True)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(module, "_open_bounded_utf8", spy)
    with pytest.raises(DomainError, match="activation not bound to Task action"):
        _read(env, execution, activation_id="00000000-0000-4000-8000-000000000000")
    assert openings == []
    env["resources"].revoke(instance_id=env["instance"].id, activation_id=env["activation"].id)
    with pytest.raises(DomainError, match="revoked, expired or invalid"):
        _read(env, execution)
    assert openings == []


def test_retired_resource_denied_without_backend_access(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("secret")
    execution = _claim(env)
    env["resources"].retire(instance_id=env["instance"].id, resource_record_id=env["resource"].id)
    with pytest.raises(DomainError, match="revoked, expired or invalid"):
        _read(env, execution)
    assert all(action != "files_read" for action, _, _ in env["backend"].calls)


def test_human_takeover_denies_file_access_before_open(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("private")
    execution = _claim(env)
    env["backend"].control_state = ComputerControlState(
        holder="human",
        requested=False,
        transitioning=False,
        resumeSnapshotRequired=False,
    )
    with pytest.raises(DomainError, match="human control active"):
        _read(env, execution)
    assert all(action != "files_read" for action, _, _ in env["backend"].calls)


def test_owner_takeover_after_control_snapshot_denies_space_read(database, settings, monkeypatch):
    """The Core takeover may complete after a stale bot-control GET returns."""
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("owner-only")
    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, env["instance"].id)
        assert permission is not None
        permission.browser = True
        permission.updated_at = utcnow()
    execution = _claim(env)
    previous_bot_control = env["backend"].control_state
    computers = AgentComputerService(database, env["factory"], env["backend"])

    async def take_remote(instance_id):
        assert instance_id == env["instance"].id
        env["backend"].control_state = ComputerControlState(
            holder="human",
            requested=False,
            transitioning=False,
            resumeSnapshotRequired=False,
        )
        return env["backend"].control_state

    async def status_after_take(_instance_id):
        return None  # Do not mask or replace the real Core takeover policy.

    async def stale_control_result(instance_id):
        await computers.take(instance_id)
        assert env["backend"].control_state.holder == "human"
        return previous_bot_control

    monkeypatch.setattr(env["backend"], "take", take_remote, raising=False)
    monkeypatch.setattr(computers, "status", status_after_take)
    monkeypatch.setattr(env["backend"], "control", stale_control_result)

    with pytest.raises(DomainError, match="stale|human|control"):
        _read(env, execution)
    with database.read() as session:
        assert not session.scalars(
            select(AuditEvent).where(
                AuditEvent.task_id == execution.task_id,
                AuditEvent.event == "space_file_read_completed",
            )
        ).all()
    assert all(action != "files_read" for action, _, _ in env["backend"].calls)


def test_pending_owner_takeover_blocks_new_tasks_and_file_use(database, settings, monkeypatch):
    """A pre-network owner request wins even while the backend still reports bot."""
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("private")
    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, env["instance"].id)
        assert permission is not None
        permission.browser = True
        permission.updated_at = utcnow()
    execution = _claim(env)
    computers = AgentComputerService(database, env["factory"], env["backend"])
    remote_entered = asyncio.Event()
    finish_remote = asyncio.Event()

    async def pending_take_remote(_instance_id):
        remote_entered.set()
        await finish_remote.wait()
        env["backend"].control_state = ComputerControlState(
            holder="human", requested=False, transitioning=False, resumeSnapshotRequired=False
        )
        return env["backend"].control_state

    async def status_after_take(_instance_id):
        return None

    monkeypatch.setattr(env["backend"], "take", pending_take_remote, raising=False)
    monkeypatch.setattr(computers, "status", status_after_take)

    async def exercise():
        pending = asyncio.create_task(computers.take(env["instance"].id))
        await asyncio.wait_for(remote_entered.wait(), 1)
        assert env["backend"].control_state.holder == "bot"
        try:
            with pytest.raises(DomainError, match="human control active|stale"):
                await env["gateway"].read(
                    execution=execution,
                    activation_id=env["activation"].id,
                    relative_path="notes.txt",
                )
            with pytest.raises(DomainError, match="computer_space_activation_invalid"):
                env["agent_submit"].submit(
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
        finally:
            finish_remote.set()
            await pending

    asyncio.run(exercise())
    with database.read() as session:
        assert not session.scalars(
            select(AuditEvent).where(
                AuditEvent.task_id == execution.task_id,
                AuditEvent.event == "space_file_read_completed",
            )
        ).all()


def test_revocation_during_control_await_denied_before_file_read(database, settings, monkeypatch):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("private")
    execution = _claim(env)
    control = env["backend"].control

    async def revoke_during_control(instance_id):
        env["resources"].revoke(instance_id=env["instance"].id, activation_id=env["activation"].id)
        return await control(instance_id)

    monkeypatch.setattr(env["backend"], "control", revoke_during_control)
    with pytest.raises(DomainError, match="revoked, expired or invalid"):
        _read(env, execution)
    assert all(action != "files_read" for action, _, _ in env["backend"].calls)


def test_stale_computer_permission_denied_after_claim(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("no")
    execution = _claim(env)
    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, env["instance"].id)
        permission.files = False
        permission.updated_at = utcnow()
    with pytest.raises(DomainError, match="computer permission stale"):
        _read(env, execution)


def test_paused_and_resumed_agent_stays_revoked_for_original_intent(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("no")
    execution = _claim(env)
    env["agents"].transition(env["instance"].id, AgentInstanceTransition(action="pause"))
    with pytest.raises(DomainError, match="agent or project stale"):
        _read(env, execution)
    env["agents"].transition(env["instance"].id, AgentInstanceTransition(action="resume"))
    with pytest.raises(DomainError, match="binding or lifecycle stale"):
        _read(env, execution)


def test_project_pause_refuses_read(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("no")
    execution = _claim(env)
    with database.transaction() as session:
        project = session.get(Project, env["project"].id)
        project.status = "paused"
    with pytest.raises(DomainError, match="agent or project stale"):
        _read(env, execution)


def test_cancelled_task_cannot_read_and_must_not_touch_file(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("no")
    execution = _claim(env)
    env["tasks"].cancel(execution.task_id)
    with pytest.raises(DomainError, match="task or attempt no longer active"):
        _read(env, execution)


def test_execution_copy_or_path_substitution_denied(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("visible")
    (env["root"] / "other.txt").write_text("secret")
    execution = _claim(env)
    with pytest.raises(DomainError, match="exact task authority"):
        _read(env, replace(execution, timeout_seconds=execution.timeout_seconds + 1))
    with pytest.raises(DomainError, match="exact task authority"):
        _read(env, execution, path="other.txt")
    with pytest.raises(DomainError, match="task or attempt no longer active"):
        _read(env, replace(execution, attempt_id="00000000-0000-4000-8000-000000000000"))
    assert _read(env, execution).text == "visible"


@pytest.mark.parametrize(
    "path",
    (
        "../secrets.txt",
        "/etc/passwd",
        "nested//notes.txt",
        ".hidden.txt",
        "UP/../a.txt",
        "text%2Fsecret.txt",
        "a\\b.txt",
        "notes.txt/",
        "notes.exe",
        "notes.txt\x00",
    ),
)
def test_noncanonical_read_paths_refused_before_file_use(database, settings, path):
    env = _fixture(database, settings)
    execution = _claim(env)
    with pytest.raises(DomainError, match="Space file path is not canonical"):
        _read(env, execution, path=path)


def test_symlink_file_and_nested_directory_denied(database, settings):
    env = _fixture(database, settings, path="nested/note.txt")
    root = env["root"]
    external = settings.runtime_dir / "private.txt"
    external.write_text("private")
    (root / "nested").symlink_to(settings.runtime_dir, target_is_directory=True)
    execution = _claim(env)
    with pytest.raises(DomainError, match="unavailable or unsafe"):
        _read(env, execution)
    (root / "nested").unlink()
    (root / "nested").mkdir()
    (root / "nested" / "note.txt").symlink_to(external)
    with pytest.raises(DomainError, match="unavailable or unsafe"):
        _read(env, execution)


def test_hardlinks_and_oversized_or_binary_files_denied(database, settings):
    env = _fixture(database, settings)
    path = env["root"] / "notes.txt"
    path.write_text("private")
    (env["root"] / "shadow.txt").hardlink_to(path)
    execution = _claim(env)
    with pytest.raises(DomainError, match="unavailable or unsafe"):
        _read(env, execution)
    (env["root"] / "shadow.txt").unlink()
    path.write_bytes(b"a" * (MAX_SPACE_TEXT_BYTES + 1))
    with pytest.raises(DomainError, match="unavailable or unsafe"):
        _read(env, execution)
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(DomainError, match="unavailable or unsafe"):
        _read(env, execution)


def test_root_inode_changed_after_activation_denied(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("unsafe")
    execution = _claim(env)
    root = env["root"]
    moved = root.with_name("moved")
    root.rename(moved)
    root.mkdir()
    with pytest.raises(DomainError, match="binding or lifecycle stale"):
        _read(env, execution)


def test_directory_move_after_fd_open_denied_before_data_return(database, settings, monkeypatch):
    import app.space_file_gateway as module

    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("cannot escape")
    execution = _claim(env)
    moved = env["root"].with_name("relocated")
    original_read = module.os.read
    changed = False

    def move_root_at_read(fd, count):
        nonlocal changed
        if not changed:
            changed = True
            env["root"].rename(moved)
            env["root"].mkdir()
        return original_read(fd, count)

    monkeypatch.setattr(module.os, "read", move_root_at_read)
    with pytest.raises(DomainError, match="unavailable or unsafe"):
        _read(env, execution)
    assert changed


def test_backend_identity_change_denied_even_when_file_permission_unchanged(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("no")
    execution = _claim(env)
    env["backend"].identity = "b" * 64
    with pytest.raises(DomainError, match="computer permission stale"):
        _read(env, execution)


def test_no_delegation_or_other_task_kind_auto_grants_file_access(database, settings):
    env = _fixture(database, settings)
    (env["root"] / "notes.txt").write_text("no")
    execution = _claim(env)
    with pytest.raises(DomainError, match="claimed Computer execution"):
        _read(env, replace(execution, kind="research_run"))
    assert _read(env, execution).text == "no"
