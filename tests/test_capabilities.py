"""v0.15 trusted capability registry foundation tests."""

import pytest
from pydantic import ValidationError

from app.agent_activation import AgentActivationEvaluator
from app.agent_factory import AgentComputerSpec, AgentRegistry, AgentSpec, default_agent_registry
from app.capabilities import (
    CapabilityRegistry,
    CapabilitySpec,
    default_capability_registry,
)
from app.developer_contracts import WorkerDescriptor
from app.worker_registry import (
    descriptor_for_computer_navigate_worker,
    descriptor_for_computer_worker,
)
from tests.conftest import AUTH


def capability(
    capability_id: str,
    *,
    subjects: tuple[str, ...] = ("agent",),
    lifecycle: str = "active",
    description: str | None = None,
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        description=description or f"Trusted {capability_id} capability.",
        subjects=subjects,
        lifecycle=lifecycle,
    )


def worker(
    *capabilities: str,
    role: str = "developer",
    worker_id: str | None = None,
) -> WorkerDescriptor:
    return WorkerDescriptor(
        worker_id=worker_id or f"{role}_worker",
        role=role,
        capabilities=tuple(sorted(capabilities or ("zelo_core_development",))),
        authority=("workspace-read",),
        implementation="test",
        enabled=True,
        priority=100,
        max_concurrency=1,
    )


def evaluator(
    registry: AgentRegistry,
    *,
    capability_registry: CapabilityRegistry,
    workers: tuple[WorkerDescriptor, ...] = (),
) -> AgentActivationEvaluator:
    return AgentActivationEvaluator(
        registry,
        available_task_kinds=("api_run", "developer_run", "python_run", "research_run"),
        available_tools=(
            "list_workspace_files",
            "read_workspace_file",
            "repo_status",
            "system_info",
        ),
        worker_descriptors=workers,
        capability_registry=capability_registry,
    )


def test_registry_version_is_deterministic_and_order_independent():
    left = CapabilityRegistry((capability("research"), capability("code_change")))
    right = CapabilityRegistry((capability("code_change"), capability("research")))

    assert left.version == right.version
    assert [item.capability_id for item in left.list()] == ["code_change", "research"]
    assert left.snapshot() == right.snapshot()


def test_registry_version_changes_with_material_definition():
    baseline = CapabilityRegistry((capability("research"),))
    changed = CapabilityRegistry(
        (capability("research", description="Changed trusted definition."),)
    )

    assert baseline.version != changed.version


def test_duplicate_capability_ids_fail_closed():
    with pytest.raises(ValueError, match="duplicate_capability_id"):
        CapabilityRegistry((capability("research"), capability("research")))


def test_capability_subjects_must_be_canonical():
    with pytest.raises(ValidationError, match="capability_subjects_not_canonical"):
        capability("code_review", subjects=("worker", "agent"))


def test_unknown_inactive_and_subject_mismatch_fail_closed():
    registry = CapabilityRegistry(
        (
            capability("agent_only"),
            capability("retired_worker", subjects=("worker",), lifecycle="retired"),
        )
    )

    with pytest.raises(ValueError, match="capability_not_registered:missing"):
        registry.require("missing", subject="agent")
    with pytest.raises(ValueError, match="capability_not_active:retired_worker"):
        registry.require("retired_worker", subject="worker")
    with pytest.raises(ValueError, match="capability_subject_mismatch:agent_only:worker"):
        registry.require("agent_only", subject="worker")


def test_default_registry_extends_research_for_v015_worker_routing():
    registry = default_capability_registry()

    assert [item.capability_id for item in registry.list()] == [
        "api_read",
        "browser_document",
        "code_change",
        "code_planning",
        "code_review",
        "computer_navigate",
        "computer_read",
        "evidence_synthesis",
        "python_execution",
        "research",
        "reverse_engineering",
        "shell_execution",
        "zelo_core_development",
    ]
    assert registry.get("code_review").subjects == ("agent", "worker")
    assert registry.get("research").subjects == ("agent", "worker")
    assert registry.get("research").version == 2
    assert registry.get("browser_document").subjects == ("agent", "worker")
    assert registry.get("browser_document").lifecycle == "active"
    assert registry.get("zelo_core_development").subjects == ("worker",)


