"""Explicit OCR output is grounded in the existing independent review snapshot."""

import asyncio
import json
from uuid import uuid4

import pytest

from app.config import Settings
from app.developer import DeveloperError
from tests.test_independent_review import review_runtime, run_review


def _ocr_runtime(developer_case):
    runtime, producer, reviewer = review_runtime(developer_case)
    runtime.settings = runtime.settings.model_copy(
        update={"developer_review_format": "open_code_review"}
    )
    raw = {
        "schema_version": "zelo-ocr-line-review-input-v1",
        "summary": "One grounded observation",
        "comments": [
            {
                "path": "DEVELOPER_SMOKE.txt",
                "start_line": 1,
                "end_line": 1,
                "severity": "low",
                "category": "documentation",
                "content": "Document the fixture purpose.",
            }
        ],
    }
    original = reviewer.run

    async def emit_ocr(request, repo):
        result = await original(request, repo)
        return result.model_copy(update={"summary": json.dumps(raw)})

    reviewer.run = emit_ocr
    return runtime, producer, reviewer, raw


def _prepared(runtime):
    request = runtime.request("Add bounded documentation", "zelo_core", 60)
    repo = runtime.path(str(uuid4()))
    repo.parent.mkdir(parents=True)
    asyncio.run(runtime.prepare(repo, request))
    (repo / "DEVELOPER_SMOKE.txt").write_text("Fixture documentation\n")
    return repo, request


def test_ocr_mode_is_explicit_and_bound_to_the_approved_request(developer_case):
    assert "developer_review_format" in Settings.model_fields
    runtime, _, _, _ = _ocr_runtime(developer_case)
    request = runtime.request("Review", "zelo_core", 60)
    assert request.reviewer.report_format == "open_code_review"
    assert request.model_dump(mode="json")["reviewer"]["report_format"] == "open_code_review"
    runtime.settings = runtime.settings.model_copy(update={"developer_review_format": "zelo"})
    assert not runtime.compatible(request, 60)


def test_ocr_findings_flow_through_existing_frozen_review_and_report(developer_case):
    runtime, _producer, reviewer, _raw = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)
    events = []
    result = run_review(runtime, repo, request, lambda name, data: events.append((name, data)))
    assert result["report"]["findings"][0]["path"] == "DEVELOPER_SMOKE.txt"
    assert result["report"]["findings"][0]["message"].startswith("[documentation, lines 1-1]")
    evidence = result["ocr_evidence"]
    assert evidence["origin"] == "isolated_candidate_snapshot"
    assert evidence["candidate_content_sha256"] == result["candidate_content_sha256"]
    assert evidence["line_evidence"]["base_sha"] == request.base_sha
    assert evidence["line_evidence"]["head_sha"] == request.base_sha
    assert not evidence["line_evidence"]["approval_authority"]
    assert len(reviewer.calls) == 1 and reviewer.calls[0] != repo
    assert not reviewer.calls[0].exists()
    assert "zelo-ocr-line-review-input-v1" in reviewer.review_requests[0].instruction
    assert [name for name, _ in events] == [
        "reviewer_capacity_claimed",
        "review_started",
        "review_completed",
        "reviewer_capacity_released",
    ]


@pytest.mark.parametrize("field,value", [("path", "test_example.py"), ("start_line", 2)])
def test_model_cannot_authorize_unmodified_file_or_line(developer_case, field, value):
    runtime, _, reviewer, raw = _ocr_runtime(developer_case)
    raw["comments"][0][field] = value
    if field == "start_line":
        raw["comments"][0]["end_line"] = value
    repo, request = _prepared(runtime)
    with pytest.raises(DeveloperError, match="ocr_review_invalid_or_untrusted"):
        run_review(runtime, repo, request, lambda *_: None)
    assert len(reviewer.calls) == 1
    assert not reviewer.calls[0].exists()


