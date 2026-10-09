"""Archify-compatible architecture candidate exporter, without renderer execution.

The source schemas are MIT-licensed pinned DATA. Outputs are a reviewed strict
subset, not arbitrary HTML/JS or a network-loaded Archify renderer.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from qualification.community_sources import read_pinned_reference

ARCHIFY_SCHEMA = "zelo-archify-architecture-export-v1"


class CommunityArchifyRefusal(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArchitectureComponent:
    identifier: str
    label: str
    component_type: Literal[
        "frontend", "backend", "database", "cloud", "security", "messagebus", "external"
    ]


@dataclass(frozen=True, slots=True)
class ArchitectureConnection:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class ArchitectureDraft:
    schema: Literal["zelo-archify-architecture-export-v1"]
    title: str
    components: tuple[ArchitectureComponent, ...]
    connections: tuple[ArchitectureConnection, ...]
    upstream_renderer_invoked: Literal[False] = False
    diagram_claims_live_state: Literal[False] = False


def _validate_sources() -> None:
    common = json.loads(read_pinned_reference("archify", "archify/schemas/common.schema.json"))
    arch = json.loads(read_pinned_reference("archify", "archify/schemas/architecture.schema.json"))
    if (
        arch["properties"]["diagram_type"].get("const") != "architecture"
        or arch["properties"]["schema_version"].get("const") != 1
        or set(arch["required"]) != {"schema_version", "diagram_type", "meta", "components"}
        or common["$defs"]["componentType"].get("enum")
        != ["frontend", "backend", "database", "cloud", "security", "messagebus", "external"]
    ):
        raise CommunityArchifyRefusal("archify_source_schema_changed")


def zelo_architecture_draft() -> ArchitectureDraft:
    """Illustrative design, not a live company state or automatic permission map."""

    _validate_sources()
    components = (
        ArchitectureComponent("managingDirector", "Managing Director", "external"),
        ArchitectureComponent("zeloMind", "Zelo Mind / CEO", "backend"),
        ArchitectureComponent("zeloCore", "Zelo Core Authority", "security"),
        ArchitectureComponent("agentFactory", "Agent Factory", "backend"),
        ArchitectureComponent("specialists", "Specialist Agents", "backend"),
        ArchitectureComponent("workers", "Qualified Workers", "backend"),
        ArchitectureComponent("tools", "Approved Tools", "external"),
        ArchitectureComponent("storage", "Scoped Memory and State", "database"),
        ArchitectureComponent("operatorUI", "Talk / Control Desk / Company World", "frontend"),
    )
    connections = (
        ArchitectureConnection("managingDirector", "zeloMind"),
        ArchitectureConnection("zeloMind", "zeloCore"),
        ArchitectureConnection("operatorUI", "zeloCore"),
        ArchitectureConnection("zeloCore", "agentFactory"),
        ArchitectureConnection("agentFactory", "specialists"),
        ArchitectureConnection("specialists", "workers"),
        ArchitectureConnection("workers", "tools"),
        ArchitectureConnection("zeloCore", "storage"),
    )
    return ArchitectureDraft(ARCHIFY_SCHEMA, "Zelo Canonical Architecture", components, connections)


def export_archify_json(draft: ArchitectureDraft) -> bytes:
    _validate_sources()
    if type(draft) is not ArchitectureDraft:
        raise CommunityArchifyRefusal("archify_draft_invalid")
    identifiers = [c.identifier for c in draft.components]
    if len(draft.components) > 32 or len(set(identifiers)) != len(identifiers):
        raise CommunityArchifyRefusal("archify_duplicate_or_excessive_components")
    if not 1 <= len(draft.title) <= 120 or any(ord(c) < 32 for c in draft.title):
        raise CommunityArchifyRefusal("archify_title_invalid")
    for item in draft.components:
        if (
            type(item.identifier) is not str
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", item.identifier) is None
        ):
            raise CommunityArchifyRefusal("archify_component_id_invalid")
        if item.component_type not in (
            "frontend",
            "backend",
            "database",
            "cloud",
            "security",
            "messagebus",
            "external",
        ):
            raise CommunityArchifyRefusal("archify_component_type_invalid")
        if not 1 <= len(item.label) <= 120 or any(ord(c) < 32 for c in item.label):
            raise CommunityArchifyRefusal("archify_component_label_invalid")
    if len(draft.connections) > 64 or any(
        edge.source not in identifiers or edge.target not in identifiers
        for edge in draft.connections
    ):
        raise CommunityArchifyRefusal("archify_edge_invalid")
    payload = {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": {"title": draft.title, "output": "zelo-architecture.html"},
        "components": [
            {"id": c.identifier, "type": c.component_type, "label": c.label}
            for c in draft.components
        ],
        "connections": [{"from": e.source, "to": e.target} for e in draft.connections],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
        "ascii"
    )


def export_mermaid(draft: ArchitectureDraft) -> str:
    """Optional static preview, no scripts or execution from untrusted source."""

    export_archify_json(draft)
    lines = ["flowchart TD"]
    for node in draft.components:
        label = (
            node.label.replace("\\", "\\\\").replace('"', "'").replace("[", "(").replace("]", ")")
        )
        lines.append(f'    {node.identifier}["{label}"]')
    for edge in draft.connections:
        lines.append(f"    {edge.source} --> {edge.target}")
    return "\n".join(lines) + "\n"