def test_worker_descriptors_are_validated_against_subjects():
    agent_only = CapabilityRegistry((capability("agent_only"),))
    unknown = CapabilityRegistry((capability("zelo_core_development", subjects=("worker",)),))

    with pytest.raises(ValueError, match="capability_subject_mismatch:agent_only:worker"):
        agent_only.validate_workers((worker("agent_only"),))
    with pytest.raises(ValueError, match="capability_not_registered:missing"):
        unknown.validate_workers((worker("missing"),))


def test_default_agent_activation_binds_capability_registry_version():
    capabilities = default_capability_registry()
    report = evaluator(
        default_agent_registry(),
        capability_registry=capabilities,
        workers=(
            worker(),
            worker("python_execution", role="python"),
            worker("research", role="research"),
        ),
    ).require()

    assert report.policy_version == "zelo-agent-activation-policy-v4"
    assert report.capability_registry_version == capabilities.version
    assert all(
        item.capability_registry_version == capabilities.version for item in report.evaluations
    )
    for evaluation in report.evaluations:
        checks = {item.code: item.status for item in evaluation.checks}
        assert checks["agent_capability_registry"] == "pass"


def test_activation_rejects_unregistered_agent_capability():
    spec = AgentSpec(
        agent_id="custom",
        role="custom",
        objective="Exercise capability fail-closed behavior.",
        capabilities=("missing",),
        allowed_task_kinds=("research_run",),
        memory_namespace="agent/custom",
        lifecycle="active",
    )
    registry = AgentRegistry((spec,))

    with pytest.raises(
        ValueError,
        match="agent_activation_rejected:custom:agent_capability_registry",
    ):
        evaluator(
            registry,
            capability_registry=default_capability_registry(),
        ).require()


def test_capability_registry_identity_changes_activation_surface():
    agents = default_agent_registry()
    baseline = default_capability_registry()
    changed_specs = list(baseline.list())
    changed_specs[0] = changed_specs[0].model_copy(update={"version": 2})
    changed = CapabilityRegistry(changed_specs)

    workers = (
        worker(),
        worker("python_execution", role="python"),
        worker("research", role="research"),
    )
    first = evaluator(agents, capability_registry=baseline, workers=workers).require()
    second = evaluator(agents, capability_registry=changed, workers=workers).require()

    assert first.capability_registry_version != second.capability_registry_version
    assert first.surface_digest != second.surface_digest
    assert first.report_digest != second.report_digest


def test_application_exposes_operator_only_capability_snapshot(client):
    response = client.get("/capabilities", headers=AUTH)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["registry_schema"] == "zelo-capability-registry-v1"
    assert payload["registry_version"] == client.app.state.capability_registry.version
    assert [item["capability_id"] for item in payload["capabilities"]] == [
        "api_read",
        "browser_document",
        "code_change",
        "code_planning",
        "code_review",
        "computer_navigate",
        "computer_read",
        "evidence_synthesis",
        "python_execution",
        "research",
        "reverse_engineering",
        "shell_execution",
        "zelo_core_development",
    ]

    rejected = client.get(
        "/capabilities",
        headers={"Authorization": "Bearer invalid-operator-token"},
    )
    assert rejected.status_code == 401


def test_application_activation_evidence_uses_live_capability_registry(client):
    report = client.get("/agent-factory/activation", headers=AUTH)
    assert report.status_code == 200, report.text

    payload = report.json()
    assert payload["capability_registry_version"] == client.app.state.capability_registry.version


