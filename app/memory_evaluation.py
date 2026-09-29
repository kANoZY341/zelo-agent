"""Deterministic baseline evaluation for v0.13 memory retrieval quality."""

from collections.abc import Iterable
from dataclasses import dataclass

from app.memory import MemoryService
from app.schemas import MemoryRetrieveRequest, MemoryScope

MEMORY_EVALUATION_SCHEMA = "zelo-memory-evaluation-v1"


@dataclass(frozen=True)
class MemoryEvaluationCase:
    """One exact-match retrieval case over explicit memory scopes."""

    name: str
    query: str
    scopes: tuple[MemoryScope, ...]
    expected_ids: frozenset[str]
    forbidden_ids: frozenset[str] = frozenset()
    limit: int = 6

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("memory evaluation case name cannot be blank")
        if not self.query.strip():
            raise ValueError("memory evaluation query cannot be blank")
        if not self.scopes:
            raise ValueError("memory evaluation requires at least one scope")
        if not 1 <= self.limit <= 20:
            raise ValueError("memory evaluation limit must be between 1 and 20")
        overlap = self.expected_ids & self.forbidden_ids
        if overlap:
            raise ValueError("expected and forbidden memory IDs must be disjoint")


@dataclass(frozen=True)
class MemoryEvaluationResult:
    name: str
    query: str
    scopes: tuple[tuple[str, str | None], ...]
    limit: int
    retrieval: str
    candidate_count: int
    actual_ids: tuple[str, ...]
    expected_ids: frozenset[str]
    forbidden_ids: frozenset[str]
    missing_expected_ids: frozenset[str]
    forbidden_ids_present: frozenset[str]
    unexpected_ids: frozenset[str]
    precision: float
    recall: float
    passed: bool


@dataclass(frozen=True)
class MemoryEvaluationReport:
    cases: tuple[MemoryEvaluationResult, ...]
    cases_total: int
    cases_passed: int
    contamination_hits: int
    unexpected_hits: int
    passed: bool


def _ratio(numerator: int, denominator: int, *, empty_value: float) -> float:
    if denominator == 0:
        return empty_value
    return numerator / denominator


def evaluate_memory_case(
    memories: MemoryService,
    case: MemoryEvaluationCase,
) -> MemoryEvaluationResult:
    response = memories.retrieve(
        MemoryRetrieveRequest(
            query=case.query,
            scopes=list(case.scopes),
            limit=case.limit,
        )
    )
    actual_ids = tuple(item.id for item in response.items)
    actual_set = frozenset(actual_ids)
    relevant = actual_set & case.expected_ids
    missing = case.expected_ids - actual_set
    forbidden = case.forbidden_ids & actual_set
    unexpected = actual_set - case.expected_ids - case.forbidden_ids
    precision = _ratio(
        len(relevant),
        len(actual_ids),
        empty_value=1.0 if not case.expected_ids else 0.0,
    )
    recall = _ratio(
        len(relevant),
        len(case.expected_ids),
        empty_value=1.0,
    )
    passed = not missing and not forbidden and not unexpected
    return MemoryEvaluationResult(
        name=case.name,
        query=response.query,
        scopes=tuple(
            (scope.kind, scope.id.strip() if scope.id is not None else None)
            for scope in case.scopes
        ),
        limit=case.limit,
        retrieval=response.retrieval,
        candidate_count=response.candidate_count,
        actual_ids=actual_ids,
        expected_ids=case.expected_ids,
        forbidden_ids=case.forbidden_ids,
        missing_expected_ids=missing,
        forbidden_ids_present=forbidden,
        unexpected_ids=unexpected,
        precision=precision,
        recall=recall,
        passed=passed,
    )


def evaluate_memory_suite(
    memories: MemoryService,
    cases: Iterable[MemoryEvaluationCase],
) -> MemoryEvaluationReport:
    case_list = tuple(cases)
    if not case_list:
        raise ValueError("memory evaluation suite cannot be empty")
    names = [case.name for case in case_list]
    if len(names) != len(set(names)):
        raise ValueError("memory evaluation case names must be unique")
    results = tuple(evaluate_memory_case(memories, case) for case in case_list)
    cases_passed = sum(result.passed for result in results)
    contamination_hits = sum(len(result.forbidden_ids_present) for result in results)
    unexpected_hits = sum(len(result.unexpected_ids) for result in results)
    return MemoryEvaluationReport(
        cases=results,
        cases_total=len(results),
        cases_passed=cases_passed,
        contamination_hits=contamination_hits,
        unexpected_hits=unexpected_hits,
        passed=cases_passed == len(results),
    )


def memory_evaluation_evidence(report: MemoryEvaluationReport) -> dict:
    """Return stable JSON-safe evidence without persisting it."""

    return {
        "schema": MEMORY_EVALUATION_SCHEMA,
        "passed": report.passed,
        "cases_total": report.cases_total,
        "cases_passed": report.cases_passed,
        "contamination_hits": report.contamination_hits,
        "unexpected_hits": report.unexpected_hits,
        "cases": [
            {
                "name": result.name,
                "query": result.query,
                "scopes": [{"kind": kind, "id": scope_id} for kind, scope_id in result.scopes],
                "limit": result.limit,
                "retrieval": result.retrieval,
                "candidate_count": result.candidate_count,
                "actual_ids": list(result.actual_ids),
                "expected_ids": sorted(result.expected_ids),
                "forbidden_ids": sorted(result.forbidden_ids),
                "missing_expected_ids": sorted(result.missing_expected_ids),
                "forbidden_ids_present": sorted(result.forbidden_ids_present),
                "unexpected_ids": sorted(result.unexpected_ids),
                "precision": result.precision,
                "recall": result.recall,
                "passed": result.passed,
            }
            for result in report.cases
        ],
    }