def test_full_developer_run_returns_grounded_ocr_without_source_promotion(developer_case):
    runtime, producer, reviewer, _ = _ocr_runtime(developer_case)
    request = runtime.request("Add a fixture note", "zelo_core", 60)
    events = []
    result = asyncio.run(
        runtime.run(
            str(uuid4()),
            str(uuid4()),
            request,
            lambda name, details: events.append((name, details)),
        )
    )
    assert result["error"] is None, result
    assert result["validation_passed"] is True
    review = result["independent_review"]
    assert review["report"]["findings"][0]["path"] == "DEVELOPER_SMOKE.txt"
    assert review["ocr_evidence"]["candidate_content_sha256"] == result["candidate_content_sha256"]
    assert len(producer.calls) == len(reviewer.calls) == 1
    assert producer.calls[0] != reviewer.calls[0]
    assert not reviewer.calls[0].exists()
    assert not (runtime.source_repository / "DEVELOPER_SMOKE.txt").exists()
    assert "review_completed" in [name for name, _ in events]


def test_default_binding_and_nested_request_keep_legacy_wire_form(developer_case):
    from app.developer_contracts import DeveloperRequest, ReviewerBinding

    runtime, _, _ = review_runtime(developer_case)
    request = runtime.request("Legacy review", "zelo_core", 60)
    assert request.reviewer.report_format == "zelo"
    assert "report_format" not in request.reviewer.model_dump(mode="json")
    assert "report_format" not in json.loads(request.model_dump_json())["reviewer"]
    assert DeveloperRequest.model_validate(request.model_dump(mode="json")) == request
    serialized = request.reviewer.model_dump(mode="json")
    assert ReviewerBinding.model_validate(serialized) == request.reviewer
    runtime.settings = runtime.settings.model_copy(
        update={"developer_review_format": "open_code_review"}
    )
    assert not runtime.compatible(request, 60)


def test_explicit_ocr_requires_an_independent_worker(developer_case):
    developer_case.settings = developer_case.settings.model_copy(
        update={
            "developer_review_format": "open_code_review",
        }
    )
    with pytest.raises(DeveloperError, match="ocr_review_requires_independent_worker"):
        developer_case.request("Must not silently skip review", "zelo_core", 60)
    assert not developer_case.worker.calls


@pytest.mark.parametrize("when", ["before", "during"])
def test_withdrawn_ocr_selection_does_not_publish_a_report(developer_case, when):
    runtime, _, reviewer, _ = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)

    async def disable(_request, _repo):
        runtime.settings = runtime.settings.model_copy(update={"developer_review_format": "zelo"})

    if when == "before":
        asyncio.run(disable(None, None))
    else:
        reviewer.action = disable
    events = []
    with pytest.raises(DeveloperError, match="reviewer_configuration_changed"):
        run_review(runtime, repo, request, lambda name, data: events.append((name, data)))
    assert "review_completed" not in [name for name, _ in events]
    assert len(reviewer.calls) == (0 if when == "before" else 1)
    assert all(not p.exists() for p in reviewer.calls)


def test_mutating_ocr_reviewer_still_fails_the_existing_snapshot_boundary(developer_case):
    runtime, _, reviewer, _ = _ocr_runtime(developer_case)
    reviewer.mutate = True
    repo, request = _prepared(runtime)
    with pytest.raises(DeveloperError, match="reviewer_boundary_violation"):
        run_review(runtime, repo, request, lambda *_: None)
    assert not reviewer.calls[0].exists()
    assert not (repo / "REVIEWER_MUTATION.txt").exists()


@pytest.mark.parametrize(
    "raw",
    [
        {
            "schema_version": "zelo-ocr-line-review-input-v1",
            "summary": "no",
            "comments": [],
            "auto_fix": True,
        },
        {
            "schema_version": "zelo-ocr-line-review-input-v1",
            "summary": "no",
            "comments": [],
            "approval": True,
        },
        {"schema_version": "wrong", "summary": "no", "comments": []},
    ],
)
def test_no_auto_fix_or_approval_fields_from_reviewer(developer_case, raw):
    runtime, _, reviewer, response = _ocr_runtime(developer_case)
    response.clear()
    response.update(raw)
    repo, request = _prepared(runtime)
    with pytest.raises(DeveloperError, match="ocr_review_invalid_or_untrusted"):
        run_review(runtime, repo, request, lambda *_: None)
    assert not reviewer.calls[0].exists()


