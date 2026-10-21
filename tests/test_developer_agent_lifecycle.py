"""Coding Agent lifecycle fences for approved Developer work."""

import asyncio

import pytest

from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, default_agent_registry
from app.agent_instances import AgentInstanceService
from app.developer import DeveloperRuntime
from app.memory import MemoryService
from app.policy import State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    DeveloperRunInput,
    ProjectCreate,
)
from app.tasks import TaskService
from tests.test_independent_review import review_runtime


def claimed_coding_task(database, developer_case, monkeypatch):
    runtime, producer, _reviewer = review_runtime(developer_case)
    # Host-independent fixture; the Developer request still uses its normal
    # source, approval, claim, checkout and fake Worker paths.
    monkeypatch.setattr(DeveloperRuntime, "validation_identity", lambda self: "a" * 64)
    factory = AgentFactory(default_agent_registry())
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database, runtime.settings, developer_runtime=runtime, agent_factory=factory
    )
    execution = AgentExecutionService(database, factory, tasks)
    project = ProjectService(database).create(
        ProjectCreate(name="Developer lifecycle", summary="Disposable Agent work.")
    )
    instance = instances.create(AgentInstanceCreate(agent_id="coding", project_id=project.id))
    task, _ = execution.submit(
        instance.id,
        AgentTaskCreate(
            task=DeveloperRunInput(
                kind="developer_run", payload={"instruction": "Write one disposable file."}
            )
        ),
        f"coding-agent-{instance.id}",
    )
    assert task.status == State.AWAITING_APPROVAL
    tasks.decide(task.approvals[0].id, "approve")
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == task.id
    assert claimed.agent_execution is not None
    return runtime, producer, instances, tasks, instance, claimed


def test_agent_pause_during_checkout_prevents_developer_worker_launch(
    database, developer_case, monkeypatch
):
    runtime, producer, instances, tasks, instance, claimed = claimed_coding_task(
        database, developer_case, monkeypatch
    )
    original_prepare = runtime.prepare

    async def pause_after_checkout(repo, request):
        await original_prepare(repo, request)
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))

    monkeypatch.setattr(runtime, "prepare", pause_after_checkout)
    asyncio.run(Runner(tasks, developer_runtime=runtime)._run(claimed))
    current = tasks.get(claimed.task_id)
    assert producer.calls == []
    assert current.status == State.CANCELLED
    assert current.error == "agent_execution_invalidated"
    assert current.result is None
    assert current.attempts[0].result is None


@pytest.mark.parametrize("resume", [False, True])
def test_agent_pause_during_developer_worker_suppresses_result(
    database, developer_case, monkeypatch, resume
):
    runtime, producer, instances, tasks, instance, claimed = claimed_coding_task(
        database, developer_case, monkeypatch
    )

    async def write_then_pause(_request, repo):
        (repo / "DEVELOPER_SMOKE.txt").write_text("one disposable change\n")
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        if resume:
            instances.transition(instance.id, AgentInstanceTransition(action="resume"))

    producer.action = write_then_pause
    asyncio.run(Runner(tasks, developer_runtime=runtime)._run(claimed))
    current = tasks.get(claimed.task_id)
    assert len(producer.calls) == 1
    assert (producer.calls[0] / "DEVELOPER_SMOKE.txt").read_text() == ("one disposable change\n")
    assert current.status == State.CANCELLED
    assert current.error == "agent_execution_invalidated"
    assert current.result is None
    assert current.attempts[0].result is None


def test_agent_pause_inside_worker_blocks_late_native_spawn(database, developer_case, monkeypatch):
    from app.developer_process import run_process

    runtime, producer, instances, tasks, instance, claimed = claimed_coding_task(
        database, developer_case, monkeypatch
    )
    observed = []

    async def pause_then_spawn(_request, repo):
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        result = await run_process(
            ["/usr/bin/python3", "-c", "open('LATE_NATIVE_WRITE', 'w').write('late')"],
            cwd=repo,
            timeout=5,
        )
        observed.append(result.exit_code)

    producer.action = pause_then_spawn
    asyncio.run(Runner(tasks, developer_runtime=runtime)._run(claimed))
    current = tasks.get(claimed.task_id)
    assert len(producer.calls) == 1
    assert not (producer.calls[0] / "LATE_NATIVE_WRITE").exists()
    assert observed == []
    assert current.status == State.CANCELLED
    assert current.result is None