def test_computer_agent_activation_requires_profile_capability_and_worker():
    spec = AgentSpec(
        agent_id="computer_specialist",
        role="computer_specialist",
        objective="Observe one persistent computer.",
        capabilities=("computer_read",),
        allowed_task_kinds=("computer_run",),
        memory_namespace="agent/computer_specialist",
        computer=AgentComputerSpec(browser=True),
        lifecycle="active",
    )
    registry = AgentRegistry((spec,))
    capabilities = default_capability_registry()

    missing_worker = AgentActivationEvaluator(
        registry,
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=(),
        capability_registry=capabilities,
    ).evaluate_spec(spec)
    assert missing_worker.status == "rejected"
    assert "computer_worker_binding" in missing_worker.failure_codes

    accepted = AgentActivationEvaluator(
        registry,
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=(descriptor_for_computer_worker(),),
        capability_registry=capabilities,
    ).require_spec(spec)
    assert accepted.status == "accepted"
    checks = {item.code: item.status for item in accepted.checks}
    assert checks["computer_profile"] == "pass"
    assert checks["computer_capability"] == "pass"
    assert checks["computer_worker_binding"] == "pass"


def test_computer_agent_activation_rejects_missing_profile_or_capability():
    worker = (descriptor_for_computer_worker(),)
    capabilities = default_capability_registry()

    no_profile = AgentSpec(
        agent_id="no_profile",
        role="no_profile",
        objective="Invalid computer Agent without a computer profile.",
        capabilities=("computer_read",),
        allowed_task_kinds=("computer_run",),
        memory_namespace="agent/no_profile",
        lifecycle="active",
    )
    evaluation = AgentActivationEvaluator(
        AgentRegistry((no_profile,)),
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=worker,
        capability_registry=capabilities,
    ).evaluate_spec(no_profile)
    assert "computer_profile" in evaluation.failure_codes

    no_capability = AgentSpec(
        agent_id="no_capability",
        role="no_capability",
        objective="Invalid computer Agent without the read capability.",
        capabilities=("research",),
        allowed_task_kinds=("computer_run",),
        memory_namespace="agent/no_capability",
        computer=AgentComputerSpec(browser=True),
        lifecycle="active",
    )
    evaluation = AgentActivationEvaluator(
        AgentRegistry((no_capability,)),
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=worker,
        capability_registry=capabilities,
    ).evaluate_spec(no_capability)
    assert "computer_capability" in evaluation.failure_codes


def test_computer_navigation_activation_requires_endpoint_grant_and_navigation_worker():
    spec = AgentSpec(
        agent_id="computer_navigator",
        role="computer_navigator",
        objective="Navigate one persistent browser only to trusted endpoints.",
        capabilities=("computer_navigate", "computer_read"),
        allowed_task_kinds=("computer_run",),
        memory_namespace="agent/computer_navigator",
        computer=AgentComputerSpec(
            browser=True,
            browser_endpoint_ids=("public_site",),
        ),
        lifecycle="active",
    )
    registry = AgentRegistry((spec,))
    capabilities = default_capability_registry()

    read_only = AgentActivationEvaluator(
        registry,
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=(descriptor_for_computer_worker(),),
        capability_registry=capabilities,
    ).evaluate_spec(spec)
    assert read_only.status == "rejected"
    assert "computer_navigation_worker_binding" in read_only.failure_codes

    accepted = AgentActivationEvaluator(
        registry,
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=(
            descriptor_for_computer_worker(),
            descriptor_for_computer_navigate_worker(),
        ),
        capability_registry=capabilities,
    ).require_spec(spec)
    assert accepted.status == "accepted"
    checks = {item.code: item.status for item in accepted.checks}
    assert checks["computer_navigation_profile"] == "pass"
    assert checks["computer_navigation_worker_binding"] == "pass"

    no_grant = spec.model_copy(
        update={
            "computer": AgentComputerSpec(browser=True),
        }
    )
    rejected = AgentActivationEvaluator(
        AgentRegistry((no_grant,)),
        available_task_kinds=("computer_run",),
        available_tools=(),
        worker_descriptors=(
            descriptor_for_computer_worker(),
            descriptor_for_computer_navigate_worker(),
        ),
        capability_registry=capabilities,
    ).evaluate_spec(no_grant)
    assert "computer_navigation_profile" in rejected.failure_codes
