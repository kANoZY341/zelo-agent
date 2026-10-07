"""v0.15 trusted non-authoritative Skill Registry tests."""

import pytest
from pydantic import ValidationError

from app.agent_factory import AgentSpec, default_agent_registry
from app.capabilities import CapabilityRegistry, CapabilitySpec, default_capability_registry
from app.skills import SkillRegistry, SkillSpec, default_skill_registry
from tests.conftest import AUTH


def skill(
    skill_id: str,
    *,
    capabilities: tuple[str, ...] = ("research",),
    procedures: tuple[str, ...] = ("evidence_retrieval",),
    task_kinds: tuple[str, ...] = ("research_run",),
    tools: tuple[str, ...] = ("repo_status",),
    lifecycle: str = "active",
) -> SkillSpec:
    return SkillSpec(
        skill_id=skill_id,
        description=f"Trusted {skill_id} skill.",
        required_capabilities=capabilities,
        procedures=procedures,
        task_kinds=task_kinds,
        tools=tools,
        lifecycle=lifecycle,
    )


def validate(registry: SkillRegistry, capabilities: CapabilityRegistry | None = None) -> None:
    registry.validate(
        capabilities or default_capability_registry(),
        available_task_kinds=(
            "api_run",
            "computer_run",
            "developer_run",
            "python_run",
            "research_run",
            "shell_run",
        ),
        available_tools=(
            "list_workspace_files",
            "read_workspace_file",
            "repo_status",
            "system_info",
        ),
    )


def test_registry_version_is_deterministic_and_order_independent():
    left = SkillRegistry((skill("b"), skill("a")))
    right = SkillRegistry((skill("a"), skill("b")))

    assert left.version == right.version
    assert [item.skill_id for item in left.list()] == ["a", "b"]
    assert left.snapshot() == right.snapshot()


def test_registry_version_changes_with_material_definition():
    baseline = SkillRegistry((skill("research"),))
    changed = SkillRegistry(
        (
            skill(
                "research",
                tools=("read_workspace_file", "repo_status"),
            ),
        )
    )

    assert baseline.version != changed.version


def test_duplicate_skill_ids_fail_closed():
    with pytest.raises(ValueError, match="duplicate_skill_id"):
        SkillRegistry((skill("research"), skill("research")))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("required_capabilities", ("research", "research"), "required_capabilities_not_canonical"),
        ("procedures", ("review", "implementation"), "procedures_not_canonical"),
        ("task_kinds", ("research_run", "research_run"), "task_kinds_not_canonical"),
        ("tools", ("repo_status", "repo_status"), "tools_not_canonical"),
    ],
)
def test_skill_lists_must_be_canonical(field, value, message):
    kwargs = {
        "skill_id": "canonical",
        "description": "Canonical validation case.",
        "required_capabilities": ("research",),
        "procedures": ("evidence_retrieval",),
        "task_kinds": ("research_run",),
        "tools": ("repo_status",),
        "lifecycle": "active",
    }
    kwargs[field] = value

    with pytest.raises(ValidationError, match=message):
        SkillSpec(**kwargs)


def test_active_skill_rejects_unknown_or_wrong_subject_capability():
    unknown = SkillRegistry((skill("unknown", capabilities=("missing",)),))
    with pytest.raises(ValueError, match="capability_not_registered:missing"):
        validate(unknown)

    worker_only = CapabilityRegistry(
        (
            CapabilitySpec(
                capability_id="worker_only",
                description="Worker-only test capability.",
                subjects=("worker",),
                lifecycle="active",
            ),
        )
    )
    mismatch = SkillRegistry((skill("mismatch", capabilities=("worker_only",)),))
    with pytest.raises(ValueError, match="capability_subject_mismatch:worker_only:agent"):
        validate(mismatch, worker_only)


def test_active_skill_rejects_unavailable_task_or_tool():
    missing_task = SkillRegistry((skill("task", task_kinds=("missing_task",)),))
    with pytest.raises(ValueError, match="skill_task_kind_unavailable:task:missing_task"):
        validate(missing_task)

    missing_tool = SkillRegistry((skill("tool", tools=("missing_tool",)),))
    with pytest.raises(ValueError, match="skill_tool_unavailable:tool:missing_tool"):
        validate(missing_tool)


