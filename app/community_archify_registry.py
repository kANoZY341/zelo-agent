"""Source-grounded, read-only Archify view of trusted Agent Factory definitions.

No AgentInstance states, live running processes, permissions or grants are
inferred from these registered specs. Never load untrusted upstream renderers.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_factory import AgentRegistry
from app.community_archify import (
    ArchitectureComponent,
    ArchitectureConnection,
    ArchitectureDraft,
    CommunityArchifyRefusal,
    export_archify_json,
    export_mermaid,
)

MAX_AGENTS = 22
MAX_DEPARTMENTS = 8


class RegistryArchitectureView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-archify-registry-template-evidence-v1"] = (
        "zelo-archify-registry-template-evidence-v1"
    )
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: Literal["trusted_agent_factory_registry"] = "trusted_agent_factory_registry"
    agent_count: int = Field(ge=0, le=MAX_AGENTS)
    department_count: int = Field(ge=0, le=MAX_DEPARTMENTS)
    archify_json: str = Field(min_length=64, max_length=131072)
    mermaid_text: str = Field(min_length=10, max_length=32768)
    live_agent_instances_included: Literal[False] = False
    live_authority_claimed: Literal[False] = False
    model_prompt_created: Literal[False] = False
    upstream_renderer_executed: Literal[False] = False
    worker_or_tool_grants: tuple[()] = ()
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _digest(self):
        if self.evidence_sha256 != _sha(self.model_dump(mode="json", exclude={"evidence_sha256"})):
            raise ValueError("registry_diagram_digest_invalid")
        return self


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("ascii")
    ).hexdigest()


def _component_id(prefix: str, identifier: str) -> str:
    if not identifier or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in identifier):
        raise CommunityArchifyRefusal("registry_diagram_identifier_invalid")
    if not identifier[0].isalpha() or len(identifier) > 64:
        raise CommunityArchifyRefusal("registry_diagram_identifier_invalid")
    return prefix + identifier


def agent_registry_architecture(registry: AgentRegistry) -> RegistryArchitectureView:
    """Use Core-owned immutable template registry only; no database or Worker read."""

    if type(registry) is not AgentRegistry:
        raise CommunityArchifyRefusal("registry_diagram_source_invalid")
    agents = registry.list_agents()
    departments = registry.list_departments()
    if len(agents) > MAX_AGENTS or len(departments) > MAX_DEPARTMENTS:
        raise CommunityArchifyRefusal("registry_diagram_too_many_components")
    if len(registry.version) != 64:
        raise CommunityArchifyRefusal("registry_diagram_version_invalid")
    ids = {agent.agent_id for agent in agents}
    components = [
        ArchitectureComponent("agentFactory", "Zelo Agent Factory Registry", "security"),
    ]
    edges: list[ArchitectureConnection] = []
    assigned = set()
    for department in departments:
        dept_id = _component_id("dept_", department.department_id)
        components.append(
            ArchitectureComponent(dept_id, f"Department: {department.department_id}", "backend")
        )
        edges.append(ArchitectureConnection("agentFactory", dept_id))
        refs = department.producer_agent_ids
        if department.manager_agent_id is not None:
            refs = (department.manager_agent_id, *refs)
        if any(agent_id not in ids for agent_id in refs):
            raise CommunityArchifyRefusal("registry_diagram_unknown_agent")
        for agent_id in refs:
            assigned.add(agent_id)
            edges.append(ArchitectureConnection(dept_id, _component_id("agent_", agent_id)))
    for agent in agents:
        aid = _component_id("agent_", agent.agent_id)
        label = f"{agent.agent_id} ({agent.organizational_level}, {agent.lifecycle} spec)"
        if len(label) > 120:
            raise CommunityArchifyRefusal("registry_diagram_label_too_long")
        components.append(ArchitectureComponent(aid, label, "backend"))
        if agent.agent_id not in assigned:
            edges.append(ArchitectureConnection("agentFactory", aid))
    draft = ArchitectureDraft(
        schema="zelo-archify-architecture-export-v1",
        title="Zelo Agent Factory Registry (templates only)",
        components=tuple(components),
        connections=tuple(edges),
    )
    exported = export_archify_json(draft).decode("ascii")
    mermaid = export_mermaid(draft)
    payload = {
        "schema_version": "zelo-archify-registry-template-evidence-v1",
        "registry_version": registry.version,
        "source_kind": "trusted_agent_factory_registry",
        "agent_count": len(agents),
        "department_count": len(departments),
        "archify_json": exported,
        "mermaid_text": mermaid,
        "live_agent_instances_included": False,
        "live_authority_claimed": False,
        "model_prompt_created": False,
        "upstream_renderer_executed": False,
        "worker_or_tool_grants": (),
    }
    return RegistryArchitectureView(**payload, evidence_sha256=_sha(payload))
