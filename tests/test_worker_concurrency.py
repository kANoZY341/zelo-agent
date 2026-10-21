"""v0.9 worker-capacity scheduling and concurrent runner regressions."""

import asyncio
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.developer import DeveloperRuntime
from app.developer_contracts import (
    DeveloperRequest,
    Limits,
    WorkerDescriptor,
    WorkerIdentity,
    WorkerSelectionSnapshot,
)
from app.developer_process import DeveloperError
from app.models import Task, utcnow
from app.policy import State
from app.runner import Runner
from app.schemas import DeveloperRunInput, EchoInput
from app.tasks import (
    CAPACITY_BACKOFF_INITIAL_SECONDS,
    CAPACITY_BACKOFF_MAX_SECONDS,
    TaskService,
    capacity_backoff_seconds,
)
from app.worker_authority import AUTHORITY_POLICY_VERSION
from tests.developer_helpers import FakeWorker


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


def _hold_worker_slot(
    workspace_root: str,
    source_repository: str,
    worker_id: str,
    max_concurrency: int,
    started,
    release,
    results,
):
    settings = Settings(_env_file=None, workspace_root=Path(workspace_root))
    runtime = DeveloperRuntime(settings, FakeWorker(), Path(source_repository))
    try:
        lock = runtime.worker_lock(worker_id, max_concurrency)
    except Exception as error:  # pragma: no cover - exercised in subprocess
        results.put(f"error:{type(error).__name__}:{error}")
        started.set()
        return
    results.put("locked")
    started.set()
    release.wait(5)
    lock.close()
    results.put("released")


class CapacityRuntime:
    def __init__(
        self,
        settings,
        *,
        capacities: dict[str, int] | None = None,
        error: str | None = None,
    ):
        self.settings = settings
        self.capacities = capacities or {"alpha": 1, "beta": 1}
        self.error = error
        self.started: set[str] = set()
        self.started_count = 0
        self.both_started = asyncio.Event()
        self.release = asyncio.Event()

    def request(
        self,
        instruction: str,
        profile: str,
        timeout: float,
        *,
        running_counts: dict[str, int] | None = None,
    ) -> DeveloperRequest:
        assert profile == "zelo_core"
        self.last_running_counts = dict(running_counts or {})
        worker_id = "alpha" if instruction.startswith("alpha") else "beta"
        return _request(
            worker_id,
            instruction,
            timeout,
            max_concurrency=self.capacities.get(worker_id, 1),
        )

    def compatible(self, request: DeveloperRequest, timeout: float) -> bool:
        return request.limits == _limits(timeout)

    def approval_compatible(self, request: DeveloperRequest, timeout: float) -> bool:
        return self.compatible(request, timeout)

    @staticmethod
    def _require_work_authority(authorize_work):
        if authorize_work is not None and not authorize_work():
            raise DeveloperError("developer_agent_authority_invalid")

    async def run(self, task_id, execution_id, request, emit, authorize_work=None):
        if self.error is not None:
            raise DeveloperError(self.error)
        self._require_work_authority(authorize_work)
        self.started.add(request.worker_selection.descriptor.worker_id)
        self.started_count += 1
        if self.started_count >= 2:
            self.both_started.set()
        await self.release.wait()
        self._require_work_authority(authorize_work)
        return _result(request)


class ThreeWorkerRuntime(CapacityRuntime):
    def __init__(self, settings):
        super().__init__(
            settings,
            capacities={"codex_core": 1, "claude_core": 1, "opencode_core": 1},
        )
        self.all_started = asyncio.Event()

    def request(
        self,
        instruction: str,
        profile: str,
        timeout: float,
        *,
        running_counts: dict[str, int] | None = None,
    ) -> DeveloperRequest:
        assert profile == "zelo_core"
        self.last_running_counts = dict(running_counts or {})
        worker_id = instruction.split("-", 1)[0]
        if worker_id not in {"codex_core", "claude_core", "opencode_core"}:
            raise AssertionError("unexpected worker instruction")
        return _request(worker_id, instruction, timeout)

    async def run(self, task_id, execution_id, request, emit, authorize_work=None):
        self._require_work_authority(authorize_work)
        self.started.add(request.worker_selection.descriptor.worker_id)
        self.started_count += 1
        if self.started_count >= 3:
            self.all_started.set()
        await self.release.wait()
        self._require_work_authority(authorize_work)
        return _result(request)


