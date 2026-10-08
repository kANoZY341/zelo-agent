"""REA metadata is visible to operators but grants no live execution authority."""

import json
from pathlib import Path

import pytest

from app.agent_factory import (
    AgentFactory,
    default_agent_registry,
    reverse_engineering_agent_spec,
)
from app.capabilities import (
    default_capability_registry,
    rea_reverse_engineering_capability_spec,
)
from app.rea_candidate import rea_candidate_snapshot
from app.skills import default_skill_registry, rea_investigation_skill_spec
from tests.conftest import AUTH


def test_rea_preserves_live_agent_registry_identity():
    registry = default_agent_registry()
    assert registry.version == ("ea9738d7ca520c07ae467b4cc7f3d6d8b56ab7e83cd6475dac38e662b0db8fe6")
    assert registry.get_agent("reverse_engineering") is None
    assert default_capability_registry().get("reverse_engineering") is None
    assert default_skill_registry().get("rea_investigation") is None
    with pytest.raises(ValueError, match="agent_not_registered"):
        AgentFactory(registry).bind_agent("reverse_engineering")


def test_rea_candidate_defines_zero_authority():
    candidate = rea_candidate_snapshot()
    agent = candidate.agent
    assert candidate.integration_id == "rea"
    assert candidate.lifecycle == "draft"
    assert candidate.source_tag == "rea-agents-6.0.0"
    assert candidate.source_commit == "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
    assert candidate.source_path == "third_party/rea"
    assert candidate.tools_available == 139
    assert candidate.tool_grants == ()
    assert candidate.execution_enabled is False
    assert agent == reverse_engineering_agent_spec()
    assert agent.agent_id == "reverse_engineering"
    assert agent.lifecycle == "draft"
    assert agent.organizational_level == "specialist"
    assert agent.memory_namespace == "agent/reverse_engineering"
    assert agent.skill_learning == "disabled"
    assert agent.skill_ids == ("rea_investigation",)
    assert agent.procedures == ("rea_investigation",)
    assert agent.allowed_task_kinds == ()
    assert agent.allowed_tools == ()
    assert agent.permissions == ()
    assert agent.space_permissions == ()
    assert agent.computer is None
    assert agent.worker_requirement is None
    assert not agent.allow_subagents

    capability = rea_reverse_engineering_capability_spec()
    skill = rea_investigation_skill_spec()
    assert candidate.capability == capability
    assert capability.lifecycle == "draft"
    assert capability.subjects == ("agent",)
    assert candidate.skill == skill
    assert skill.lifecycle == "draft"
    assert skill.required_capabilities == ("reverse_engineering",)
    assert skill.task_kinds == ()
    assert skill.tools == ()


def test_rea_manifest_never_admits_execution():
    path = Path(__file__).resolve().parents[1] / "integrations" / "rea" / "integration.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["lifecycle"] == "draft"
    assert manifest["source_repository"] == "https://github.com/morluto/rea"
    assert manifest["license"] == "MIT"
    assert manifest["schema"] == "zelo-source-owned-rea-integration-v1"
    assert manifest["source"]["tag"] == "rea-agents-6.0.0"
    assert manifest["source"]["commit"] == "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
    assert manifest["source"]["path"] == "third_party/rea"
    assert manifest["source"]["observed_tools"] == 139
    assert manifest["mcp_tool_grants"] == []
    assert manifest["source_integrity_qualified"] is True
    assert manifest["core_task_kind"] is None
    assert manifest["worker_authority_profile"] is None
    for key, value in manifest.items():
        if key.endswith("_enabled"):
            assert value is False


def test_rea_candidate_requires_operator_and_stays_out_of_live_surfaces(client):
    accepted = client.get("/integration-candidates/rea", headers=AUTH)
    assert accepted.status_code == 200, accepted.text
    payload = accepted.json()
    assert payload["agent"]["agent_id"] == "reverse_engineering"
    assert payload["agent"]["lifecycle"] == "draft"
    assert payload["capability"]["lifecycle"] == "draft"
    assert payload["skill"]["lifecycle"] == "draft"
    assert payload["execution_enabled"] is False
    assert payload["source_tag"] == "rea-agents-6.0.0"
    assert payload["source_path"] == "third_party/rea"
    assert payload["tools_available"] == 139
    assert payload["tool_grants"] == []

    denied = client.get("/integration-candidates/rea", headers={"Authorization": "Bearer invalid"})
    assert denied.status_code == 401
    assert client.post("/integration-candidates/rea", headers=AUTH, json={}).status_code == 405

    agents = client.get("/agent-factory/activation", headers=AUTH)
    assert agents.status_code == 200
    assert [item["agent_id"] for item in agents.json()["evaluations"]] == [
        "coding",
        "research",
    ]
    assert all(
        item["capability_id"] != "reverse_engineering"
        for item in client.get("/capabilities", headers=AUTH).json()["capabilities"]
    )
    assert all(
        item["skill_id"] != "rea_investigation"
        for item in client.get("/skills", headers=AUTH).json()["skills"]
    )
