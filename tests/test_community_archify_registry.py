"""Archify diagrams must reflect trusted registry templates, not live agents."""

import hashlib
import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.agent_factory import AgentRegistry, AgentSpec, DepartmentSpec, default_agent_registry
from app.community_archify import CommunityArchifyRefusal
from app.community_archify_registry import (
    RegistryArchitectureView,
    agent_registry_architecture,
)
from app.community_sources import CommunitySourceRefusal
from tests.conftest import AUTH

ROUTE = "/integration-candidates/community/architecture/agent-registry"


def _agent(name, *, lifecycle="draft"):
    return AgentSpec(
        agent_id=name,
        role=name,
        objective="Fixture draft only",
        capabilities=("code_planning",),
        memory_namespace=f"agent/{name}",
        lifecycle=lifecycle,
    )


def test_default_diagram_is_deterministic_source_grounded_and_never_live():
    registry = default_agent_registry()
    diagram = agent_registry_architecture(registry)
    assert diagram == agent_registry_architecture(registry)
    assert diagram.registry_version == registry.version
    assert diagram.agent_count == len(registry.list_agents())
    assert diagram.department_count == len(registry.list_departments())
    parsed = json.loads(diagram.archify_json)
    assert parsed["diagram_type"] == "architecture"
    assert parsed["meta"]["title"].endswith("(templates only)")
    nodes = {node["id"] for node in parsed["components"]}
    assert {"agentFactory", "agent_coding", "agent_research"}.issubset(nodes)
    assert {"dept_engineering", "dept_research"}.issubset(nodes)
    assert all(edge["from"] in nodes and edge["to"] in nodes for edge in parsed["connections"])
    assert "flowchart TD" in diagram.mermaid_text
    assert not diagram.live_agent_instances_included
    assert not diagram.live_authority_claimed
    assert not diagram.upstream_renderer_executed
    assert not diagram.model_prompt_created
    assert diagram.worker_or_tool_grants == ()
    assert (
        diagram.evidence_sha256
        == hashlib.sha256(
            json.dumps(
                diagram.model_dump(mode="json", exclude={"evidence_sha256"}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
    )
    assert RegistryArchitectureView.model_validate_json(diagram.model_dump_json(), strict=True) == (
        diagram
    )


def test_registered_draft_templates_visible_but_not_claimed_running():
    registry = AgentRegistry(
        agents=(_agent("draft_one"), _agent("draft_two", lifecycle="retired")),
        departments=(
            DepartmentSpec(
                department_id="test_department",
                objective="Fixture grouping",
                manager_agent_id="draft_one",
                producer_agent_ids=("draft_two",),
            ),
        ),
    )
    diagram = agent_registry_architecture(registry)
    assert diagram.agent_count == 2 and diagram.department_count == 1
    data = json.loads(diagram.archify_json)
    labels = [x["label"] for x in data["components"]]
    assert any("draft_one (specialist, draft spec)" in label for label in labels)
    assert any("draft_two (specialist, retired spec)" in label for label in labels)
    assert not diagram.live_agent_instances_included and not diagram.live_authority_claimed


def test_empty_registry_is_valid_and_does_not_claim_live_status():
    diagram = agent_registry_architecture(AgentRegistry())
    assert diagram.agent_count == 0 and diagram.department_count == 0
    assert len(json.loads(diagram.archify_json)["components"]) == 1


def test_invalid_source_and_oversized_registration_refuse_without_truncating():
    with pytest.raises(CommunityArchifyRefusal, match="registry_diagram_source_invalid"):
        agent_registry_architecture({})
    registry = AgentRegistry(agents=tuple(_agent(f"ag{i:02d}") for i in range(23)))
    with pytest.raises(CommunityArchifyRefusal, match="registry_diagram_too_many_components"):
        agent_registry_architecture(registry)


def test_mutated_digest_and_runtime_authority_flags_refuse():
    record = agent_registry_architecture(default_agent_registry()).model_dump(mode="json")
    record["registry_version"] = "0" * 64
    with pytest.raises(ValidationError, match="registry_diagram_digest_invalid"):
        RegistryArchitectureView.model_validate_json(json.dumps(record), strict=True)
    record = agent_registry_architecture(default_agent_registry()).model_dump(mode="json")
    record["live_agent_instances_included"] = True
    with pytest.raises(ValidationError):
        RegistryArchitectureView.model_validate_json(json.dumps(record), strict=True)


def test_operator_only_read_route_does_not_grant_actions(client):
    response = client.get(ROUTE, headers=AUTH)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_kind"] == "trusted_agent_factory_registry"
    assert not data["live_authority_claimed"]
    assert data["worker_or_tool_grants"] == []
    assert client.get(ROUTE, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    assert client.post(ROUTE, headers=AUTH, json={}).status_code == 405


def test_pinned_source_refusal_is_generic_unavailable_and_never_reveals_path(client):
    with patch(
        "app.community_archify.read_pinned_reference",
        side_effect=CommunitySourceRefusal("community_source_bytes_changed"),
    ):
        response = client.get(ROUTE, headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "archify_registry_unavailable"}
    assert "community_source_bytes_changed" not in response.text
