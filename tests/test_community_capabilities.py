"""ECC, Harness, Archify and Context Mode-inspired bounded foundations."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.community_archify import (
    ArchitectureComponent,
    ArchitectureConnection,
    CommunityArchifyRefusal,
    export_archify_json,
    export_mermaid,
    zelo_architecture_draft,
)
from app.community_context_window import (
    CommunityContextRefusal,
    index_tool_output,
    read_explicit_window,
)
from app.community_harness import TeamBlueprint, propose_team
from app.community_skill_catalog import community_skill_candidates


def test_ecc_and_partner_skill_library_is_draft_only():
    candidates = community_skill_candidates()
    assert len(candidates) == 6
    assert {x.skill.skill_id for x in candidates} == {
        "ecc_security_review",
        "ecc_agent_evaluation",
        "ecc_ai_regression",
        "ecc_architecture_decisions",
        "harness_team_design",
        "archify_architecture",
    }
    assert all(x.skill.lifecycle == "draft" for x in candidates)
    assert all(not x.runtime_authority and not x.publication_authority for x in candidates)
    assert all(x.skill.tools == () and x.skill.task_kinds == () for x in candidates)
    assert all(x.reviewed_steps and not x.tool_grants for x in candidates)
    assert len({x.upstream_path for x in candidates}) == len(candidates)
    assert all(len(x.upstream_commit) == 40 for x in candidates)
    from app.skills import default_skill_registry

    active = default_skill_registry()
    assert all(active.get(item.skill.skill_id) is None for item in candidates)


@pytest.mark.parametrize("template", ("engineering_quality", "business_research"))
def test_harness_team_is_strict_immutable_draft_with_no_grants(template):
    proposed = propose_team(template)
    assert proposed == propose_team(template)
    assert len(proposed.roles) == 4
    assert proposed.managing_director_approval_required
    assert not proposed.ready_to_activate
    assert not proposed.tasks_created
    assert proposed.tool_grants == () and proposed.worker_grants == ()
    assert tuple(r.organizational_level for r in proposed.roles) == (
        "vp",
        "director",
        "manager",
        "specialist",
    )
    assert proposed.parent_ids[0] is None
    assert all(r.lifecycle == "draft" and not r.allowed_tools for r in proposed.roles)
    assert all(not r.allowed_task_kinds and r.space_permissions == () for r in proposed.roles)
    from app.agent_factory import default_agent_registry

    actual = default_agent_registry()
    assert all(actual.get_agent(role.agent_id) is None for role in proposed.roles)

    assert (
        TeamBlueprint.model_validate_json(json.dumps(proposed.model_dump(mode="json")), strict=True)
        == proposed
    )


def test_harness_rejects_grants_hierarchy_corruption_and_unknown_team():
    with pytest.raises(ValueError, match="harness_template_unknown"):
        propose_team("unreviewed-team")
    plan = propose_team("engineering_quality")
    altered = plan.model_dump(mode="json")
    altered["roles"][0]["lifecycle"] = "active"
    with pytest.raises(ValidationError, match="harness_blueprint_execution_forbidden"):
        TeamBlueprint.model_validate_json(json.dumps(altered), strict=True)
    altered = plan.model_dump(mode="json")
    altered["parent_ids"][1] = "unrelated-manager"
    with pytest.raises(ValidationError, match="harness_reporting_graph_invalid"):
        TeamBlueprint.model_validate_json(json.dumps(altered), strict=True)
    altered = plan.model_dump(mode="json")
    altered["ready_to_activate"] = True
    with pytest.raises(ValidationError):
        TeamBlueprint.model_validate_json(json.dumps(altered), strict=True)


def test_archify_export_matches_pinned_architecture_schema_subset():
    draft = zelo_architecture_draft()
    payload = json.loads(export_archify_json(draft))
    assert payload["schema_version"] == 1
    assert payload["diagram_type"] == "architecture"
    assert len(payload["components"]) == 9
    assert payload["meta"]["output"] == "zelo-architecture.html"
    assert set(payload) == {"schema_version", "diagram_type", "meta", "components", "connections"}
    assert {x["from"] for x in payload["connections"]}.issubset(
        {x["id"] for x in payload["components"]}
    )
    assert "flowchart TD" in export_mermaid(draft)
    assert "zeloCore" in export_mermaid(draft)
    assert not draft.upstream_renderer_invoked
    assert not draft.diagram_claims_live_state


def test_archify_refuses_untrusted_graph_edges_and_duplicate_ids():
    draft = zelo_architecture_draft()
    with pytest.raises(CommunityArchifyRefusal, match="archify_edge_invalid"):
        export_archify_json(replace(draft, connections=(ArchitectureConnection("a", "b"),)))
    with pytest.raises(CommunityArchifyRefusal, match="archify_duplicate_or_excessive_components"):
        export_archify_json(replace(draft, components=draft.components + draft.components[:1]))
    with pytest.raises(CommunityArchifyRefusal, match="archify_component_label_invalid"):
        export_archify_json(
            replace(draft, components=(ArchitectureComponent("x", "bad\x00", "backend"),))
        )


def test_archify_rejects_empty_graph_required_by_pinned_schema():
    draft = zelo_architecture_draft()
    with pytest.raises(CommunityArchifyRefusal, match="archify_components_required"):
        export_archify_json(replace(draft, components=(), connections=()))


def test_context_mode_independent_concepts_index_and_explicit_retrieval():
    raw = b"\n".join(f"Line {i}: untrusted data".encode() for i in range(1, 103))
    entry = index_tool_output(raw)
    assert len(entry.source_sha256) == 64
    assert entry.line_count == 102
    assert entry.excerpt_is_partial
    assert "Line 102" not in entry.excerpt
    assert not entry.provider_imported and not entry.instructions_trusted
    assert not entry.tool_execution_allowed
    picked = read_explicit_window(raw, expected_sha256=entry.source_sha256, start_line=101, count=2)
    assert "Line 101" in picked and "Line 102" in picked
    assert "Line 1:" not in picked


@pytest.mark.parametrize(
    "bad",
    [
        ("wrong_sha", {}),
        ("invalid_start", {"start_line": 0}),
        ("count_excess", {"count": 81}),
        ("missing_line", {"start_line": 5}),
        ("bool_start", {"start_line": True}),
    ],
)
def test_context_window_rejects_unauthorized_retrieval(bad):
    name, change = bad
    raw = b"one\ntwo\n"
    entry = index_tool_output(raw)
    kwargs = {"expected_sha256": entry.source_sha256, "start_line": 1, "count": 1}
    kwargs.update(change)
    if name == "wrong_sha":
        kwargs["expected_sha256"] = "0" * 64
    with pytest.raises(CommunityContextRefusal):
        read_explicit_window(raw, **kwargs)


def test_context_refuses_binary_or_oversize_data_and_long_windows():
    with pytest.raises(CommunityContextRefusal):
        index_tool_output(b"binary\x00")
    with pytest.raises(CommunityContextRefusal):
        index_tool_output(b"a" * (1024 * 1024 + 1))
    raw = b"x" * 17000
    entry = index_tool_output(raw)
    with pytest.raises(CommunityContextRefusal, match="context_window_size_limit"):
        read_explicit_window(raw, expected_sha256=entry.source_sha256, start_line=1, count=1)


def test_archify_rejects_schema_invalid_identifier_and_type():
    draft = zelo_architecture_draft()
    for invalid_id in ("123", "space id", "x.y", "é", ""):
        altered = replace(
            draft,
            components=(ArchitectureComponent(invalid_id, "node", "backend"),),
            connections=(),
        )
        with pytest.raises(CommunityArchifyRefusal, match="archify_component_id_invalid"):
            export_archify_json(altered)
    altered = replace(
        draft,
        components=(ArchitectureComponent("valid", "node", "shell"),),
        connections=(),
    )
    with pytest.raises(CommunityArchifyRefusal, match="archify_component_type_invalid"):
        export_archify_json(altered)


def test_committed_architecture_examples_reproduce_current_reviewed_projection():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    draft = zelo_architecture_draft()
    assert (repo / "examples/curated/zelo-architecture.mmd").read_text() == export_mermaid(draft)
    assert (repo / "examples/curated/zelo-architecture.archify.json").read_bytes() == (
        export_archify_json(draft) + b"\n"
    )