def _approved(service: TaskService, instruction: str):
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": instruction}), None
    )
    service.decide(task.approvals[0].id, "approve")
    return service.get(task.id)


def _set_claim_order(database, *tasks):
    # Scheduler order is persisted (created_at, id), not request submission order.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with database.transaction() as session:
        for seconds, task in enumerate(tasks):
            session.get(Task, task.id).created_at = base + timedelta(seconds=seconds)


def test_scheduler_enforces_worker_capacity_and_can_skip_to_another_worker(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha_one = _approved(service, "alpha-one")
    alpha_two = _approved(service, "alpha-two")
    beta = _approved(service, "beta-one")
    _set_claim_order(database, alpha_one, alpha_two, beta)

    first = service.claim()
    assert first is not None and first.task_id == alpha_one.id
    second = service.claim()
    assert second is not None and second.task_id == beta.id
    assert service.claim() is None

    assert first.developer_request is not None
    service.finish(first, State.SUCCEEDED, _result(first.developer_request))
    third = service.claim()
    assert third is not None and third.task_id == alpha_two.id


def test_submission_routes_with_current_running_worker_counts(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    first = _approved(service, "alpha-one")
    execution = service.claim()
    assert execution is not None and execution.task_id == first.id

    service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "beta-one"}), None
    )
    assert runtime.last_running_counts == {"alpha": 1}


def test_non_developer_task_is_exclusive_barrier(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha = _approved(service, "alpha-one")
    first = service.claim()
    assert first is not None and first.task_id == alpha.id

    echo, _ = service.submit(EchoInput(kind="echo", payload={"text": "barrier"}), None)
    beta = _approved(service, "beta-one")
    _set_claim_order(database, echo, beta)
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


def test_runner_overlaps_codex_claude_and_opencode_workers(settings, database):
    runtime = ThreeWorkerRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    tasks = [
        _approved(service, "codex_core-task"),
        _approved(service, "claude_core-task"),
        _approved(service, "opencode_core-task"),
    ]

    async def exercise():
        runner = Runner(service, developer_runtime=runtime)
        runner.start()
        await asyncio.wait_for(runtime.all_started.wait(), timeout=2)
        assert runtime.started == {"codex_core", "claude_core", "opencode_core"}
        assert all(service.get(task.id).status == State.RUNNING for task in tasks)
        assert set(runner.operations) == {task.id for task in tasks}

        runtime.release.set()
        for _ in range(100):
            if all(service.get(task.id).status == State.SUCCEEDED for task in tasks):
                break
            await asyncio.sleep(0.01)
        assert all(service.get(task.id).status == State.SUCCEEDED for task in tasks)
        await runner.stop()

    asyncio.run(exercise())


def test_scheduler_allows_same_worker_up_to_max_concurrency(settings, database):
    runtime = CapacityRuntime(settings, capacities={"alpha": 2, "beta": 1})
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha_one = _approved(service, "alpha-one")
    alpha_two = _approved(service, "alpha-two")
    alpha_three = _approved(service, "alpha-three")
    _set_claim_order(database, alpha_one, alpha_two, alpha_three)

    first = service.claim()
    second = service.claim()
    assert first is not None and first.task_id == alpha_one.id
    assert second is not None and second.task_id == alpha_two.id
    assert service.claim() is None

    assert first.developer_request is not None
    service.finish(first, State.SUCCEEDED, _result(first.developer_request))
    third = service.claim()
    assert third is not None and third.task_id == alpha_three.id


def test_runner_overlaps_same_worker_when_ceiling_allows(settings, database):
    runtime = CapacityRuntime(settings, capacities={"alpha": 2, "beta": 1})
    service = TaskService(database, settings, developer_runtime=runtime)
    alpha_one = _approved(service, "alpha-one")
    alpha_two = _approved(service, "alpha-two")

    async def exercise():
        runner = Runner(service, developer_runtime=runtime)
        runner.start()
        await asyncio.wait_for(runtime.both_started.wait(), timeout=2)
        assert service.get(alpha_one.id).status == State.RUNNING
        assert service.get(alpha_two.id).status == State.RUNNING
        assert set(runner.operations) == {alpha_one.id, alpha_two.id}
        runtime.release.set()
        for _ in range(100):
            if (
                service.get(alpha_one.id).status == State.SUCCEEDED
                and service.get(alpha_two.id).status == State.SUCCEEDED
            ):
                break
            await asyncio.sleep(0.01)
        assert service.get(alpha_one.id).status == State.SUCCEEDED
        assert service.get(alpha_two.id).status == State.SUCCEEDED
        await runner.stop()

    asyncio.run(exercise())


def test_runner_failure_interrupts_already_claimed_developer_work(settings, database, monkeypatch):
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


def test_cancel_before_first_operation_step_is_finalized(settings, database):
    runtime = CapacityRuntime(settings)
    service = TaskService(database, settings, developer_runtime=runtime)
    first = _approved(service, "alpha-one")
    second = _approved(service, "alpha-two")
    _set_claim_order(database, first, second)

    async def exercise():
        runner = Runner(service, developer_runtime=runtime)
        execution = service.claim()
        assert execution is not None
        operation = asyncio.create_task(runner._run(execution))
        operation.add_done_callback(
            lambda task: runner._finalize_unstarted_operation(execution, task)
        )
        runner.operations[execution.task_id] = operation
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        current = service.get(first.id)
        assert current.status == State.CANCELLED
        assert current.attempts[0].outcome == State.CANCELLED
        assert runner.operations == {}
        next_execution = service.claim()
        assert next_execution is not None and next_execution.task_id == second.id

    asyncio.run(exercise())


def test_slot_exhaustion_defers_without_consuming_approval(settings, database, monkeypatch):
    now = utcnow()
    monkeypatch.setattr("app.tasks.utcnow", lambda: now)
    runtime = CapacityRuntime(settings, error="developer_worker_capacity_exhausted")
    service = TaskService(database, settings, developer_runtime=runtime)
    task = _approved(service, "alpha-one")
    execution = service.claim()
    assert execution is not None

    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))

    current = service.get(task.id)
    assert current.status == State.PENDING
    assert current.error is None
    assert current.approvals[0].status == "approved"
    assert current.attempts[0].outcome == State.INTERRUPTED
    assert service.claim() is None
    with database.read() as session:
        eligible_at = session.get(Task, task.id).capacity_backoff_until
    assert eligible_at == now + timedelta(seconds=CAPACITY_BACKOFF_INITIAL_SECONDS)
    now = eligible_at - timedelta(microseconds=1)
    assert service.claim() is None
    now = eligible_at
    retry = service.claim()
    assert retry is not None and retry.task_id == task.id


