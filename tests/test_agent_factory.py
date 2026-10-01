"""v0.14 Agent Factory registry and activation-boundary tests."""

import pytest
from pydantic import ValidationError

from app.agent_factory import (
    BUILTIN_AGENT_TASK_KINDS,
    AgentFactory,
    AgentRegistry,
    AgentSpec,
    DepartmentSpec,
    WorkerRequirement,
    coding_agent_spec,
    default_agent_registry,
    research_agent_spec,
)


def agent_spec(
    agent_id: str = "coding",
    *,
    version: int = 1,
    lifecycle: str = "active",
    allowed_task_kinds: tuple[str, ...] = ("developer_run",),
    allowed_tools: tuple[str, ...] = ("repo_status",),
    allow_subagents: bool = False,
    max_subagents: int = 0,
) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        version=version,
        role=agent_id,
        objective=f"Bounded {agent_id} work.",
        capabilities=(f"{agent_id}_work",),
        allowed_task_kinds=allowed_task_kinds,
        allowed_tools=allowed_tools,
        memory_namespace=f"agent/{agent_id}",
        lifecycle=lifecycle,
        allow_subagents=allow_subagents,
        max_subagents=max_subagents,
    )


def test_agent_spec_is_frozen_and_forbids_extra_fields():
    item = agent_spec()

    with pytest.raises(ValidationError):
        item.agent_id = "other"

    payload = item.model_dump(mode="json")
    payload["worker_id"] = "codex_core"
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capabilities", ("z_work", "a_work")),
        ("allowed_task_kinds", ("research_run", "developer_run")),
        ("allowed_tools", ("repo_status", "repo_status")),
        ("permissions", ("workspace_read", "delegate_developer")),
        ("procedures", ("review", "implementation")),
    ],
)
def test_agent_spec_requires_canonical_collections(field, value):
    payload = agent_spec().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match=f"{field}_not_canonical"):
        AgentSpec.model_validate(payload)


def test_agent_spec_binds_memory_namespace_to_identity():
    payload = agent_spec().model_dump(mode="json")
    payload["memory_namespace"] = "agent/research"

    with pytest.raises(ValidationError, match="memory_namespace_mismatch"):
        AgentSpec.model_validate(payload)


@pytest.mark.parametrize(
    ("allow_subagents", "max_subagents", "error"),
    [
        (True, 0, "subagent_limit_required"),
        (False, 1, "subagent_limit_without_authority"),
    ],
)
def test_agent_spec_subagent_authority_is_fail_closed(allow_subagents, max_subagents, error):
    with pytest.raises(ValidationError, match=error):
        agent_spec(
            allow_subagents=allow_subagents,
            max_subagents=max_subagents,
        )


def test_worker_requirement_is_worker_neutral_and_canonical():
    requirement = WorkerRequirement(
        role="developer",
        capabilities=("python_development", "zelo_core_development"),
    )
    assert requirement.role == "developer"

    with pytest.raises(ValidationError, match="worker_capabilities_not_canonical"):
        WorkerRequirement(
            role="developer",
            capabilities=("zelo_core_development", "python_development"),
        )

    payload = requirement.model_dump(mode="json")
    assert "worker_id" not in payload
    assert "implementation" not in payload
    assert "provider" not in payload


def test_department_assignments_are_canonical_and_reference_agents():
    coding = agent_spec()
    research = agent_spec(
        "research",
        allowed_task_kinds=("research_run",),
    )

    with pytest.raises(ValidationError, match="producer_agent_ids_not_canonical"):
        DepartmentSpec(
            department_id="product",
            objective="Coordinate work.",
            producer_agent_ids=("research", "coding"),
            lifecycle="active",
        )

    with pytest.raises(ValidationError, match="manager_cannot_be_producer"):
        DepartmentSpec(
            department_id="product",
            objective="Coordinate work.",
            manager_agent_id="coding",
            producer_agent_ids=("coding",),
            lifecycle="active",
        )

    with pytest.raises(ValueError, match="department_agent_not_registered"):
        AgentRegistry(
            (coding, research),
            (
                DepartmentSpec(
                    department_id="product",
                    objective="Coordinate work.",
                    producer_agent_ids=("missing",),
                ),
            ),
        )


