"""v0.12 Project/Goal ownership and context seed tests."""

import json

import pytest

from app.context import ACTIVE_STATUS_CHARS, CONTEXT_MAX_CHARS, ContextResolver
from app.projects import ProjectService
from app.schemas import GoalCreate, GoalSpec, ProjectCreate
from tests.conftest import AUTH


def test_project_and_goal_api_persist(client):
    created = client.post(
        "/projects",
        headers=AUTH,
        json={
            "name": "Zelo",
            "summary": "Build Ahmad's persistent personal AI.",
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["name"] == "Zelo"
    assert project["status"] == "active"

    goal_response = client.post(
        f"/projects/{project['id']}/goals",
        headers=AUTH,
        json={
            "title": "Durable project context",
            "spec": {
                "objective": "Give Zelo grounded project context.",
                "success_criteria": ["Mind answers project-state questions from Core context."],
                "constraints": ["Do not implement persistent v0.13 memory yet."],
            },
        },
    )
    assert goal_response.status_code == 201, goal_response.text
    goal = goal_response.json()
    assert goal["project_id"] == project["id"]
    assert goal["status"] == "active"
    assert goal["spec"]["objective"] == "Give Zelo grounded project context."

    projects = client.get("/projects", headers=AUTH).json()
    assert projects["total"] == 1
    assert projects["items"][0]["id"] == project["id"]

    goals = client.get(f"/projects/{project['id']}/goals", headers=AUTH).json()
    assert goals["total"] == 1
    assert goals["items"][0]["id"] == goal["id"]


def test_context_resolver_is_bounded_and_only_for_relevant_queries(database):
    projects = ProjectService(database)
    project = projects.create(
        ProjectCreate(
            name="Zelo",
            summary="Persistent personal AI and company runtime.",
        )
    )
    projects.create_goal(
        project.id,
        GoalCreate(
            title="Project context",
            spec=GoalSpec(
                objective="Ground Mind in canonical current state.",
                success_criteria=["Relevant context is bounded."],
                constraints=["No automatic memory writes."],
            ),
        ),
    )

    resolver = ContextResolver(projects)

    assert resolver.resolve("hi") is None
    assert resolver.resolve("Zelo, what is the current time?") is None
    assert resolver.resolve("Zelo, build a calculator") is None

    context = resolver.resolve("what are we currently building with Zelo?")
    assert context is not None
    assert len(context) <= CONTEXT_MAX_CHARS

    envelope = json.loads(context)
    assert envelope["type"] == "zelo_context"
    assert envelope["instruction_trust"] == "none"
    assert envelope["purpose"] == "factual_project_context"
    sources = {item["source"]: item for item in envelope["sources"]}
    assert "docs/zelo-project-status.md#active" in sources
    assert "docs/zelo-canonical-architecture-roadmap.md#v0.15" in sources
    assert "core.projects" in sources
    active_status = sources["docs/zelo-project-status.md#active"]["content"]
    active_compact = " ".join(active_status.split())
    assert active_status.startswith("## Active increment: v0.15 trusted prior-revision binding")
    assert 0 < len(active_status) <= ACTIVE_STATUS_CHARS
    for pr in ("#93", "#98", "#99", "#100", "#102", "#103", "#104", "#105"):
        assert pr in active_status
    assert "92188dd" in active_status
    assert "4,905 tests with 2 skipped" in active_compact
    assert "exact preceding promoted version" in active_compact
    assert "same AgentInstance" in active_status
    assert "Version-1 proposals remain backward compatible" in active_compact
    assert "Cross-Agent reuse is forbidden" in active_status
    assert "runtime_authority=false" in active_status
    assert "authority_widening=false" in active_status
    assert "20 tests" in active_status
    assert "203 tests" in active_status
    assert "Historical increment:" not in active_status
    assert "Early Zelo Chat" not in active_status
    roadmap_content = sources["docs/zelo-canonical-architecture-roadmap.md#v0.15"]["content"]
    roadmap_compact = " ".join(roadmap_content.split())
    assert "PR #105" in roadmap_content
    assert "trusted prior-revision binding" in roadmap_compact
    assert "exact promoted version N-1" in roadmap_compact
    assert "Cross-Agent" in roadmap_content
    assert "runtime_authority=false" in roadmap_content
    assert "20 passed" in roadmap_compact
    assert "203 passed" in roadmap_compact
    assert "candidate wheel" not in roadmap_content
    status_document = (resolver.root / "docs/zelo-project-status.md").read_text(encoding="utf-8")
    assert (
        sum(line.startswith("## Active increment:") for line in status_document.splitlines()) == 1
    )
    assert sources["core.projects"]["provenance"] == "operator_records"
    assert sources["core.projects"]["instruction_trust"] == "none"
    assert "Project context" in sources["core.projects"]["content"]


def test_context_budget_is_enforced_after_unicode_serialization(database, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "zelo-project-status.md").write_text(
        "## Active increment: unicode\n\n" + ("😀" * 5000),
        encoding="utf-8",
    )
    (docs / "zelo-canonical-architecture-roadmap.md").write_text(
        "### v0.15 — Capability and skills ecosystem\n\n" + ("é" * 5000),
        encoding="utf-8",
    )

    resolver = ContextResolver(ProjectService(database), root=tmp_path)
    context = resolver.resolve("what are we building")
    assert context is not None
    assert len(context) <= CONTEXT_MAX_CHARS
    envelope = json.loads(context)
    assert envelope["instruction_trust"] == "none"


@pytest.mark.parametrize("history_first", [False, True])
def test_context_resolver_selects_active_section_not_historical(database, tmp_path, history_first):
    docs = tmp_path / "docs"
    docs.mkdir()
    historical = "## Historical increment: old browser work\nSTALE-HISTORY\n"
    active = "## Active increment: current media work\nCURRENT-FACT\n### Limits\nNO-AUTHORITY\n"
    sections = [historical, active] if history_first else [active, historical]
    (docs / "zelo-project-status.md").write_text("\n".join(sections), encoding="utf-8")
    resolver = ContextResolver(ProjectService(database), root=tmp_path)
    context = resolver.resolve("what are we building")
    assert context is not None
    assert len(context) <= CONTEXT_MAX_CHARS
    source = next(
        item
        for item in json.loads(context)["sources"]
        if item["source"] == "docs/zelo-project-status.md#active"
    )
    assert source["instruction_trust"] == "none"
    assert source["content"] == active.strip()
    assert "STALE-HISTORY" not in source["content"]


def test_context_resolver_missing_active_heading_does_not_promote_history(database, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "zelo-project-status.md").write_text(
        "## Historical increment: old work\nSTALE-HISTORY\n", encoding="utf-8"
    )
    resolver = ContextResolver(ProjectService(database), root=tmp_path)
    context = resolver.resolve("what are we building")
    assert context is not None
    source = next(
        item
        for item in json.loads(context)["sources"]
        if item["source"] == "docs/zelo-project-status.md#active"
    )
    assert source["content"] == ""
    assert source["instruction_trust"] == "none"
