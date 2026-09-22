from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.developer import DeveloperRuntime
from app.developer_contracts import ValidationResult, WorkerDescriptor, WorkerIdentity
from app.developer_process import DeveloperError
from app.developer_sandbox import CODEX, sandbox
from app.policy import DomainError, State
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from app.worker_authority import (
    AUTHORITY_POLICY_VERSION,
    WorkerAuthorityProfile,
    authority_policy_fingerprint,
    authority_profile,
    codex_permission_semantics,
)
from app.worker_registry import (
    RegisteredWorker,
    Supervisor,
    WorkerRegistry,
    descriptor_for_reviewer,
)


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


def descriptor(
    worker_id: str,
    *,
    priority: int = 1,
    enabled: bool = True,
    capabilities: tuple[str, ...] = ("zelo_core_development",),
    max_concurrency: int = 1,
):
    return WorkerDescriptor(
        worker_id=worker_id,
        role="developer",
        capabilities=capabilities,
        authority=("workspace-write",),
        implementation="stub",
        enabled=enabled,
        priority=priority,
        max_concurrency=max_concurrency,
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


def test_supervisor_binds_requested_capabilities_and_skips_full_worker():
    high = StubWorker()
    low = StubWorker()
    registry = WorkerRegistry(
        [
            (descriptor("high", priority=20), high),
            (descriptor("low", priority=10), low),
        ]
    )
    supervisor = Supervisor(registry)

    selected, snapshot = supervisor.select(
        role="developer",
        capabilities=("zelo_core_development",),
        running_counts={"high": 1},
    )
    assert selected.worker is low
    assert snapshot.descriptor.worker_id == "low"
    assert snapshot.requested_role == "developer"
    assert snapshot.requested_capabilities == ("zelo_core_development",)
    assert snapshot.routing_policy == "available-capacity-priority-worker-id-v2"
    assert supervisor.matches(snapshot)


def test_supervisor_uses_deterministic_winner_when_all_eligible_workers_are_full():
    registry = WorkerRegistry(
        [
            (descriptor("high", priority=20), StubWorker()),
            (descriptor("low", priority=10), StubWorker()),
        ]
    )
    selected, _ = Supervisor(registry).select(
        role="developer",
        capabilities=("zelo_core_development",),
        running_counts={"high": 1, "low": 1},
    )
    assert selected.descriptor.worker_id == "high"


def test_supervisor_capability_filter_and_request_binding_are_enforced():
    registry = WorkerRegistry(
        [
            (descriptor("general", priority=20), StubWorker()),
            (
                descriptor(
                    "testing",
                    priority=10,
                    capabilities=("testing", "zelo_core_development"),
                ),
                StubWorker(),
            ),
        ]
    )
    supervisor = Supervisor(registry)
    selected, snapshot = supervisor.select(role="developer", capabilities=("testing",))
    assert selected.descriptor.worker_id == "testing"
    assert snapshot.requested_capabilities == ("testing",)
    tampered = snapshot.model_copy(update={"requested_capabilities": ("missing",)})
    assert not supervisor.matches(tampered)


def test_supervisor_rejects_invalid_capacity_snapshot():
    registry = WorkerRegistry([(descriptor("worker"), StubWorker())])
    with pytest.raises(DeveloperError, match="worker_capacity_invalid"):
        Supervisor(registry).select(
            role="developer",
            capabilities=("zelo_core_development",),
            running_counts={"worker": -1},
        )


def test_runtime_preapproval_selection_uses_live_worker_capacity(developer_case):
    high = StubWorker("high")
    low = StubWorker("low")
    registry = WorkerRegistry(
        [
            (
                descriptor("high", priority=20).model_copy(update={"implementation": "high"}),
                high,
            ),
            (
                descriptor("low", priority=10).model_copy(update={"implementation": "low"}),
                low,
            ),
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, None, developer_case.source_repository, registry
    )
    request = runtime.request(
        "test",
        "zelo_core",
        30,
        running_counts={"high": 1},
    )
    assert request.worker_selection.descriptor.worker_id == "low"
    assert request.worker == low.identity()


def test_read_only_reviewer_authority_is_fail_closed_and_fingerprinted():
    read = authority_profile(("workspace-read",))
    write = authority_profile(("workspace-write",))
    assert read.name == "workspace-read-no-network-v1"
    assert read.workspace_write is False
    assert read.network is False
    assert authority_policy_fingerprint((read,)) != authority_policy_fingerprint((write,))

    from app import developer_sandbox

    plans = developer_sandbox.worker_sandbox_plans(read.workspace_write)
    execution = plans["worker_execution"]
    assert execution["workspace"]["writable"] is False
    assert codex_permission_semantics(read)["network_enabled"] is False
    repo_mount = next(
        operation
        for operation in execution["operations"]
        if operation.get("dest") == developer_sandbox.REPO
    )
    assert repo_mount["op"] == "--ro-bind"


def test_reviewer_descriptor_uses_read_only_authority():
    worker = StubWorker("reviewer_impl")
    descriptor_value = descriptor_for_reviewer(
        worker, implementation="reviewer_impl", worker_id="reviewer"
    )
    registry = WorkerRegistry([(descriptor_value, worker)])
    selected, snapshot = Supervisor(registry).select(role="reviewer", capabilities=("code_review",))
    assert selected.descriptor.role == "reviewer"
    assert selected.authority_profile.name == "workspace-read-no-network-v1"
    assert snapshot.requested_role == "reviewer"
    assert snapshot.requested_capabilities == ("code_review",)


def test_reviewer_selection_excludes_producer_and_binds_exclusion():
    producer = StubWorker("shared")
    independent = StubWorker("independent")
    developer_descriptor = descriptor("producer").model_copy(update={"implementation": "shared"})
    aliased_reviewer = descriptor_for_reviewer(
        producer, implementation="shared", worker_id="producer_reviewer_alias"
    ).model_copy(update={"priority": 20})
    independent_reviewer = descriptor_for_reviewer(
        independent, implementation="independent", worker_id="independent_reviewer"
    ).model_copy(update={"priority": 10})
    registry = WorkerRegistry(
        [
            (developer_descriptor, producer),
            (aliased_reviewer, producer),
            (independent_reviewer, independent),
        ]
    )
    # The Supervisor applies explicit exclusions before deterministic selection.
    selected, snapshot = Supervisor(registry).select(
        role="reviewer",
        capabilities=("code_review",),
        exclude_worker_ids=("producer_reviewer_alias",),
    )
    assert selected.worker is independent
    assert snapshot.excluded_worker_ids == ("producer_reviewer_alias",)
    assert Supervisor(registry).matches(snapshot)
    assert snapshot.descriptor.worker_id not in snapshot.excluded_worker_ids


def test_runtime_reviewer_selection_excludes_same_worker_instance(developer_case):
    producer = StubWorker("shared")
    independent = StubWorker("independent")
    registry = WorkerRegistry(
        [
            (
                descriptor("producer", priority=100).model_copy(
                    update={"implementation": "shared"}
                ),
                producer,
            ),
            (
                descriptor_for_reviewer(
                    producer,
                    implementation="shared",
                    worker_id="producer_reviewer_alias",
                ).model_copy(update={"priority": 100}),
                producer,
            ),
            (
                descriptor_for_reviewer(
                    independent,
                    implementation="independent",
                    worker_id="independent_reviewer",
                ).model_copy(update={"priority": 10}),
                independent,
            ),
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, producer, developer_case.source_repository, registry
    )
    selected, snapshot = runtime.select_reviewer("producer")
    assert selected.worker is independent
    assert selected.descriptor.worker_id == "independent_reviewer"
    assert snapshot.authority_profile == "workspace-read-no-network-v1"
    assert "producer" in snapshot.excluded_worker_ids
    assert "producer_reviewer_alias" in snapshot.excluded_worker_ids


def test_runtime_reviewer_selection_fails_without_independent_reviewer(developer_case):
    producer = StubWorker("shared")
    registry = WorkerRegistry(
        [
            (
                descriptor("producer").model_copy(update={"implementation": "shared"}),
                producer,
            ),
            (
                descriptor_for_reviewer(
                    producer,
                    implementation="shared",
                    worker_id="producer_reviewer_alias",
                ),
                producer,
            ),
        ]
    )
    runtime = developer_case.__class__(
        developer_case.settings, producer, developer_case.source_repository, registry
    )
    with pytest.raises(DeveloperError, match="no_eligible_worker"):
        runtime.select_reviewer("producer")


def test_default_registry_registers_optional_claude_and_independent_reviewers(
    developer_case,
):
    settings = developer_case.settings.model_copy(update={"developer_claude_enabled": True})
    runtime = DeveloperRuntime(
        settings,
        source_repository=developer_case.source_repository,
    )
    descriptors = {item.worker_id: item for item in runtime.registry.list()}
    assert set(descriptors) == {
        "codex_core",
        "codex_reviewer",
        "claude_core",
        "claude_reviewer",
    }
    assert descriptors["codex_core"].role == "developer"
    assert descriptors["claude_core"].role == "developer"
    assert descriptors["codex_reviewer"].role == "reviewer"
    assert descriptors["claude_reviewer"].role == "reviewer"
    assert descriptors["codex_core"].priority > descriptors["claude_core"].priority

    selected, _ = runtime.supervisor.select(
        role="developer",
        capabilities=("zelo_core_development",),
        running_counts={"codex_core": 1},
    )
    assert selected.descriptor.worker_id == "claude_core"

    reviewer, snapshot = runtime.select_reviewer("codex_core")
    assert reviewer.descriptor.worker_id == "claude_reviewer"
    assert set(snapshot.excluded_worker_ids) == {"codex_core", "codex_reviewer"}

    reverse, reverse_snapshot = runtime.select_reviewer("claude_core")
    assert reverse.descriptor.worker_id == "codex_reviewer"
    assert set(reverse_snapshot.excluded_worker_ids) == {
        "claude_core",
        "claude_reviewer",
    }


def test_default_registry_registers_opencode_as_third_worker(developer_case):
    settings = developer_case.settings.model_copy(
        update={
            "developer_claude_enabled": True,
            "developer_opencode_enabled": True,
        }
    )
    runtime = DeveloperRuntime(
        settings,
        source_repository=developer_case.source_repository,
    )
    descriptors = {item.worker_id: item for item in runtime.registry.list()}
    assert set(descriptors) == {
        "codex_core",
        "codex_reviewer",
        "claude_core",
        "claude_reviewer",
        "opencode_core",
        "opencode_reviewer",
    }
    assert descriptors["codex_core"].priority > descriptors["claude_core"].priority
    assert descriptors["claude_core"].priority > descriptors["opencode_core"].priority
    assert descriptors["opencode_core"].role == "developer"
    assert descriptors["opencode_reviewer"].role == "reviewer"

    selected, _ = runtime.supervisor.select(
        role="developer",
        capabilities=("zelo_core_development",),
        running_counts={"codex_core": 1, "claude_core": 1},
    )
    assert selected.descriptor.worker_id == "opencode_core"

    reviewer, snapshot = runtime.select_reviewer("codex_core")
    assert reviewer.descriptor.worker_id == "claude_reviewer"
    assert "codex_reviewer" in snapshot.excluded_worker_ids


def test_default_registry_keeps_claude_disabled_until_configured(developer_case):
    runtime = DeveloperRuntime(
        developer_case.settings,
        source_repository=developer_case.source_repository,
    )
    assert {item.worker_id for item in runtime.registry.list()} == {
        "codex_core",
        "codex_reviewer",
    }


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


def test_authority_policy_fingerprint_binds_permission_semantics():
    denied = WorkerAuthorityProfile(
        name="profile",
        authority=("workspace-write",),
        workspace_write=True,
        network=False,
    )
    allowed = WorkerAuthorityProfile(
        name="profile",
        authority=("workspace-write",),
        workspace_write=True,
        network=True,
    )
    assert authority_policy_fingerprint((denied,)) != authority_policy_fingerprint((allowed,))


def test_authority_policy_matches_realized_permission_semantics(monkeypatch):
    registry = WorkerRegistry([(descriptor("worker"), StubWorker())])
    _, snapshot = Supervisor(registry).select(
        role="developer", capabilities=("zelo_core_development",)
    )
    assert Supervisor(registry).matches(snapshot)
    from app import worker_authority

    original = worker_authority.codex_permission_semantics

    def widened(profile):
        semantics = dict(original(profile))
        filesystem = dict(semantics["filesystem"])
        filesystem["/tmp/zelo-codex-engine"] = "read"
        semantics["filesystem"] = filesystem
        return semantics

    monkeypatch.setattr(worker_authority, "codex_permission_semantics", widened)
    assert not Supervisor(registry).matches(snapshot)


def test_authority_policy_matches_real_sandbox_semantics(monkeypatch, tmp_path):
    from app import developer_sandbox

    profile = authority_profile(("workspace-write",))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    auth = tmp_path / "auth.json"
    auth.write_text("fixture-auth")
    baseline_argv = sandbox(
        repo,
        [CODEX],
        writable=profile.workspace_write,
        codex_fd=11,
        code_mode_host_fd=12,
        auth=auth,
        service_network=True,
    )
    baseline = authority_policy_fingerprint((profile,))
    extra_bind = tmp_path / "extra-bind"
    extra_bind.mkdir()
    monkeypatch.setattr(
        developer_sandbox,
        "SYSTEM_READONLY_BINDS",
        (*developer_sandbox.SYSTEM_READONLY_BINDS, str(extra_bind)),
    )
    changed_argv = sandbox(
        repo,
        [CODEX],
        writable=profile.workspace_write,
        codex_fd=11,
        code_mode_host_fd=12,
        auth=auth,
        service_network=True,
    )
    assert str(extra_bind) not in baseline_argv
    assert str(extra_bind) in changed_argv
    assert authority_policy_fingerprint((profile,)) != baseline


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