def test_registry_rejects_duplicates_and_is_order_independent():
    coding = agent_spec()
    research = agent_spec(
        "research",
        allowed_task_kinds=("research_run",),
    )
    engineering = DepartmentSpec(
        department_id="engineering",
        objective="Coordinate engineering.",
        producer_agent_ids=("coding",),
    )

    with pytest.raises(ValueError, match="duplicate_agent_id"):
        AgentRegistry((coding, coding))

    with pytest.raises(ValueError, match="duplicate_department_id"):
        AgentRegistry(
            (coding,),
            (engineering, engineering),
        )

    left = AgentRegistry((research, coding), (engineering,))
    right = AgentRegistry((coding, research), (engineering,))

    assert [item.agent_id for item in left.list_agents()] == ["coding", "research"]
    assert left.version == right.version


def test_registry_version_changes_for_material_agent_or_department_change():
    baseline = AgentRegistry(
        (agent_spec(),),
        (
            DepartmentSpec(
                department_id="engineering",
                objective="Coordinate engineering.",
                producer_agent_ids=("coding",),
            ),
        ),
    )
    changed_agent = AgentRegistry(
        (agent_spec(version=2),),
        baseline.list_departments(),
    )
    changed_department = AgentRegistry(
        baseline.list_agents(),
        (
            DepartmentSpec(
                department_id="engineering",
                version=2,
                objective="Coordinate engineering.",
                producer_agent_ids=("coding",),
            ),
        ),
    )

    assert baseline.version != changed_agent.version
    assert baseline.version != changed_department.version


def test_specialist_organizational_level_preserves_legacy_registry_identity():
    registry = default_agent_registry()

    assert registry.version == "b02890f40e6c0892260d8c75603fd5dce00df91ac8d4dc60d57f60ca87d9b25a"
    assert coding_agent_spec().organizational_level == "specialist"
    assert research_agent_spec().organizational_level == "specialist"

    promoted_coding = coding_agent_spec().model_copy(update={"organizational_level": "manager"})
    changed = AgentRegistry(
        (promoted_coding, research_agent_spec()),
        registry.list_departments(),
    )

    assert changed.version != registry.version


def test_activation_validation_rejects_missing_surfaces_and_inactive_assignments():
    active = agent_spec()
    registry = AgentRegistry((active,))

    with pytest.raises(ValueError, match="agent_task_kind_unavailable"):
        registry.validate_activation(
            available_task_kinds=(),
            available_tools=("repo_status",),
        )

    with pytest.raises(ValueError, match="agent_tool_unavailable"):
        registry.validate_activation(
            available_task_kinds=("developer_run",),
            available_tools=(),
        )

    draft = agent_spec(lifecycle="draft")
    department = DepartmentSpec(
        department_id="engineering",
        objective="Coordinate engineering.",
        producer_agent_ids=("coding",),
        lifecycle="active",
    )
    with pytest.raises(ValueError, match="department_agent_not_active"):
        AgentRegistry((draft,), (department,)).validate_activation(
            available_task_kinds=("developer_run",),
            available_tools=("repo_status",),
        )


def test_factory_binds_only_active_exact_registry_snapshots():
    registry = AgentRegistry((agent_spec(),))
    factory = AgentFactory(registry)
    binding = factory.bind_agent("coding")

    assert binding.spec == agent_spec()
    assert factory.matches_agent(binding)

    changed = AgentFactory(AgentRegistry((agent_spec(version=2),)))
    assert not changed.matches_agent(binding)

    with pytest.raises(ValueError, match="agent_not_registered"):
        factory.bind_agent("missing")

    inactive = AgentFactory(AgentRegistry((agent_spec(lifecycle="retired"),)))
    with pytest.raises(ValueError, match="agent_not_active"):
        inactive.bind_agent("coding")


def test_default_registry_generalizes_existing_coding_and_research_specialists():
    registry = default_agent_registry()
    registry.validate_activation(
        available_task_kinds=BUILTIN_AGENT_TASK_KINDS,
        available_tools=(
            "list_workspace_files",
            "read_workspace_file",
            "repo_status",
            "system_info",
        ),
    )

    coding = registry.get_agent("coding")
    research = registry.get_agent("research")
    assert coding == coding_agent_spec()
    assert research == research_agent_spec()
    assert coding is not None and coding.worker_requirement is not None
    assert coding.worker_requirement.role == "developer"
    assert research is not None and research.worker_requirement is None
    assert "system_info" not in coding.allowed_tools
    assert "system_info" not in research.allowed_tools
    assert [item.department_id for item in registry.list_departments()] == [
        "engineering",
        "research",
    ]


def test_application_exposes_validated_agent_factory(client):
    registry = client.app.state.agent_registry
    factory = client.app.state.agent_factory

    assert registry.get_agent("coding") is not None
    assert registry.get_agent("research") is not None
    assert factory.bind_agent("coding").registry_version == registry.version
    assert factory.bind_department("engineering").registry_version == registry.version