def test_opencode_prompt_denied_after_pause_without_blocking_cleanup_or_other_task(
    database, developer_case, monkeypatch
):
    from app import opencode_worker

    runtime, producer, instances, tasks, instance, claimed = claimed_coding_task(
        database, developer_case, monkeypatch
    )
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            sent.append(request.full_url)
            return Response()

    monkeypatch.setattr(opencode_worker, "build_opener", lambda *_args: Opener())
    paused = asyncio.Event()
    release = asyncio.Event()

    async def pause_then_prompt(_request, _repo):
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        paused.set()
        await release.wait()
        try:
            await opencode_worker._api_json_async(
                "http://127.0.0.1:12345",
                "fixture",
                "POST",
                "/api/session/ses_worker/prompt",
                payload={"text": "late"},
            )
        finally:
            # Owned session cleanup must still be able to send interrupt.
            await opencode_worker._api_json_async(
                "http://127.0.0.1:12345", "fixture", "POST", "/api/session/ses_worker/interrupt"
            )

    producer.action = pause_then_prompt

    async def run_with_unrelated_task():
        operation = asyncio.create_task(Runner(tasks, developer_runtime=runtime)._run(claimed))
        await paused.wait()
        # A sibling task has no Agent authority callback from this worker.
        await asyncio.to_thread(
            opencode_worker._api_json,
            "http://127.0.0.1:12345",
            "fixture",
            "POST",
            "/api/session/ses_standalone/prompt",
            payload={"text": "separate"},
        )
        release.set()
        await operation

    asyncio.run(run_with_unrelated_task())
    assert sent == [
        "http://127.0.0.1:12345/api/session/ses_standalone/prompt",
        "http://127.0.0.1:12345/api/session/ses_worker/interrupt",
    ]
    current = tasks.get(claimed.task_id)
    assert current.status == State.CANCELLED
    assert current.result is None


def test_agent_pause_before_validation_blocks_late_native_process(
    database, developer_case, monkeypatch
):
    from app.developer_process import run_process

    runtime, producer, instances, tasks, instance, claimed = claimed_coding_task(
        database, developer_case, monkeypatch
    )

    async def write_change(_request, repo):
        (repo / "DEVELOPER_SMOKE.txt").write_text("one disposable change\n")

    async def pause_then_validate(repo, _request):
        instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        await run_process(
            ["/usr/bin/python3", "-c", "open('LATE_VALIDATION_WRITE', 'w').write('late')"],
            cwd=repo,
            timeout=5,
        )
        pytest.fail("late validation process was allowed")

    producer.action = write_change
    monkeypatch.setattr(runtime, "validate", pause_then_validate)
    asyncio.run(Runner(tasks, developer_runtime=runtime)._run(claimed))
    current = tasks.get(claimed.task_id)
    assert len(producer.calls) == 1
    assert not (producer.calls[0] / "LATE_VALIDATION_WRITE").exists()
    assert current.status == State.CANCELLED
    assert current.error == "agent_execution_invalidated"
    assert current.result is None


def test_agent_operator_cancel_suppresses_developer_result(database, developer_case, monkeypatch):
    runtime, producer, _instances, tasks, _instance, claimed = claimed_coding_task(
        database, developer_case, monkeypatch
    )

    async def write_then_cancel(_request, repo):
        (repo / "DEVELOPER_SMOKE.txt").write_text("one disposable change\n")
        tasks.cancel(claimed.task_id)

    producer.action = write_then_cancel
    asyncio.run(Runner(tasks, developer_runtime=runtime)._run(claimed))
    current = tasks.get(claimed.task_id)
    assert len(producer.calls) == 1
    assert current.status == State.CANCELLED
    assert current.error == "cancelled_by_operator"
    assert current.result is None
    assert current.attempts[0].result is None
