"""v0.14 deterministic Agent Factory activation/evaluation tests."""

import pytest

from app.agent_activation import AgentActivationEvaluator
from app.agent_factory import AgentRegistry, AgentSpec, WorkerRequirement, default_agent_registry
from app.developer_contracts import WorkerDescriptor
from tests.conftest import AUTH


def worker(
    *,
    role: str = "developer",
    capabilities: tuple[str, ...] = ("zelo_core_development",),
) -> WorkerDescriptor:
    return WorkerDescriptor(
        worker_id=f"{role}_test",
        role=role,
        capabilities=capabilities,
        authority=("workspace-read",),
        implementation="test",
        priority=1,
        max_concurrency=1,
    )


def executive_spec(level: str = "manager") -> AgentSpec:
    return AgentSpec(
        agent_id=f"{level}_ops",
        role=f"{level}_ops",
        organizational_level=level,
        objective=f"Coordinate bounded {level} work.",
        capabilities=("coordination",),
        permissions=("organization_hire",),
        procedures=("organization",),
        memory_namespace=f"agent/{level}_ops",
        lifecycle="active",
        allow_subagents=True,
        max_subagents=4,
    )


def specialist_spec(**updates) -> AgentSpec:
    base = AgentSpec(
        agent_id="analyst",
        role="analyst",
        organizational_level="specialist",
        objective="Perform bounded analysis.",
        capabilities=("analysis",),
        allowed_task_kinds=("research_run",),
        allowed_tools=("repo_status",),
        permissions=("workspace_read",),
        procedures=("analysis",),
        memory_namespace="agent/analyst",
        lifecycle="active",
    )
    return base.model_copy(update=updates)


@pytest.mark.parametrize("level", ["vp", "director", "manager"])
def test_executive_templates_require_bounded_organization_authority(level):
    valid = executive_spec(level)
    accepted = AgentActivationEvaluator(
        AgentRegistry((valid,)),
        available_task_kinds=(),
        available_tools=(),
    ).require()
    assert accepted.status == "accepted"

    invalid = valid.model_copy(
        update={
            "allow_subagents": False,
            "max_subagents": 0,
            "permissions": (),
        }
    )
    report = AgentActivationEvaluator(
        AgentRegistry((invalid,)),
        available_task_kinds=(),
        available_tools=(),
    ).evaluate()

    assert report.status == "rejected"
    evaluation = report.evaluations[0]
    assert "executive_hiring_authority" in evaluation.failure_codes
    with pytest.raises(
        ValueError,
        match=f"agent_activation_rejected:{invalid.agent_id}:executive_hiring_authority",
    ):
        AgentActivationEvaluator(
            AgentRegistry((invalid,)),
            available_task_kinds=(),
            available_tools=(),
        ).require()


def test_specialist_cannot_activate_with_permanent_hiring_permission():
    invalid = specialist_spec(
        permissions=("organization_hire", "workspace_read"),
        allow_subagents=True,
        max_subagents=1,
    )
    report = AgentActivationEvaluator(
        AgentRegistry((invalid,)),
        available_task_kinds=("research_run",),
        available_tools=("repo_status",),
    ).evaluate()

    assert report.status == "rejected"
    assert report.evaluations[0].failure_codes == ("specialist_hiring_boundary",)


def test_developer_task_requires_worker_requirement_and_available_worker():
    missing_requirement = specialist_spec(
        allowed_task_kinds=("developer_run",),
        capabilities=("code_change",),
        procedures=("implementation",),
    )
    report = AgentActivationEvaluator(
        AgentRegistry((missing_requirement,)),
        available_task_kinds=("developer_run",),
        available_tools=("repo_status",),
        worker_descriptors=(worker(),),
    ).evaluate()
    assert "developer_worker_binding" in report.evaluations[0].failure_codes

    required = missing_requirement.model_copy(
        update={
            "worker_requirement": WorkerRequirement(
                role="developer",
                capabilities=("zelo_core_development",),
            )
        }
    )
    unavailable = AgentActivationEvaluator(
        AgentRegistry((required,)),
        available_task_kinds=("developer_run",),
        available_tools=("repo_status",),
        worker_descriptors=(),
    ).evaluate()
    assert "worker_requirement" in unavailable.evaluations[0].failure_codes

    accepted = AgentActivationEvaluator(
        AgentRegistry((required,)),
        available_task_kinds=("developer_run",),
        available_tools=("repo_status",),
        worker_descriptors=(worker(),),
    ).require()
    assert accepted.status == "accepted"


def test_review_required_template_must_declare_review_contract():
    invalid = specialist_spec(
        allowed_task_kinds=("developer_run",),
        capabilities=("code_change",),
        procedures=("implementation",),
        worker_requirement=WorkerRequirement(
            role="developer",
            capabilities=("zelo_core_development",),
        ),
        review_required=True,
    )
    report = AgentActivationEvaluator(
        AgentRegistry((invalid,)),
        available_task_kinds=("developer_run",),
        available_tools=("repo_status",),
        worker_descriptors=(worker(),),
    ).evaluate()

    assert "review_contract" in report.evaluations[0].failure_codes


def test_activation_rejects_missing_task_and_tool_surfaces():
    spec = specialist_spec(
        allowed_task_kinds=("research_run",),
        allowed_tools=("repo_status",),
    )
    report = AgentActivationEvaluator(
        AgentRegistry((spec,)),
        available_task_kinds=(),
        available_tools=(),
    ).evaluate()

    assert report.evaluations[0].failure_codes == ("task_surface", "tool_surface")


def test_default_registry_activation_report_is_deterministic():
    registry = default_agent_registry()
    kwargs = {
        "available_task_kinds": ("developer_run", "research_run"),
        "available_tools": (
            "list_workspace_files",
            "read_workspace_file",
            "repo_status",
            "system_info",
        ),
        "worker_descriptors": (worker(),),
    }
    first = AgentActivationEvaluator(registry, **kwargs).require()
    second = AgentActivationEvaluator(registry, **kwargs).require()

    assert first == second
    assert first.status == "accepted"
    assert [item.agent_id for item in first.evaluations] == ["coding", "research"]
    assert len(first.report_digest) == 64


def test_application_exposes_operator_only_activation_evidence(client):
    response = client.get("/agent-factory/activation", headers=AUTH)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "accepted"
    assert [item["agent_id"] for item in payload["evaluations"]] == ["coding", "research"]

    response = client.get(
        "/agent-factory/activation",
        headers={"Authorization": "Bearer invalid-operator-token"},
    )
    assert response.status_code == 401
