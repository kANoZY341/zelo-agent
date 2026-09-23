"""Agent Factory contract and deterministic registry tests."""

import pytest
from pydantic import ValidationError

from app.agent_factory import (
    AgentDescriptor,
    AgentFactory,
    AgentRegistry,
)


def descriptor(
    agent_id: str = "coding",
    *,
    enabled: bool = True,
    version: int = 1,
    capabilities: tuple[str, ...] = ("code_planning", "repository_change"),
    allowed_task_kinds: tuple[str, ...] = ("agent_run", "developer_run"),
    allowed_tools: tuple[str, ...] = (
        "list_workspace_files",
        "read_workspace_file",
        "repo_status",
    ),
    delegation_role: str | None = "developer",
    delegation_capabilities: tuple[str, ...] = ("zelo_core_development",),
) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=agent_id,
        role=agent_id,
        capabilities=capabilities,
        allowed_task_kinds=allowed_task_kinds,
        allowed_tools=allowed_tools,
        memory_namespace=f"agent/{agent_id}",
        delegation_role=delegation_role,
        delegation_capabilities=delegation_capabilities,
        enabled=enabled,
        version=version,
    )


def test_agent_descriptor_is_frozen_and_forbids_extra_fields():
    item = descriptor()

    with pytest.raises(ValidationError):
        item.agent_id = "other"

    payload = item.model_dump(mode="json")
    payload["worker_id"] = "codex_core"
    with pytest.raises(ValidationError):
        AgentDescriptor.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capabilities", ("repository_change", "code_planning")),
        ("capabilities", ("code_planning", "code_planning")),
        ("allowed_task_kinds", ("developer_run", "agent_run")),
        ("allowed_tools", ("repo_status", "repo_status")),
        ("delegation_capabilities", ("zelo_core_development", "python_development")),
    ],
)
def test_agent_descriptor_requires_canonical_collections(field, value):
    payload = descriptor().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match=f"{field}_not_canonical"):
        AgentDescriptor.model_validate(payload)


def test_agent_descriptor_binds_memory_namespace_to_agent_id():
    payload = descriptor().model_dump(mode="json")
    payload["memory_namespace"] = "agent/business"

    with pytest.raises(ValidationError, match="memory_namespace_mismatch"):
        AgentDescriptor.model_validate(payload)


@pytest.mark.parametrize(
    ("delegation_role", "delegation_capabilities", "error"),
    [
        (None, ("zelo_core_development",), "delegation_capabilities_without_role"),
        ("developer", (), "delegation_role_without_capabilities"),
    ],
)
def test_agent_descriptor_requires_complete_delegation_contract(
    delegation_role, delegation_capabilities, error
):
    payload = descriptor().model_dump(mode="json")
    payload["delegation_role"] = delegation_role
    payload["delegation_capabilities"] = delegation_capabilities

    with pytest.raises(ValidationError, match=error):
        AgentDescriptor.model_validate(payload)


def test_agent_without_worker_delegation_is_valid():
    item = descriptor(
        agent_id="research",
        delegation_role=None,
        delegation_capabilities=(),
        capabilities=("research",),
        allowed_task_kinds=("agent_run",),
        allowed_tools=("read_workspace_file",),
    )

    assert item.delegation_role is None
    assert item.delegation_capabilities == ()


def test_registry_rejects_duplicate_agent_id():
    with pytest.raises(ValueError, match="duplicate_agent_id"):
        AgentRegistry((descriptor(), descriptor()))


def test_registry_listing_and_version_are_insertion_order_independent():
    coding = descriptor()
    research = descriptor(
        agent_id="research",
        capabilities=("research",),
        allowed_task_kinds=("agent_run",),
        allowed_tools=("read_workspace_file",),
        delegation_role=None,
        delegation_capabilities=(),
    )

    left = AgentRegistry((research, coding))
    right = AgentRegistry((coding, research))

    assert [item.agent_id for item in left.list()] == ["coding", "research"]
    assert left.version == right.version


@pytest.mark.parametrize(
    "changed",
    [
        descriptor(version=2),
        descriptor(capabilities=("code_planning", "documentation")),
        descriptor(allowed_task_kinds=("developer_run",)),
        descriptor(allowed_tools=("repo_status",)),
        descriptor(delegation_capabilities=("python_development",)),
        descriptor(enabled=False),
    ],
)
def test_registry_version_changes_for_material_descriptor_changes(changed):
    baseline = AgentRegistry((descriptor(),))
    modified = AgentRegistry((changed,))

    assert modified.version != baseline.version


def test_factory_returns_exact_binding_and_detects_registry_change():
    registry = AgentRegistry((descriptor(),))
    factory = AgentFactory(registry)
    binding = factory.bind("coding")

    assert binding.descriptor == descriptor()
    assert binding.registry_version == registry.version
    assert factory.matches(binding)

    changed_factory = AgentFactory(AgentRegistry((descriptor(version=2),)))
    assert not changed_factory.matches(binding)


def test_factory_fails_closed_for_unknown_or_disabled_agent():
    factory = AgentFactory(AgentRegistry((descriptor(enabled=False),)))

    with pytest.raises(ValueError, match="agent_disabled"):
        factory.bind("coding")

    with pytest.raises(ValueError, match="agent_not_registered"):
        factory.bind("missing")


def test_agent_contract_does_not_name_worker_vendor_or_model():
    payload = descriptor().model_dump(mode="json")

    assert "worker_id" not in payload
    assert "implementation" not in payload
    assert "model" not in payload
    assert "provider" not in payload
    assert payload["delegation_role"] == "developer"
    assert payload["delegation_capabilities"] == ["zelo_core_development"]