def test_capacity_deferral_honors_pending_cancellation(settings, database):
    runtime = CapacityRuntime(settings, error="developer_worker_capacity_exhausted")
    service = TaskService(database, settings, developer_runtime=runtime)
    task = _approved(service, "alpha-one")
    execution = service.claim()
    assert execution is not None
    assert service.cancel(task.id).status == State.RUNNING

    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))

    current = service.get(task.id)
    assert current.status == State.CANCELLED
    assert current.error == "cancelled_by_operator"
    assert current.attempts[0].outcome == State.CANCELLED
    assert service.claim() is None


def test_real_runtime_slot_contention_defers_with_bounded_backoff_and_retries(
    developer_case, database
):
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Linux Bubblewrap prerequisite not installed")
    settings = developer_case.settings.model_copy(update={"runner_poll_seconds": 0.05})
    service = TaskService(database, settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    worker_id = developer_case.request(
        "test", "zelo_core", 30
    ).worker_selection.descriptor.worker_id
    lock = developer_case.worker_lock(worker_id, 1)

    async def exercise():
        runner = Runner(service, developer_runtime=developer_case)
        runner.start()
        try:
            # Backoff starts at 0.25s. A slow CI runner can observe multiple
            # bounded capacity deferrals while the lock remains held. Do not
            # assert exactly one attempt during a 0.5s polling window.
            deadline = asyncio.get_running_loop().time() + 12
            while asyncio.get_running_loop().time() < deadline:
                current = service.get(task.id)
                if (
                    current.status == State.PENDING
                    and len(current.attempts) >= 2
                    and all(attempt.outcome == State.INTERRUPTED for attempt in current.attempts)
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("runner did not defer two attempts while capacity was exhausted")

            current = service.get(task.id)
            assert current.status == State.PENDING
            assert current.error is None
            assert current.approvals[0].status == "approved"
            assert len(current.attempts) >= 2
            assert all(
                attempt.outcome == State.INTERRUPTED
                and attempt.error == "developer_worker_capacity_exhausted"
                for attempt in current.attempts
            )
            interrupted_count = len(current.attempts)
            deferred = [
                event
                for event in service.events(task.id, 100, 0)
                if event.event == "execution_deferred"
            ]
            assert len(deferred) >= 2
            assert deferred[0].details["retry_after_seconds"] == CAPACITY_BACKOFF_INITIAL_SECONDS
            assert deferred[1].details["retry_after_seconds"] == min(
                CAPACITY_BACKOFF_INITIAL_SECONDS * 2, CAPACITY_BACKOFF_MAX_SECONDS
            )
            for event in deferred:
                consecutive = event.details["consecutive_deferrals"]
                assert event.details["retry_after_seconds"] == capacity_backoff_seconds(consecutive)
                assert 1 <= consecutive
                assert (
                    CAPACITY_BACKOFF_INITIAL_SECONDS
                    <= event.details["retry_after_seconds"]
                    <= CAPACITY_BACKOFF_MAX_SECONDS
                )

            lock.close()
            deadline = asyncio.get_running_loop().time() + 15
            while asyncio.get_running_loop().time() < deadline:
                current = service.get(task.id)
                if current.status == State.SUCCEEDED and len(current.attempts) > interrupted_count:
                    break
                await asyncio.sleep(0.05)
            current = service.get(task.id)
            assert current.status == State.SUCCEEDED
            assert all(attempt.outcome == State.INTERRUPTED for attempt in current.attempts[:-1])
            assert current.attempts[-1].outcome == State.SUCCEEDED
            # A single in-flight claim may be re-deferred as the lock is
            # released, but the next bounded retry must succeed.
            assert len(current.attempts) <= interrupted_count + 2
        finally:
            if not lock.closed:
                lock.close()
            await runner.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("preset", [1025, 1_000_000])
def test_capacity_backoff_clamps_large_persisted_counters(settings, database, preset):
    runtime = CapacityRuntime(settings, error="developer_worker_capacity_exhausted")
    service = TaskService(database, settings, developer_runtime=runtime)
    task = _approved(service, "alpha-one")
    execution = service.claim()
    assert execution is not None
    with database.transaction() as session:
        session.get(Task, task.id).capacity_backoff_attempts = preset

    service.defer(execution, error="developer_worker_capacity_exhausted")

    current = service.get(task.id)
    assert current.status == State.PENDING
    assert current.error is None
    assert current.approvals[0].status == "approved"
    assert current.attempts[0].outcome == State.INTERRUPTED
    with database.read() as session:
        stored = session.get(Task, task.id)
        assert stored.capacity_backoff_attempts == preset + 1
        assert stored.capacity_backoff_until is not None
        delay = (stored.capacity_backoff_until - utcnow()).total_seconds()
        assert 0 < delay <= CAPACITY_BACKOFF_MAX_SECONDS + 0.5


def test_runner_capacity_deferral_clamps_large_backoff_counter(settings, database):
    runtime = CapacityRuntime(settings, error="developer_worker_capacity_exhausted")
    service = TaskService(database, settings, developer_runtime=runtime)
    task = _approved(service, "alpha-one")
    execution = service.claim()
    assert execution is not None
    with database.transaction() as session:
        session.get(Task, task.id).capacity_backoff_attempts = 1_000_000

    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))

    current = service.get(task.id)
    assert current.status == State.PENDING
    assert current.error is None
    assert current.approvals[0].status == "approved"
    assert current.attempts[0].outcome == State.INTERRUPTED


