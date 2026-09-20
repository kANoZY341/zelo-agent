from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.developer_contracts import ValidationResult, WorkerDescriptor, WorkerIdentity
from app.developer_process import DeveloperError
from app.policy import DomainError, State
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from app.worker_authority import AUTHORITY_POLICY_VERSION, authority_profile
from app.worker_registry import RegisteredWorker, Supervisor, WorkerRegistry


class StubWorker:
    def __init__(self, implementation: str = "stub") -> None:
        self._identity = WorkerIdentity(
            implementation=implementation,
            config_version="test",
            cli_version="test",
            capability_profile="test",
            executable="test-worker",
            executable_sha256="a" * 64,
            code_mode_host="test-host",
            code_mode_host_sha256="b" * 64,
            auth_location="none",
        )

    def identity(self):
        return self._identity

    def bind(self, approved):
        return nullcontext(self)

    async def preflight(self):
        return self._identity


def descriptor(worker_id: str, *, priority: int = 1, enabled: bool = True):
    return WorkerDescriptor(
        worker_id=worker_id,
        role="developer",
        capabilities=("zelo_core_development",),
        authority=("workspace-write",),
        implementation="stub",
        enabled=enabled,
        priority=priority,
        max_concurrency=1,
    )


def test_registry_rejects_duplicate_ids_and_lists_stably():
    with pytest.raises(ValueError, match="duplicate_worker_id"):
        WorkerRegistry(
            [
                (descriptor("zeta"), StubWorker()),
                (descriptor("zeta"), StubWorker()),
            ]
        )
    registry = WorkerRegistry(
        [(descriptor("zeta"), StubWorker()), (descriptor("alpha"), StubWorker())]
    )
    assert [item.worker_id for item in registry.list()] == ["alpha", "zeta"]


def test_supervisor_filters_unknown_disabled_and_mismatched_workers():
    registry = WorkerRegistry(
        [
            (descriptor("disabled", enabled=False), StubWorker()),
            (descriptor("developer", priority=5), StubWorker()),
        ]
    )
    supervisor = Supervisor(registry)
    with pytest.raises(DeveloperError, match="no_eligible_worker"):
        supervisor.select(role="unknown", capabilities=("zelo_core_development",))
    with pytest.raises(DeveloperError, match="no_eligible_worker"):
        supervisor.select(role="developer", capabilities=("unavailable",))
    assert supervisor.registry.get("missing") is None
    assert (
        supervisor.select(role="developer", capabilities=("zelo_core_development",))[
            1
        ].descriptor.worker_id
        == "developer"
    )


def test_supervisor_uses_priority_then_stable_worker_id():
    registry = WorkerRegistry(
        [
            (descriptor("zeta", priority=10), StubWorker()),
            (descriptor("alpha", priority=10), StubWorker()),
            (descriptor("high", priority=11), StubWorker()),
        ]
    )
    selected, snapshot = Supervisor(registry).select(
        role="developer", capabilities=("zelo_core_development",)
    )
    assert selected.descriptor.worker_id == "high"
    assert snapshot.descriptor == selected.descriptor
    assert snapshot.registry_version == registry.version
    assert snapshot.authority_profile == "workspace-write-no-network-v1"
    assert snapshot.authority_policy_version == AUTHORITY_POLICY_VERSION

    registry = WorkerRegistry(
        [
            (descriptor("zeta", priority=10), StubWorker()),
            (descriptor("alpha", priority=10), StubWorker()),
        ]
    )
    assert (
        Supervisor(registry)
        .select(role="developer", capabilities=("zelo_core_development",))[0]
        .descriptor.worker_id
        == "alpha"
    )


def test_descriptors_and_selection_snapshots_are_immutable():
    worker = StubWorker()
    registry = WorkerRegistry([(descriptor("only"), worker)])
    _, snapshot = Supervisor(registry).select(
        role="developer", capabilities=("zelo_core_development",)
    )
    with pytest.raises(ValidationError):
        snapshot.descriptor.priority = 2
    with pytest.raises(ValidationError):
        snapshot.registry_version = "changed"
    with pytest.raises(AttributeError):
        registry.version = "changed"
    with pytest.raises(AttributeError):
        registry.routing_policy = "changed"
    with pytest.raises(TypeError):
        other = descriptor("other")
        registry._workers["other"] = RegisteredWorker(
            other, worker, authority_profile(other.authority)
        )


