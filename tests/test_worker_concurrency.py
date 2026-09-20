"""v0.9 worker-capacity scheduling and concurrent runner regressions."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.developer_contracts import (
    DeveloperRequest,
    Limits,
    WorkerDescriptor,
    WorkerIdentity,
    WorkerSelectionSnapshot,
)
from app.developer_process import DeveloperError
from app.policy import State
from app.runner import Runner
from app.schemas import DeveloperRunInput, EchoInput
from app.tasks import TaskService
from app.worker_authority import AUTHORITY_POLICY_VERSION


def _limits(timeout: float) -> Limits:
    return Limits(
        overall_seconds=min(timeout, 30),
        worker_seconds=10,
        validation_seconds=10,
        git_seconds=5,
        stdout_bytes=4096,
        stderr_bytes=4096,
        line_bytes=4096,
        events=64,
        diff_bytes=4096,
        changed_files=20,
    )


def _request(worker_id: str, instruction: str, timeout: float, *, max_concurrency: int = 1):
    descriptor = WorkerDescriptor(
        worker_id=worker_id,
        role="developer",
        capabilities=("zelo_core_development",),
        authority=("workspace-write",),
        implementation=worker_id,
        priority=100,
        max_concurrency=max_concurrency,
    )
    return DeveloperRequest(
        instruction=instruction,
        source_repository="/trusted/source",
        workspace_root="/trusted/workspaces/developer",
        validation_identity="validation-test",
        base_sha="a" * 40,
        worker=WorkerIdentity(
            implementation=worker_id,
            config_version="test",
            cli_version="test",
            capability_profile="test",
            executable=f"/trusted/{worker_id}",
            executable_sha256="b" * 64,
            code_mode_host=f"/trusted/{worker_id}-host",
            code_mode_host_sha256="c" * 64,
            auth_location="none",
        ),
        worker_selection=WorkerSelectionSnapshot(
            descriptor=descriptor,
            registry_version="d" * 64,
            routing_policy="test-routing-v1",
            authority_profile="workspace-write-no-network-v1",
            authority_policy_version=AUTHORITY_POLICY_VERSION,
        ),
        sandbox_version="test-sandbox",
        limits=_limits(timeout),
    )


def _result(request: DeveloperRequest) -> dict:
    now = datetime.now(UTC)
    return {
        "worker": request.worker.implementation,
        "worker_version": request.worker.cli_version,
        "worker_config_version": request.worker.config_version,
        "base_sha": request.base_sha,
        "worktree": f"{request.worker_selection.descriptor.worker_id}/repo",
        "worker_exit_code": 0,
        "worker_summary": "done",
        "usage": {},
        "nonfatal_error_items": 0,
        "changed_files": [],
        "ignored_generated_files": [],
        "git_status": "",
        "diff_stat": "",
        "diff": "",
        "head": request.base_sha,
        "head_changed": False,
        "validation": [],
        "validation_passed": True,
        "started_at": now.isoformat(),
        "worker_started_at": now.isoformat(),
        "worker_completed_at": now.isoformat(),
        "validation_completed_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "duration_seconds": 0.01,
        "error": None,
        "protocol_diagnostic": None,
        "boundary_error": None,
        "original_error": None,
        "cancelled": False,
    }


class CapacityRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.started: set[str] = set()
        self.both_started = asyncio.Event()
        self.release = asyncio.Event()

    def request(self, instruction: str, profile: str, timeout: float) -> DeveloperRequest:
        assert profile == "zelo_core"
        worker_id = "alpha" if instruction.startswith("alpha") else "beta"
        return _request(worker_id, instruction, timeout)

    def compatible(self, request: DeveloperRequest, timeout: float) -> bool:
        return request.limits == _limits(timeout)

    def approval_compatible(self, request: DeveloperRequest, timeout: float) -> bool:
        return self.compatible(request, timeout)

    async def run(self, task_id, execution_id, request, emit):
        self.started.add(request.worker_selection.descriptor.worker_id)
        if len(self.started) >= 2:
            self.both_started.set()
        await self.release.wait()
        return _result(request)


def _approved(service: TaskService, instruction: str):
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": instruction}), None
    )
    service.decide(task.approvals[0].id, "approve")
    return service.get(task.id)


def test_scheduler_enforces_worker_capacity_and_can_skip_to_another_worker(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha_one = _approved(service, "alpha-one")
    alpha_two = _approved(service, "alpha-two")
    beta = _approved(service, "beta-one")

    first = service.claim()
    assert first is not None and first.task_id == alpha_one.id
    second = service.claim()
    assert second is not None and second.task_id == beta.id
    assert service.claim() is None

    assert first.developer_request is not None
    service.finish(first, State.SUCCEEDED, _result(first.developer_request))
    third = service.claim()
    assert third is not None and third.task_id == alpha_two.id


def test_non_developer_task_is_exclusive_barrier(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha = _approved(service, "alpha-one")
    first = service.claim()
    assert first is not None and first.task_id == alpha.id

    echo, _ = service.submit(EchoInput(kind="echo", payload={"text": "barrier"}), None)
    _approved(service, "beta-one")
    assert service.claim() is None

    assert first.developer_request is not None
    service.finish(first, State.SUCCEEDED, _result(first.developer_request))
    next_execution = service.claim()
    assert next_execution is not None and next_execution.task_id == echo.id


def test_runner_overlaps_different_workers(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha = _approved(service, "alpha-one")
    beta = _approved(service, "beta-one")

    async def exercise():
        runner = Runner(service, developer_runtime=runtime)
        runner.start()
        await asyncio.wait_for(runtime.both_started.wait(), timeout=2)
        assert service.get(alpha.id).status == State.RUNNING
        assert service.get(beta.id).status == State.RUNNING
        assert set(runner.operations) == {alpha.id, beta.id}
        runtime.release.set()
        for _ in range(100):
            if (
                service.get(alpha.id).status == State.SUCCEEDED
                and service.get(beta.id).status == State.SUCCEEDED
            ):
                break
            await asyncio.sleep(0.01)
        assert service.get(alpha.id).status == State.SUCCEEDED
        assert service.get(beta.id).status == State.SUCCEEDED
        await runner.stop()

    asyncio.run(exercise())


def test_runner_failure_interrupts_already_claimed_developer_work(
    settings, database, monkeypatch
):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    task = _approved(service, "alpha-one")
    original_claim = service.claim
    calls = 0

    def fail_after_first_claim():
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_claim()
        raise RuntimeError("injected scheduler failure")

    monkeypatch.setattr(service, "claim", fail_after_first_claim)

    async def exercise():
        runner = Runner(service, developer_runtime=runtime)
        runner.start()
        await asyncio.wait_for(runner.loop_task, timeout=2)
        assert runner.failed
        assert not runner.ready
        current = service.get(task.id)
        assert current.status == State.INTERRUPTED
        assert current.error == "runner_failed"
        assert runner.operations == {}

    asyncio.run(exercise())


def test_runtime_worker_slots_enforce_descriptor_ceiling(developer_case):
    request = developer_case.request("test", "zelo_core", 30)
    first = developer_case.worker_lock(request)
    try:
        with pytest.raises(DeveloperError, match="developer_worker_capacity_exhausted"):
            developer_case.worker_lock(request)
    finally:
        first.close()

    descriptor = request.worker_selection.descriptor.model_copy(update={"max_concurrency": 2})
    selection = request.worker_selection.model_copy(update={"descriptor": descriptor})
    two_slot_request = request.model_copy(update={"worker_selection": selection})
    first = developer_case.worker_lock(two_slot_request)
    second = developer_case.worker_lock(two_slot_request)
    try:
        with pytest.raises(DeveloperError, match="developer_worker_capacity_exhausted"):
            developer_case.worker_lock(two_slot_request)
    finally:
        second.close()
        first.close()
