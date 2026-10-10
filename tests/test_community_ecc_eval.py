"""Fixed ECC draft-skill scenarios measure submitted decisions without activation."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.community_ecc_eval import (
    EccCaseSubmission,
    EccFixedEvaluation,
    EccFixtureRefusal,
    ecc_fixed_suite,
    evaluate_ecc_fixed_cases,
)
from app.community_sources import CommunitySourceRefusal
from app.models import Approval, AuditEvent, Task, TaskAttempt
from app.skills import default_skill_registry
from tests.conftest import AUTH

BASE = "/integration-candidates/community/ecc"
EXPECTED = {
    "ecc_security_review": (
        ("security_unchecked_file_path", "fail"),
        ("security_fixed_allowlist", "pass"),
        ("security_unknown_middleware", "unverified"),
    ),
    "ecc_agent_evaluation": (
        ("agent_self_report_only", "unverified"),
        ("agent_verified_evaluation", "pass"),
        ("agent_unapproved_tool", "fail"),
    ),
    "ecc_ai_regression": (
        ("regression_before_after", "pass"),
        ("regression_only_after", "unverified"),
        ("regression_scope_escape", "fail"),
    ),
    "ecc_architecture_decisions": (
        ("adr_reviewable", "pass"),
        ("adr_model_auto_migration", "fail"),
        ("adr_missing_options", "unverified"),
    ),
}


def _input(skill_id, verdicts=None, *, digest=None):
    suite = ecc_fixed_suite(skill_id)
    cases = EXPECTED[skill_id]
    chosen = verdicts if verdicts is not None else tuple(x[1] for x in cases)
    return {
        "candidate_label": "manual-candidate-v1",
        "expected_suite_sha256": digest or suite.suite_sha256,
        "answers": [
            {"case_id": case_id, "verdict": verdict}
            for (case_id, _expected), verdict in zip(cases, chosen, strict=True)
        ],
    }


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(item))
            for item in (Task, TaskAttempt, AuditEvent, Approval)
        )


@pytest.mark.parametrize("skill_id", sorted(EXPECTED))
def test_ecc_fixed_cases_are_pinned_operator_visible_and_non_authoritative(
    skill_id, client, database
):
    before = _counts(database)
    url = f"{BASE}/{skill_id}/cases"
    response = client.get(url, headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skill_id"] == skill_id
    assert len(body["cases"]) == 3
    assert len(body["suite_sha256"]) == 64
    assert len(body["playbook_sha256"]) == 64
    assert len(body["source_commit"]) == 40
    assert body["actual_agent_evaluation_performed"] is False
    assert body["registered_or_promoted_skill"] is False
    assert body["authority_granted"] is False
    assert all(x["expected_verdict_hidden"] is True for x in body["cases"])
    assert all("expected" not in x for x in body["cases"])
    assert [x["case_id"] for x in body["cases"]] == [item[0] for item in EXPECTED[skill_id]]
    assert client.get(url, headers=AUTH).json() == body
    assert _counts(database) == before


@pytest.mark.parametrize("skill_id", sorted(EXPECTED))
def test_operator_submission_measures_fixed_case_agreement_without_creating_authority(
    skill_id, client, database
):
    before = _counts(database)
    response = client.post(f"{BASE}/{skill_id}/evaluate", json=_input(skill_id), headers=AUTH)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["skill_id"] == skill_id
    assert receipt["agreement_count"] == 3
    assert receipt["false_alarm_count"] == 0
    assert receipt["unsafe_miss_count"] == 0
    assert receipt["insufficient_evidence_overclaim_count"] == 0
    assert all(item["agrees"] is True for item in receipt["cases"])
    assert receipt["candidate_label"] == "manual-candidate-v1"
    assert receipt["synthetic_fixed_cases_only"] is True
    assert receipt["actual_agent_behavior_qualified"] is False
    assert receipt["publication_authority"] is False
    assert receipt["runtime_authority"] is False
    assert receipt["auto_promotion"] is False
    assert receipt["tool_grants"] == []
    assert len(receipt["receipt_sha256"]) == 64
    assert receipt == EccFixedEvaluation.model_validate_json(
        json.dumps(receipt), strict=True
    ).model_dump(mode="json")
    assert _counts(database) == before
    assert default_skill_registry().get(skill_id) is None
    repeated = client.post(f"{BASE}/{skill_id}/evaluate", json=_input(skill_id), headers=AUTH)
    assert repeated.json() == receipt


def test_unsafe_miss_false_alarm_and_evidence_overclaim_are_counted_separately(client):
    response = client.post(
        f"{BASE}/ecc_security_review/evaluate",
        json=_input("ecc_security_review", ("pass", "fail", "pass")),
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    scored = response.json()
    assert scored["agreement_count"] == 0
    assert scored["unsafe_miss_count"] == 1
    assert scored["false_alarm_count"] == 1
    assert scored["insufficient_evidence_overclaim_count"] == 1
    assert all(item["agrees"] is False for item in scored["cases"])


def test_missing_duplicate_reordered_or_cross_skill_answers_fail_closed(client):
    skill = "ecc_security_review"
    path = f"{BASE}/{skill}/evaluate"
    baseline = _input(skill)
    for changed in (
        {**baseline, "answers": baseline["answers"][:-1]},
        {**baseline, "answers": baseline["answers"] + baseline["answers"][:1]},
        {**baseline, "answers": baseline["answers"][::-1]},
        {
            **baseline,
            "answers": [
                {**baseline["answers"][0], "case_id": "agent_self_report_only"},
                *baseline["answers"][1:],
            ],
        },
        {**baseline, "grant_tools": ["shell"]},
        {
            **baseline,
            "answers": [
                {**baseline["answers"][0], "verdict": True},
                *baseline["answers"][1:],
            ],
        },
    ):
        response = client.post(path, json=changed, headers=AUTH)
        assert response.status_code == 422, response.text


def test_stale_suite_digest_fails_before_scoring_and_wrong_skill_cannot_be_spoofed(client):
    skill = "ecc_ai_regression"
    result = client.post(
        f"{BASE}/{skill}/evaluate",
        json=_input(skill, digest="0" * 64),
        headers=AUTH,
    )
    assert result.status_code == 409
    assert result.json()["detail"] == "ecc_fixture_identity_stale"
    assert client.get(f"{BASE}/harness_team_design/cases", headers=AUTH).status_code == 404
    assert (
        client.post(
            f"{BASE}/harness_team_design/evaluate",
            json=_input(skill),
            headers=AUTH,
        ).status_code
        == 404
    )


def test_duplicate_json_keys_large_body_auth_and_method_restrictions(client):
    skill = "ecc_security_review"
    path = f"{BASE}/{skill}/evaluate"
    valid = _input(skill)
    assert client.get(path, headers=AUTH).status_code == 405
    denied_get = client.get(f"{BASE}/{skill}/cases", headers={"Authorization": "Bearer bad"})
    assert denied_get.status_code == 401
    assert client.post(path, json=valid, headers={"Authorization": "Bearer bad"}).status_code == 401
    invalid_type = client.post(
        path, content=json.dumps(valid), headers={**AUTH, "Content-Type": "text/plain"}
    )
    assert invalid_type.status_code == 415
    empty_response = client.post(
        path, content=b"", headers={**AUTH, "Content-Type": "application/json"}
    )
    assert empty_response.status_code == 422
    duplicate = (
        b'{"candidate_label":"a","candidate_label":"b","expected_suite_sha256":"'
        + valid["expected_suite_sha256"].encode()
        + b'","answers":[]}'
    )
    result = client.post(
        path, content=duplicate, headers={**AUTH, "Content-Type": "application/json"}
    )
    assert result.status_code == 422
    assert result.json()["detail"] == "ecc_evaluation_invalid"
    oversized = client.post(
        path, content=b"x" * 8193, headers={**AUTH, "Content-Type": "application/json"}
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == "ecc_evaluation_size_denied"


def test_streamed_overlimit_upload_refuses_without_invoking_evaluator(client, monkeypatch):
    from app import community_ecc_eval_api as module

    invoked = []

    def forbidden_evaluator(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("oversized streaming input must not be evaluated")

    monkeypatch.setattr(module, "evaluate_ecc_fixed_cases", forbidden_evaluator)

    def chunks():
        yield b"{" * 4000
        yield b"x" * 5000

    response = client.post(
        f"{BASE}/ecc_security_review/evaluate",
        content=chunks(),
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "ecc_evaluation_size_denied"
    assert invoked == []


def test_stale_or_modified_upstream_reference_refuses_without_grants(client):
    skill = "ecc_agent_evaluation"
    exact_answers = _input(skill)
    with patch(
        "app.community_skill_catalog.verify_community_sources",
        side_effect=CommunitySourceRefusal("modified_source_byte"),
    ):
        case_response = client.get(f"{BASE}/{skill}/cases", headers=AUTH)
        verdict_response = client.post(
            f"{BASE}/{skill}/evaluate",
            json=exact_answers,
            headers=AUTH,
        )
    assert case_response.status_code == verdict_response.status_code == 503
    assert case_response.json()["detail"] == "ecc_fixture_source_unavailable"
    assert "modified_source_byte" not in verdict_response.text


def test_tampered_evaluation_receipt_is_rejected_by_checksum():
    suite = ecc_fixed_suite("ecc_ai_regression")
    input_model = EccCaseSubmission.model_validate(_input("ecc_ai_regression"), strict=False)
    assert input_model.expected_suite_sha256 == suite.suite_sha256
    receipt = evaluate_ecc_fixed_cases("ecc_ai_regression", input_model)
    altered = receipt.model_dump(mode="json")
    altered["cases"][0]["expected"] = "fail"
    with pytest.raises(ValidationError, match="ecc_evaluation_receipt_digest_invalid"):
        EccFixedEvaluation.model_validate_json(json.dumps(altered), strict=True)
    altered = receipt.model_dump(mode="json")
    altered["tool_grants"] = ["filesystem"]
    with pytest.raises(ValidationError):
        EccFixedEvaluation.model_validate_json(json.dumps(altered), strict=True)


def test_old_snapshot_suite_fingerprint_not_reusable_across_playbook_change(monkeypatch):
    from app import community_ecc_eval as module

    suite = ecc_fixed_suite("ecc_security_review")
    original = module.ecc_manual_playbook

    def changed_plan(skill_id):
        return original(skill_id).model_copy(update={"playbook_sha256": "0" * 64})

    monkeypatch.setattr(module, "ecc_manual_playbook", changed_plan)
    updated = module.ecc_fixed_suite("ecc_security_review")
    assert updated.suite_sha256 != suite.suite_sha256
    submission = EccCaseSubmission.model_validate(
        _input("ecc_security_review", digest=suite.suite_sha256), strict=False
    )
    with pytest.raises(EccFixtureRefusal, match="ecc_fixture_identity_stale"):
        evaluate_ecc_fixed_cases("ecc_security_review", submission)