def test_default_registry_validates_current_surfaces_and_agents():
    registry = default_skill_registry()
    validate(registry)
    registry.validate_agents(default_agent_registry().list_agents())

    assert [item.skill_id for item in registry.list()] == [
        "api_read",
        "computer_navigation",
        "computer_observation",
        "core_implementation",
        "core_review",
        "evidence_retrieval",
        "python_execution",
        "rea_investigation",
        "shell_execution",
    ]


def test_inactive_skill_does_not_cover_active_agent_procedure():
    registry = SkillRegistry(
        (
            skill(
                "research",
                lifecycle="draft",
            ),
        )
    )
    agent = AgentSpec(
        agent_id="custom",
        role="custom",
        objective="Require a trusted active skill.",
        capabilities=("research",),
        allowed_task_kinds=("research_run",),
        allowed_tools=("repo_status",),
        procedures=("evidence_retrieval",),
        memory_namespace="agent/custom",
        lifecycle="active",
    )

    with pytest.raises(
        ValueError,
        match="agent_procedure_skill_unavailable:custom:evidence_retrieval",
    ):
        registry.validate_agents((agent,))


def test_skill_must_fit_agent_capability_task_and_tool_bounds():
    registry = SkillRegistry((skill("research"),))
    base = {
        "agent_id": "custom",
        "role": "custom",
        "objective": "Exercise skill compatibility boundaries.",
        "capabilities": ("research",),
        "allowed_task_kinds": ("research_run",),
        "allowed_tools": ("repo_status",),
        "procedures": ("evidence_retrieval",),
        "memory_namespace": "agent/custom",
        "lifecycle": "active",
    }

    registry.validate_agents((AgentSpec(**base),))

    for update in (
        {"capabilities": ("evidence_synthesis",)},
        {"allowed_task_kinds": ("developer_run",)},
        {"allowed_tools": ("system_info",)},
    ):
        with pytest.raises(ValueError, match="agent_procedure_skill_unavailable"):
            registry.validate_agents((AgentSpec(**(base | update)),))


def test_application_exposes_operator_only_read_only_skill_snapshot(client):
    response = client.get("/skills", headers=AUTH)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["registry_schema"] == "zelo-skill-registry-v1"
    assert payload["registry_version"] == client.app.state.skill_registry.version
    assert [item["skill_id"] for item in payload["skills"]] == [
        "api_read",
        "computer_navigation",
        "computer_observation",
        "core_implementation",
        "core_review",
        "evidence_retrieval",
        "python_execution",
        "rea_investigation",
        "shell_execution",
    ]

    rejected = client.get(
        "/skills",
        headers={"Authorization": "Bearer invalid-operator-token"},
    )
    assert rejected.status_code == 401

    mutation = client.post("/skills", headers=AUTH, json={})
    assert mutation.status_code == 405


def test_skill_registry_has_no_execution_method():
    registry = default_skill_registry()

    assert not hasattr(registry, "execute")
    assert not hasattr(registry, "run")


def test_explicit_agent_skill_binding_must_be_active_and_compatible():
    registry = SkillRegistry((skill("research"),))
    agent = AgentSpec(
        agent_id="custom",
        role="custom",
        objective="Use an explicitly installed research skill.",
        capabilities=("research",),
        allowed_task_kinds=("research_run",),
        allowed_tools=("repo_status",),
        procedures=("evidence_retrieval",),
        skill_ids=("research",),
        memory_namespace="agent/custom",
        lifecycle="active",
    )
    registry.validate_agents((agent,))

    unavailable = agent.model_copy(update={"skill_ids": ("missing",)})
    with pytest.raises(ValueError, match="agent_skill_unavailable:custom:missing"):
        registry.validate_agents((unavailable,))

    incompatible = agent.model_copy(update={"allowed_tools": ()})
    with pytest.raises(ValueError, match="agent_skill_incompatible:custom:research"):
        registry.validate_agents((incompatible,))
