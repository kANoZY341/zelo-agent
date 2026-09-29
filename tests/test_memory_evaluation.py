"""Baseline quality and contamination evaluation for v0.13 memory."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.memory_evaluation import (
    MEMORY_EVALUATION_SCHEMA,
    MemoryEvaluationCase,
    evaluate_memory_case,
    evaluate_memory_suite,
    memory_evaluation_evidence,
)
from app.policy import DomainError
from app.schemas import MemoryScope
from tests.conftest import AUTH


def _create_memory(client, *, content: str, scope=None, kind="fact", **extra) -> dict:
    body = {
        "scope": scope or {"kind": "personal"},
        "kind": kind,
        "content": content,
        "provenance": {"kind": "operator"},
        **extra,
    }
    response = client.post("/memories", headers=AUTH, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_memory_baseline_evaluation_suite_has_exact_recall_and_zero_contamination(client):
    original = _create_memory(
        client,
        content="My favorite interface color is blue.",
        kind="preference",
    )
    corrected = _create_memory(
        client,
        content="My favorite interface color is black.",
        kind="preference",
        replaces_id=original["id"],
        replacement_kind="correction",
    )
    expired = _create_memory(
        client,
        content="My favorite interface color is temporary green.",
        kind="preference",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    dessert = _create_memory(
        client,
        content="My preferred dessert is caramel.",
        kind="preference",
    )
    arabic = _create_memory(
        client,
        content="لوني المفضل الأسود.",
        kind="preference",
    )

    alpha = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Alpha", "summary": "Memory evaluation project."},
    ).json()
    beta = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Beta", "summary": "Contamination distractor."},
    ).json()
    alpha_memory = _create_memory(
        client,
        scope={"kind": "project", "id": alpha["id"]},
        kind="decision",
        content="The deployment database is SQLite.",
        provenance={"kind": "project", "ref": alpha["id"]},
    )
    beta_memory = _create_memory(
        client,
        scope={"kind": "project", "id": beta["id"]},
        kind="decision",
        content="The deployment database is PostgreSQL.",
        provenance={"kind": "project", "ref": beta["id"]},
    )
    agent_memory = _create_memory(
        client,
        scope={"kind": "agent", "id": "research"},
        kind="experience",
        content="Evidence retrieval must preserve source provenance.",
        provenance={"kind": "agent", "ref": "research"},
    )

    memories = client.app.state.memories
    all_contaminants = frozenset(
        {
            original["id"],
            expired["id"],
            dessert["id"],
            alpha_memory["id"],
            beta_memory["id"],
            agent_memory["id"],
        }
    )
    cases = (
        MemoryEvaluationCase(
            name="personal-current-preference",
            query="what is my favorite interface color?",
            scopes=(MemoryScope(kind="personal"),),
            expected_ids=frozenset({corrected["id"]}),
            forbidden_ids=all_contaminants,
        ),
        MemoryEvaluationCase(
            name="personal-unrelated-no-hit",
            query="what is my favorite telescope?",
            scopes=(MemoryScope(kind="personal"),),
            expected_ids=frozenset(),
            forbidden_ids=frozenset(
                {
                    corrected["id"],
                    dessert["id"],
                    arabic["id"],
                }
            ),
        ),
        MemoryEvaluationCase(
            name="arabic-personal-recall",
            query="ما هو لوني المفضل؟",
            scopes=(MemoryScope(kind="personal"),),
            expected_ids=frozenset({arabic["id"]}),
            forbidden_ids=frozenset({corrected["id"], dessert["id"]}),
        ),
        MemoryEvaluationCase(
            name="project-alpha-isolation",
            query="deployment database",
            scopes=(MemoryScope(kind="project", id=alpha["id"]),),
            expected_ids=frozenset({alpha_memory["id"]}),
            forbidden_ids=frozenset({beta_memory["id"], corrected["id"]}),
        ),
        MemoryEvaluationCase(
            name="agent-research-isolation",
            query="evidence retrieval provenance",
            scopes=(MemoryScope(kind="agent", id="research"),),
            expected_ids=frozenset({agent_memory["id"]}),
            forbidden_ids=frozenset({alpha_memory["id"], beta_memory["id"]}),
        ),
    )

    report = evaluate_memory_suite(memories, cases)

    assert report.passed is True
    assert report.cases_total == 5
    assert report.cases_passed == 5
    assert report.contamination_hits == 0
    assert report.unexpected_hits == 0
    assert all(result.precision == 1.0 for result in report.cases)
    assert all(result.recall == 1.0 for result in report.cases)
    assert all(result.retrieval == "bounded_lexical_v1" for result in report.cases)
    assert all(result.candidate_count >= len(result.actual_ids) for result in report.cases)

    evidence = memory_evaluation_evidence(report)
    assert evidence["schema"] == MEMORY_EVALUATION_SCHEMA
    assert evidence["passed"] is True
    first_case = evidence["cases"][0]
    assert first_case["query"] == "what is my favorite interface color?"
    assert first_case["scopes"] == [{"kind": "personal", "id": None}]
    assert first_case["limit"] == 6
    assert first_case["actual_ids"] == [corrected["id"]]
    assert first_case["expected_ids"] == [corrected["id"]]
    assert original["id"] in first_case["forbidden_ids"]
    json.dumps(evidence, ensure_ascii=False, allow_nan=False)


def test_memory_evaluation_detects_forbidden_and_unexpected_retrieval(client):
    active = _create_memory(
        client,
        content="My favorite interface color is black.",
        kind="preference",
    )
    case = MemoryEvaluationCase(
        name="intentional-contamination-sentinel",
        query="favorite interface color",
        scopes=(MemoryScope(kind="personal"),),
        expected_ids=frozenset(),
        forbidden_ids=frozenset({active["id"]}),
    )

    result = evaluate_memory_case(client.app.state.memories, case)

    assert result.passed is False
    assert result.actual_ids == (active["id"],)
    assert result.forbidden_ids_present == frozenset({active["id"]})
    assert result.unexpected_ids == frozenset()
    assert result.precision == 0.0
    assert result.recall == 1.0

    unexpected_case = MemoryEvaluationCase(
        name="intentional-unexpected-sentinel",
        query="favorite interface color",
        scopes=(MemoryScope(kind="personal"),),
        expected_ids=frozenset(),
    )
    unexpected = evaluate_memory_case(client.app.state.memories, unexpected_case)
    assert unexpected.passed is False
    assert unexpected.forbidden_ids_present == frozenset()
    assert unexpected.unexpected_ids == frozenset({active["id"]})


def test_memory_evaluation_case_requires_explicit_scope_validation(client):
    case = MemoryEvaluationCase(
        name="missing-project",
        query="deployment database",
        scopes=(
            MemoryScope(
                kind="project",
                id="00000000-0000-0000-0000-000000000099",
            ),
        ),
        expected_ids=frozenset(),
    )

    with pytest.raises(DomainError, match="Project not found"):
        evaluate_memory_case(client.app.state.memories, case)


def test_memory_evaluation_contract_rejects_ambiguous_cases(client):
    active = _create_memory(
        client,
        content="My favorite interface color is black.",
        kind="preference",
    )
    with pytest.raises(ValueError, match="disjoint"):
        MemoryEvaluationCase(
            name="ambiguous",
            query="favorite interface color",
            scopes=(MemoryScope(kind="personal"),),
            expected_ids=frozenset({active["id"]}),
            forbidden_ids=frozenset({active["id"]}),
        )

    case = MemoryEvaluationCase(
        name="duplicate",
        query="favorite interface color",
        scopes=(MemoryScope(kind="personal"),),
        expected_ids=frozenset({active["id"]}),
    )
    with pytest.raises(ValueError, match="names must be unique"):
        evaluate_memory_suite(client.app.state.memories, (case, case))
