"""OCR fixed regression scoring distinguishes anchors, false alarms and refusals."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.community_review_fixed_eval import (
    OcrCalibrationResult,
    OcrCalibrationSubmission,
    evaluate_ocr_fixed_cases,
    ocr_fixed_suite,
)
from app.community_sources import CommunitySourceRefusal
from app.models import AgentInstance, Approval, AuditEvent, MemoryRecord, Task
from tests.conftest import AUTH

BASE = "/integration-candidates/community/open-code-review/calibration"


def _raw(comments=(), summary="Synthetic review of only demonstrated issues"):
    return json.dumps(
        {
            "schema_version": "zelo-ocr-line-review-input-v1",
            "summary": summary,
            "comments": list(comments),
        },
        separators=(",", ":"),
    )


def _finding(path, line, *, category, severity, content):
    return {
        "path": path,
        "start_line": line,
        "end_line": line,
        "category": category,
        "severity": severity,
        "content": content,
    }


SECURITY = _finding(
    "src/handlers.py",
    5,
    category="security",
    severity="high",
    content="The user-controlled path is read without an authorization or containment gate.",
)
PAGINATION = _finding(
    "src/pages.py",
    4,
    category="bug",
    severity="medium",
    content="The extra item exceeds the documented maximum item count.",
)
FALSE_ALARM = _finding(
    "src/handlers.py",
    5,
    category="security",
    severity="high",
    content="Claim arbitrary file access despite verified server-owned allowlist.",
)


def _body(*, answers=None, label="manual_ocr_reference", suite=None):
    suite = suite or ocr_fixed_suite()
    cases = suite.cases
    if answers is None:
        answers = (_raw((SECURITY,)), _raw((PAGINATION,)), _raw(), _raw())
    return {
        "candidate_label": label,
        "expected_suite_sha256": suite.suite_sha256,
        "answers": [
            {"case_id": case.case_id, "raw_review_json": answer}
            for case, answer in zip(cases, answers, strict=True)
        ],
    }


def _evaluate(client, body):
    return client.post(BASE + "/evaluate", headers=AUTH, json=body)


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (Task, Approval, AuditEvent, AgentInstance, MemoryRecord)
        )


def test_fixed_suite_is_source_pinned_inert_and_deterministic(client):
    response = client.get(BASE + "/cases", headers=AUTH)
    assert response.status_code == 200, response.text
    record = response.json()
    assert record == json.loads(ocr_fixed_suite().model_dump_json())
    assert record["source_commit"] == "fabbdb296b0d97e2140ada8d54ca7d6d6d1d7ad4"
    assert len(record["cases"]) == 4
    assert len(set(x["case_id"] for x in record["cases"])) == 4
    assert record["synthetic_truth_only"] is True
    assert record["genuine_git_revision_proof"] is False
    assert record["actual_model_execution_observed"] is False
    assert record["reviewer_worker_registered"] is False
    assert record["authority_granted"] is False
    assert all(case["hidden_expected_findings"] is True for case in record["cases"])
    assert all("expected" not in case for case in record["cases"])


def test_exact_synthetic_reference_scores_without_mutations(client, database):
    before = _counts(database)
    result = _evaluate(client, _body())
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["matched_finding_count"] == 2
    assert body["unexpected_finding_count"] == 0
    assert body["missed_finding_count"] == 0
    assert body["invalid_review_count"] == 0
    assert body["clean_negative_count"] == 2
    assert all(x["adapter_status"] == "accepted" for x in body["cases"])
    assert body["fixture_only"] is True
    assert body["genuine_git_revision_proof"] is False
    assert body["independent_semantic_quality_proven"] is False
    assert body["live_model_invoked"] is False
    assert body["reviewer_worker_registered"] is False
    assert body["approval_or_merge_authority"] is False
    assert body["tool_or_space_grants"] == []
    assert OcrCalibrationResult.model_validate_json(json.dumps(body), strict=True)
    assert result.json() == _evaluate(client, _body()).json()
    assert _counts(database) == before


def test_false_alarm_and_unsafe_miss_are_separate_scored_failures(client):
    answers = (
        _raw(),
        _raw((PAGINATION,)),
        _raw((FALSE_ALARM,)),
        _raw(),
    )
    result = _evaluate(client, _body(answers=answers))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["matched_finding_count"] == 1
    assert data["unexpected_finding_count"] == 1
    assert data["missed_finding_count"] == 1
    assert data["invalid_review_count"] == 0
    assert data["clean_negative_count"] == 1
    assert data["cases"][0]["missed_findings"] == 1
    assert data["cases"][2]["unexpected_findings"] == 1


def test_wrong_severity_is_not_a_true_positive(client):
    inaccurate = {**SECURITY, "severity": "critical"}
    result = _evaluate(
        client, _body(answers=(_raw((inaccurate,)), _raw((PAGINATION,)), _raw(), _raw()))
    )
    assert result.status_code == 200
    data = result.json()
    assert data["matched_finding_count"] == 1
    assert data["unexpected_finding_count"] == 1
    assert data["missed_finding_count"] == 1


def test_duplicate_claims_with_distinct_text_do_not_mint_extra_true_positives(client):
    repeated = {**SECURITY, "content": SECURITY["content"] + " Repeated claim."}
    answers = (_raw((SECURITY, repeated)), _raw((PAGINATION,)), _raw(), _raw())
    result = _evaluate(client, _body(answers=answers))
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["matched_finding_count"] == 2
    assert report["unexpected_finding_count"] == 1
    assert report["missed_finding_count"] == 0
    assert report["invalid_review_count"] == 0


def test_invalid_untrusted_review_is_not_silently_counted_as_a_clean_negative(client):
    off_diff = {**SECURITY, "start_line": 44, "end_line": 44}
    invalid = _raw((off_diff,))
    answers = (_raw((SECURITY,)), _raw((PAGINATION,)), invalid, _raw())
    data = _evaluate(client, _body(answers=answers)).json()
    assert data["invalid_review_count"] == 1
    assert data["clean_negative_count"] == 1
    assert data["cases"][2]["adapter_status"] == "invalid"
    duplicate_key = (
        '{"schema_version":"zelo-ocr-line-review-input-v1",'
        '"summary":"foo","summary":"bar","comments":[]}'
    )
    data = _evaluate(
        client, _body(answers=(_raw((SECURITY,)), _raw((PAGINATION,)), duplicate_key, _raw()))
    ).json()
    assert data["invalid_review_count"] == 1
    assert data["clean_negative_count"] == 1


def test_refuse_stale_suite_reordered_cases_and_shape_extensions(client):
    stale = _body()
    stale["expected_suite_sha256"] = "0" * 64
    result = _evaluate(client, stale)
    assert result.status_code == 409
    assert result.json() == {"detail": "ocr_calibration_suite_stale"}
    wrong_order = _body()
    wrong_order["answers"].reverse()
    assert _evaluate(client, wrong_order).status_code == 422
    extra = _body()
    extra["approve_pr"] = True
    assert _evaluate(client, extra).status_code == 422
    wrong = _body()
    wrong["answers"][0]["raw_review_json"] = ""
    assert _evaluate(client, wrong).status_code == 422


def test_http_auth_duplicate_fields_bad_content_type_and_size(client, database):
    before = _counts(database)
    assert client.get(BASE + "/cases", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert (
        client.post(
            BASE + "/evaluate", headers={"Authorization": "Bearer bad"}, json=_body()
        ).status_code
        == 401
    )
    assert (
        client.post(
            BASE + "/evaluate",
            headers={**AUTH, "Content-Type": "text/plain"},
            content=json.dumps(_body()).encode(),
        ).status_code
        == 415
    )
    assert (
        client.post(
            BASE + "/evaluate",
            headers={**AUTH, "Content-Type": "application/json"},
            content=b'{"candidate_label":"a","candidate_label":"b"}',
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/evaluate",
            headers={**AUTH, "Content-Type": "application/json"},
            content=b"z" * 32769,
        ).status_code
        == 413
    )
    assert _counts(database) == before


def test_untrusted_pinned_source_refusal_is_availability_not_false_green(client):
    prepared = _body()
    with patch(
        "app.community_review_fixed_eval.verify_community_sources",
        side_effect=CommunitySourceRefusal("source_bytes_changed"),
    ):
        get = client.get(BASE + "/cases", headers=AUTH)
        post = _evaluate(client, prepared)
    assert get.status_code == 503
    assert post.status_code == 503
    assert "source_bytes_changed" not in get.text + post.text


def test_result_digest_and_authority_flags_are_tamper_evident(client):
    body = _evaluate(client, _body()).json()
    tampered = json.loads(json.dumps(body))
    tampered["matched_finding_count"] = 3
    with pytest.raises(ValidationError):
        OcrCalibrationResult.model_validate_json(json.dumps(tampered), strict=True)
    tampered = json.loads(json.dumps(body))
    tampered["approval_or_merge_authority"] = True
    with pytest.raises(ValidationError):
        OcrCalibrationResult.model_validate_json(json.dumps(tampered), strict=True)
    tampered = json.loads(json.dumps(body))
    tampered["cases"][0]["unexpected_findings"] = 1
    with pytest.raises(ValidationError):
        OcrCalibrationResult.model_validate_json(json.dumps(tampered), strict=True)


def test_no_model_execution_or_tool_dispatch_from_direct_scoring():
    submission = OcrCalibrationSubmission.model_validate_json(json.dumps(_body()), strict=True)
    record = evaluate_ocr_fixed_cases(submission)
    assert record.matched_finding_count == 2
    assert not record.live_model_invoked
    assert record.tool_or_space_grants == ()
