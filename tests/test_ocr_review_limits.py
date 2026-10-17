"""Reviewer-discovered prompt-budget regressions through real snapshot review."""

import pytest

from app.developer import DeveloperError
from app.developer_ocr_review import CandidateOcrScope
from tests.test_independent_review import run_review
from tests.test_ocr_independent_review import _ocr_runtime, _prepared


def test_maximum_contiguous_changed_lines_fit_bounded_reviewer_request(developer_case):
    runtime, _, reviewer, raw = _ocr_runtime(developer_case)
    runtime.settings = runtime.settings.model_copy(update={"developer_diff_bytes": 131072})
    repo, request = _prepared(runtime)
    (repo / "DEVELOPER_SMOKE.txt").write_text("x\n" * 12000)
    raw["comments"][0].update(start_line=12000, end_line=12000)
    result = run_review(runtime, repo, request, lambda *_: None)
    assert "lines 12000-12000" in result["report"]["findings"][0]["message"]
    assert len(reviewer.review_requests[0].instruction) < 2000
    assert len(reviewer.calls) == 1 and not reviewer.calls[0].exists()


def test_sparse_anchor_instruction_refuses_overflow_instead_of_truncation():
    scope = CandidateOcrScope(
        base_sha="a" * 40,
        candidate_content_sha256="b" * 64,
        diff_sha256="c" * 64,
        changed_lines=(("sparse.py", frozenset(range(1, 24000, 2))),),
    )
    with pytest.raises(DeveloperError, match="ocr_review_instruction_limit"):
        scope.instruction()
