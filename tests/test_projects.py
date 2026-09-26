"""v0.12 Project/Goal ownership and context seed tests."""

import json

from app.context import CONTEXT_MAX_CHARS, ContextResolver
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
    assert "docs/zelo-canonical-architecture-roadmap.md#v0.12" in sources
    assert "core.projects" in sources
    assert (
        "Active increment: v0.12 Bounded scheduler and Mind wake cycles"
        in sources["docs/zelo-project-status.md#active"]["content"]
    )
    assert "Early Zelo Chat" not in sources["docs/zelo-project-status.md#active"]["content"]
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
        "### v0.12 — Durable goals and first useful campaign\n\n" + ("é" * 5000),
        encoding="utf-8",
    )

    resolver = ContextResolver(ProjectService(database), root=tmp_path)
    context = resolver.resolve("what are we building")
    assert context is not None
    assert len(context) <= CONTEXT_MAX_CHARS
    envelope = json.loads(context)
    assert envelope["instruction_trust"] == "none"