def test_later_runnable_work_can_overtake_deferred_developer_task(settings, database, monkeypatch):
    # Exercise eligibility boundaries without depending on host speed or sleep timing.
    now = utcnow()
    monkeypatch.setattr("app.tasks.utcnow", lambda: now)
    runtime = CapacityRuntime(settings, error="developer_worker_capacity_exhausted")
    service = TaskService(database, settings, developer_runtime=runtime)
    developer = _approved(service, "alpha-one")
    deferred = service.claim()
    assert deferred is not None
    asyncio.run(Runner(service, developer_runtime=runtime)._run(deferred))
    echo, _ = service.submit(EchoInput(kind="echo", payload={"text": "later"}), None)

    claim = service.claim()
    assert claim is not None and claim.task_id == echo.id and claim.kind == "echo"
    service.finish(claim, State.SUCCEEDED, result={"text": "later"})
    # The deferred developer remains ineligible until its exact stored deadline.
    assert service.claim() is None
    with database.read() as session:
        eligible_at = session.get(Task, developer.id).capacity_backoff_until
    assert eligible_at == now + timedelta(seconds=CAPACITY_BACKOFF_INITIAL_SECONDS)
    now = eligible_at - timedelta(microseconds=1)
    assert service.claim() is None
    now = eligible_at
    next_claim = service.claim()
    assert next_claim is not None and next_claim.task_id == developer.id