def test_descriptor_set_fields_must_be_canonical():
    with pytest.raises(ValidationError, match="not_canonical"):
        WorkerDescriptor(
            worker_id="worker",
            role="developer",
            capabilities=("zelo_core_development", "zelo_core_development"),
            authority=("workspace-write",),
            implementation="stub",
            priority=1,
            max_concurrency=1,
        )


def test_registry_version_is_content_derived():
    worker = StubWorker()
    first = WorkerRegistry([(descriptor("worker", priority=1), worker)])
    second = WorkerRegistry([(descriptor("worker", priority=2), worker)])
    third = WorkerRegistry(
        [
            (descriptor("worker", priority=1), worker),
            (descriptor("disabled", enabled=False), StubWorker()),
        ]
    )

    assert first.version != second.version
    assert first.version != third.version
    _, snapshot = Supervisor(first).select(
        role="developer", capabilities=("zelo_core_development",)
    )
    assert not Supervisor(second).matches(snapshot)
    assert not Supervisor(third).matches(snapshot)


def test_registry_revalidates_descriptor_instances():
    malformed = descriptor("worker").model_copy(
        update={
            "capabilities": (
                "zelo_core_development",
                "zelo_core_development",
            )
        }
    )
    with pytest.raises(ValidationError, match="not_canonical"):
        WorkerRegistry([(malformed, StubWorker())])


def test_registry_rejects_unsupported_authority():
    unsupported = descriptor("worker").model_copy(update={"authority": ("network-write",)})
    with pytest.raises(ValueError, match="worker_authority_unsupported"):
        WorkerRegistry([(unsupported, StubWorker())])


def test_authority_policy_is_approval_bound():
    registry = WorkerRegistry([(descriptor("worker"), StubWorker())])
    _, snapshot = Supervisor(registry).select(
        role="developer", capabilities=("zelo_core_development",)
    )
    assert Supervisor(registry).matches(snapshot)
    assert not Supervisor(registry).matches(
        snapshot.model_copy(update={"authority_policy_version": "changed"})
    )
    assert not Supervisor(registry).matches(
        snapshot.model_copy(update={"authority_profile": "changed"})
    )


def test_developer_api_cannot_select_worker_or_authority():
    with pytest.raises(ValidationError):
        DeveloperRunInput(
            kind="developer_run",
            payload={
                "instruction": "test",
                "worker_id": "attacker",
                "authority": ["github"],
            },
        )


def test_developer_submission_audits_routing_and_registry_change_invalidates(
    developer_case, database
):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "test"}), None
    )
    events = service.events(task.id, 20, 0)
    names = [event.event for event in events]
    assert "supervisor_routing_requested" in names
    assert "supervisor_worker_selected" in names

    selected = developer_case.registry.list()[0]
    registered = developer_case.registry.get(selected.worker_id)
    assert registered is not None
    original_version = developer_case.registry.version
    changed = selected.model_copy(update={"priority": selected.priority + 1})
    developer_case.registry = WorkerRegistry([(changed, registered.worker)])
    assert developer_case.registry.version != original_version
    developer_case.supervisor = Supervisor(developer_case.registry)
    service.expire_approvals()
    invalidated = service.get(task.id)
    assert invalidated.status == State.CANCELLED
    assert invalidated.error == "approval_invalidated"


def test_explicit_registry_rejects_unregistered_primary_worker(developer_case):
    worker_a = StubWorker("worker_a")
    worker_b = StubWorker("worker_b")
    registry = WorkerRegistry(
        [
            (
                descriptor("worker_a").model_copy(update={"implementation": "worker_a"}),
                worker_a,
            )
        ]
    )

    with pytest.raises(DeveloperError, match="worker_registry_mismatch"):
        developer_case.__class__(
            developer_case.settings,
            worker_b,
            developer_case.source_repository,
            registry,
        )


def test_explicit_registry_derives_supervisor_winner(developer_case):
    worker_a = StubWorker("worker_a")
    worker_b = StubWorker("worker_b")
    registry = WorkerRegistry(
        [
            (
                descriptor("worker_a", priority=1).model_copy(
                    update={"implementation": "worker_a"}
                ),
                worker_a,
            ),
            (
                descriptor("worker_b", priority=2).model_copy(
                    update={"implementation": "worker_b"}
                ),
                worker_b,
            ),
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, None, developer_case.source_repository, registry
    )
    assert runtime.worker is worker_b


def test_explicit_registry_rejects_worker_reassignment(developer_case):
    worker = StubWorker("worker_a")
    registry = WorkerRegistry(
        [
            (
                descriptor("worker_a").model_copy(update={"implementation": "worker_a"}),
                worker,
            )
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, worker, developer_case.source_repository, registry
    )

    with pytest.raises(DeveloperError, match="worker_registry_explicit"):
        runtime.worker = StubWorker("worker_b")


def test_validation_identity_change_invalidates_at_operator_approval(
    developer_case, database, monkeypatch
):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "test"}), None
    )
    monkeypatch.setattr(developer_case, "validation_identity", lambda: "changed")

    with pytest.raises(DomainError, match="no longer pending|conflicts"):
        service.decide(task.approvals[0].id, "approve")

    invalidated = service.get(task.id)
    assert invalidated.status == State.CANCELLED
    assert invalidated.error == "approval_invalidated"


