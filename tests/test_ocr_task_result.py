"""Grounded reviewer results must survive the actual Task result contract."""

import asyncio

import pytest
from pydantic import ValidationError

from app.developer import DeveloperError
from app.policy import State
from app.runner import Runner
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from tests.developer_helpers import fixture_commit
from tests.test_independent_review import run_review
from tests.test_ocr_independent_review import _ocr_runtime, _prepared


def test_approved_developer_task_persists_grounded_result_through_runner(developer_case, database):
    runtime, _, reviewer, _ = _ocr_runtime(developer_case)
    tasks = TaskService(database, runtime.settings, developer_runtime=runtime)
    submitted, created = tasks.submit(
        DeveloperRunInput(
            kind="developer_run",
            payload={"instruction": "Add a harmless fixture note"},
            timeout_seconds=60,
        ),
        "grounded-review-task",
    )
    assert created and submitted.status == State.AWAITING_APPROVAL
    tasks.decide(submitted.approvals[0].id, "approve")
    claimed = tasks.claim()
    assert claimed is not None
    asyncio.run(Runner(tasks, developer_runtime=runtime)._run(claimed))
    finished = tasks.get(submitted.id)
    assert finished.status == State.SUCCEEDED, finished.error
    evidence = finished.result["independent_review"]["ocr_evidence"]
    assert evidence["candidate_content_sha256"] == finished.result["candidate_content_sha256"]
    assert evidence["line_evidence"]["comments"][0]["path"] == "DEVELOPER_SMOKE.txt"
    reopened = TaskService(database, runtime.settings, developer_runtime=runtime).get(finished.id)
    assert reopened.result == finished.result
    assert len(reviewer.calls) == 1 and not reviewer.calls[0].exists()


def test_source_diff_attributes_cannot_reclassify_binary_as_safe_reviewable_text(developer_case):
    runtime, _, reviewer, raw = _ocr_runtime(developer_case)
    (runtime.source_repository / ".gitattributes").write_text("*.bin diff\n")
    (runtime.source_repository / "sample.bin").write_bytes(b"original\x00binary\n")
    fixture_commit(runtime.source_repository)
    repo, request = _prepared(runtime)
    (repo / "sample.bin").write_bytes(b"changed\x00binary\n")
    raw["comments"] = []
    with pytest.raises(DeveloperError, match="ocr_review_scope_unavailable"):
        run_review(runtime, repo, request, lambda *_: None)
    assert not reviewer.calls


def test_review_result_roundtrip_keeps_exact_validated_provenance(developer_case):
    from app.schemas import IndependentReviewResult

    runtime, _, _, _ = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)
    result = run_review(runtime, repo, request, lambda *_: None)
    decoded = IndependentReviewResult.model_validate(result)
    serialized = decoded.model_dump(mode="json")
    assert serialized["ocr_evidence"] == result["ocr_evidence"]
    assert serialized["report"] == result["report"]
    assert IndependentReviewResult.model_validate_json(decoded.model_dump_json()) == decoded
    for field in ("candidate_content_sha256", "report"):
        import copy

        forged = copy.deepcopy(result)
        if field == "report":
            forged["report"]["findings"] = []
        else:
            forged[field] = "f" * 64
        with pytest.raises(ValidationError):
            IndependentReviewResult.model_validate(forged)