def test_empty_findings_are_valid_evidence_not_approval(developer_case):
    runtime, _, _, raw = _ocr_runtime(developer_case)
    raw["comments"] = []
    repo, request = _prepared(runtime)
    result = run_review(runtime, repo, request, lambda *_: None)
    assert result["report"]["findings"] == []
    assert not result["ocr_evidence"]["line_evidence"]["approval_authority"]
    assert not result["ocr_evidence"]["line_evidence"]["comment_publication_authority"]


def test_modified_tracked_file_anchors_only_the_changed_lines(developer_case):
    runtime, _, _, raw = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)
    (repo / "test_example.py").write_text("def test_example():\n    assert 1 == 1\n")
    raw["comments"][0].update(path="test_example.py", start_line=2, end_line=2)
    result = run_review(runtime, repo, request, lambda *_: None)
    assert result["report"]["findings"][0]["path"] == "test_example.py"
    raw["comments"][0].update(start_line=1, end_line=1)
    with pytest.raises(DeveloperError, match="ocr_review_invalid_or_untrusted"):
        run_review(runtime, repo, request, lambda *_: None)


def test_ignored_candidate_bytes_never_supply_review_anchors(developer_case):
    runtime, _, _, raw = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "private.txt").write_text("never anchor this ignored fixture")
    raw["comments"][0].update(path="__pycache__/private.txt", start_line=1, end_line=1)
    with pytest.raises(DeveloperError, match="ocr_review_invalid_or_untrusted"):
        run_review(runtime, repo, request, lambda *_: None)


@pytest.mark.parametrize("change", ["binary", "delete", "too_large"])
def test_unsupported_candidate_is_refused_before_calling_reviewer(developer_case, change):
    runtime, _, reviewer, raw = _ocr_runtime(developer_case)
    raw["comments"] = []
    repo, request = _prepared(runtime)
    if change == "binary":
        (repo / "DEVELOPER_SMOKE.txt").write_bytes(b"\x00unreviewable\x00")
    elif change == "delete":
        (repo / "test_example.py").unlink()  # Only this test's disposable source copy.
    else:
        (repo / "DEVELOPER_SMOKE.txt").write_text("x" * (request.limits.diff_bytes + 1))
    with pytest.raises(DeveloperError):
        run_review(runtime, repo, request, lambda *_: None)
    assert not reviewer.calls
    assert list(runtime.root().iterdir()) == [repo.parent]


def test_pinned_ocr_source_failure_refuses_before_model(developer_case, monkeypatch):
    from app.community_sources import CommunitySourceRefusal

    runtime, _, reviewer, _ = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)

    def unavailable():
        raise CommunitySourceRefusal("synthetic_missing_source")

    monkeypatch.setattr("app.developer_ocr_review.verify_community_sources", unavailable)
    with pytest.raises(DeveloperError, match="ocr_review_source_unavailable"):
        run_review(runtime, repo, request, lambda *_: None)
    assert not reviewer.calls
    assert list(runtime.root().iterdir()) == [repo.parent]


def test_output_format_cannot_be_selected_by_raw_model_json(developer_case):
    runtime, _, _, _ = _ocr_runtime(developer_case)
    runtime.settings = runtime.settings.model_copy(update={"developer_review_format": "zelo"})
    repo, request = _prepared(runtime)
    with pytest.raises(DeveloperError, match="reviewer_report_invalid"):
        run_review(runtime, repo, request, lambda *_: None)


def test_scope_preflight_changes_cannot_accept_a_stale_snapshot(developer_case):
    from tests.developer_helpers import fixture_commit

    runtime, _, reviewer, _ = _ocr_runtime(developer_case)
    repo, request = _prepared(runtime)
    prepared = runtime._create_review_snapshot

    async def changed_snapshot(*args, **kwargs):
        snapshot = await prepared(*args, **kwargs)
        await asyncio.to_thread(fixture_commit, snapshot)
        return snapshot

    runtime._create_review_snapshot = changed_snapshot
    with pytest.raises(DeveloperError, match="ocr_review_snapshot_head_changed"):
        run_review(runtime, repo, request, lambda *_: None)
    assert not reviewer.calls
    assert list(runtime.root().iterdir()) == [repo.parent]