def test_transient_revalidation_failure_leaves_approval_pending(
    developer_case, database, monkeypatch
):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "test"}), None
    )

    def unavailable(*args, **kwargs):
        raise DeveloperError("approval_revalidation_unavailable")

    monkeypatch.setattr(developer_case, "approval_compatible", unavailable)
    with pytest.raises(DomainError) as failure:
        service.decide(task.approvals[0].id, "approve")

    assert failure.value.status_code == 503
    current = service.get(task.id)
    assert current.status == State.AWAITING_APPROVAL
    assert current.approvals[0].status == "pending"


def test_selected_worker_identity_and_execution_are_bound_to_registry(developer_case, monkeypatch):
    worker_a = StubWorker("worker_a")
    worker_b = StubWorker("worker_b")
    calls = []

    async def run_b(request, repo):
        calls.append("b")
        return await developer_case.worker.run(request, repo)

    async def run_a(request, repo):
        calls.append("a")
        return await developer_case.worker.run(request, repo)

    worker_a.run = run_a
    worker_b.run = run_b
    registry = WorkerRegistry(
        [
            (
                descriptor("worker_a", priority=1).model_copy(
                    update={"implementation": "worker_a"}
                ),
                worker_a,
            ),
            (
                descriptor("worker_b", priority=2).model_copy(
                    update={"implementation": "worker_b"}
                ),
                worker_b,
            ),
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, worker_b, developer_case.source_repository, registry
    )
    request = runtime.request("test", "zelo_core", 30)
    assert request.worker == worker_b.identity()
    assert request.worker_selection.descriptor.worker_id == "worker_b"

    monkeypatch.setattr(runtime, "primary_snapshot", lambda request: _constant("source"))
    monkeypatch.setattr(runtime, "prepare", _prepare)
    monkeypatch.setattr(runtime, "verify_checkout_path", lambda repo: None)
    monkeypatch.setattr("app.developer.tree_digest", lambda root: "tree")
    monkeypatch.setattr(runtime, "validate", _validations)
    monkeypatch.setattr(runtime, "review", _review)
    result = __import__("asyncio").run(
        runtime.run(
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            request,
            lambda *args: None,
        )
    )
    assert result["error"] is None
    assert calls == ["b"]


def test_selected_identity_change_invalidates_approval_binding(developer_case):
    worker = StubWorker("worker_b")
    registry = WorkerRegistry(
        [
            (
                descriptor("worker_b").model_copy(update={"implementation": "worker_b"}),
                worker,
            )
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, worker, developer_case.source_repository, registry
    )
    request = runtime.request("test", "zelo_core", 30)
    worker._identity = worker.identity().model_copy(update={"cli_version": "changed"})
    assert not runtime.approval_compatible(request, 30)


def test_descriptor_identity_mismatch_fails_closed(developer_case):
    worker = StubWorker("worker_b")
    registry = WorkerRegistry(
        [(descriptor("worker_b").model_copy(update={"implementation": "wrong"}), worker)]
    )
    runtime = developer_case.__class__(
        developer_case.settings, worker, developer_case.source_repository, registry
    )
    with pytest.raises(DeveloperError, match="worker_descriptor_mismatch"):
        runtime.request("test", "zelo_core", 30)


async def _constant(value):
    return value


async def _prepare(repo, request):
    repo.mkdir(parents=True, exist_ok=True)


async def _validations(repo, request):
    return [
        ValidationResult(
            identifier=str(index),
            exit_code=0,
            output="",
            duration_seconds=0,
            completed_at=datetime.now(UTC),
        )
        for index in range(4)
    ]


async def _review(repo, request):
    return {
        "head": request.base_sha,
        "head_changed": False,
        "changed_files": [],
        "ignored_generated_files": [],
        "git_status": "",
        "diff_stat": "",
        "diff": "",
    }
