"""Regression contract: REA catalog must not activate execution."""

import json
from pathlib import Path

import pytest

from app.agent_factory import AgentFactory, default_agent_registry, reverse_engineering_agent_spec
from app.capabilities import default_capability_registry
from app.skills import default_skill_registry
from tests.conftest import AUTH


def test_rea_draft_agent_is_present_but_cannot_be_bound():
    spec = reverse_engineering_agent_spec()
    registry = default_agent_registry()
    assert registry.get_agent("reverse_engineering") == spec
    assert spec.lifecycle == "draft"
    assert spec.organizational_level == "specialist"
    assert spec.memory_namespace == "agent/reverse_engineering"
    assert spec.skill_learning == "disabled"
    assert spec.skill_ids == ("rea_investigation",)
    assert spec.procedures == ("rea_investigation",)
    assert spec.allowed_task_kinds == ()
    assert spec.allowed_tools == ()
    assert spec.permissions == ()
    assert spec.computer is None
    assert spec.worker_requirement is None
    assert not spec.allow_subagents

    with pytest.raises(ValueError, match="agent_not_active"):
        AgentFactory(registry).bind_agent("reverse_engineering")
    assert all(
        "reverse_engineering" not in department.producer_agent_ids
        for department in registry.list_departments()
        if department.lifecycle == "active"
    )


def test_rea_capability_and_skill_remain_inert():
    capability_registry = default_capability_registry()
    spec = capability_registry.get("reverse_engineering")
    assert spec is not None
    assert spec.lifecycle == "draft"
    assert spec.subjects == ("agent",)
    with pytest.raises(ValueError, match="capability_not_active:reverse_engineering"):
        capability_registry.require("reverse_engineering", subject="agent")

    skills = default_skill_registry()
    skill = skills.get("rea_investigation")
    assert skill is not None
    assert skill.lifecycle == "draft"
    assert skill.required_capabilities == ("reverse_engineering",)
    assert skill.task_kinds == ()
    assert skill.tools == ()
    skills.validate_agents(default_agent_registry().list_agents())


def test_rea_manifest_never_admits_execution():
    path = Path(__file__).resolve().parents[1] / "integrations" / "rea" / "integration.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["lifecycle"] == "draft"
    assert manifest["source_repository"] == "https://github.com/morluto/rea"
    assert manifest["license"] == "MIT"
    assert manifest["package"]["name"] == "rea-agents"
    assert manifest["package"]["version"] == "5.0.0"
    assert manifest["package"]["transport"] == "stdio"
    assert manifest["source_integrity_qualified"] is False
    assert manifest["core_task_kind"] is None
    assert manifest["worker_authority_profile"] is None
    for key, value in manifest.items():
        if key.endswith("_enabled"):
            assert value is False


def test_rea_draft_absent_from_live_activation_evaluation(client):
    response = client.get("/agent-factory/activation", headers=AUTH)
    assert response.status_code == 200, response.text
    assert "reverse_engineering" not in [
        entry["agent_id"] for entry in response.json()["evaluations"]
    ]