def test_runtime_worker_slots_enforce_trusted_ceiling(developer_case):
    request = developer_case.request("test", "zelo_core", 30)
    worker_id = request.worker_selection.descriptor.worker_id
    first = developer_case.worker_lock(worker_id, 1)
    try:
        with pytest.raises(DeveloperError, match="developer_worker_capacity_exhausted"):
            developer_case.worker_lock(worker_id, 1)
    finally:
        first.close()

    first = developer_case.worker_lock(worker_id, 2)
    second = developer_case.worker_lock(worker_id, 2)
    try:
        with pytest.raises(DeveloperError, match="developer_worker_capacity_exhausted"):
            developer_case.worker_lock(worker_id, 2)
    finally:
        second.close()
        first.close()


def test_persisted_descriptor_cannot_override_registry_ceiling(developer_case):
    trusted = developer_case.request("test", "zelo_core", 30)
    descriptor = trusted.worker_selection.descriptor.model_copy(update={"max_concurrency": 2})
    selection = trusted.worker_selection.model_copy(update={"descriptor": descriptor})
    stale = trusted.model_copy(update={"worker_selection": selection})
    developer_case.compatible = lambda request, timeout: True
    developer_case.supervisor.matches = lambda snapshot: True
    worker_id = trusted.worker_selection.descriptor.worker_id
    lock = developer_case.worker_lock(worker_id, 1)
    try:
        with pytest.raises(DeveloperError, match="developer_worker_capacity_exhausted"):
            asyncio.run(developer_case.run(str(uuid4()), str(uuid4()), stale, lambda *_: None))
    finally:
        lock.close()


def test_cross_process_worker_slots_enforce_same_trusted_ceiling(developer_case):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("fork multiprocessing is unavailable")
    ctx = get_context("fork")
    worker_id = "cross-process-worker"
    started_one = ctx.Event()
    started_two = ctx.Event()
    release = ctx.Event()
    results = ctx.Queue()
    args = (
        str(developer_case.settings.workspace_root),
        str(developer_case.source_repository),
        worker_id,
        2,
        release,
        results,
    )
    first = ctx.Process(
        target=_hold_worker_slot,
        args=(args[0], args[1], args[2], args[3], started_one, args[4], args[5]),
    )
    second = ctx.Process(
        target=_hold_worker_slot,
        args=(args[0], args[1], args[2], args[3], started_two, args[4], args[5]),
    )
    first.start()
    second.start()
    try:
        assert started_one.wait(5)
        assert results.get(timeout=5) == "locked"
        assert started_two.wait(5)
        assert results.get(timeout=5) == "locked"
        with pytest.raises(DeveloperError, match="developer_worker_capacity_exhausted"):
            developer_case.worker_lock(worker_id, 2)
    finally:
        release.set()
        first.join(5)
        second.join(5)
    assert first.exitcode == 0
    assert second.exitcode == 0


@pytest.mark.parametrize("method,args", [("worker_lock", ("worker", 1)), ("maintenance_lock", ())])
def test_lock_helpers_close_fd_when_fdopen_fails(developer_case, monkeypatch, method, args):
    closed = []
    original_close = os.close

    def fail_fdopen(fd, mode):
        raise OSError("fdopen_failed")

    def record_close(fd):
        closed.append(fd)
        return original_close(fd)

    monkeypatch.setattr("app.developer.os.fdopen", fail_fdopen)
    monkeypatch.setattr("app.developer.os.close", record_close)
    with pytest.raises(OSError, match="fdopen_failed"):
        getattr(developer_case, method)(*args)
    assert len(closed) == 1
