"""Offline refusal checks for proposed Media run-approval evidence only."""

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from qualification import media_reader_run_approval_v3 as approval

HISTORY = [
    {
        "attempt": f"native-reader-proof-attempt-{i}",
        "policy_sha256": str(i) * 64,
        "proof_sha256": str(i + 4) * 64,
        "source_head": str(i) * 40,
        "source_tree": str(i + 4) * 40,
    }
    for i in range(1, 5)
]
HISTORY_SHA256 = approval._history_digest(HISTORY)
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
COMMENT_ID = 123456
MD_ID = 64309771  # Synthetic test mapping; no trusted production mapping exists.


def evidence(root):
    body = {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "runtime_root": str(root),
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "proof_history": HISTORY,
        "proof_history_sha256": HISTORY_SHA256,
        "attempts_used_before": 4,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
        "expires_utc": "2026-10-20T19:00:00Z",
    }
    repository = {
        "id": approval.REPOSITORY_ID,
        "full_name": "kANoZY341/zelo-agent",
        "url": approval.API_ROOT,
    }
    issue = {
        "id": approval.ISSUE_ID,
        "number": 166,
        "repository_url": approval.API_ROOT,
    }
    comment = {
        "id": COMMENT_ID,
        "issue_url": f"{approval.API_ROOT}/issues/166",
        "url": f"{approval.API_ROOT}/issues/comments/{COMMENT_ID}",
        "user": {"id": MD_ID},
        "created_at": "2026-10-20T17:30:00Z",
        "updated_at": "2026-10-20T17:30:00Z",
        "body": json.dumps(body, sort_keys=True),
    }
    return repository, issue, comment


def check(root, repository, issue, comment, **changes):
    args = {
        "repository": repository,
        "issue": issue,
        "comment": comment,
        "expected_comment_id": COMMENT_ID,
        "md_account_id": MD_ID,
        "expected_root": root,
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "proof_history": HISTORY,
        "now": NOW,
    }
    args.update(changes)
    return approval.validate_snapshot(**args)


def test_exact_snapshot_is_bounded_audit_evidence_without_any_side_effect(tmp_path):
    repository, issue, comment = evidence(tmp_path)
    result = check(tmp_path, repository, issue, comment)
    assert result["comment_body_sha256"] == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert result["comment_id"] == COMMENT_ID
    assert result["repository_id"] == approval.REPOSITORY_ID
    assert result["issue_id"] == approval.ISSUE_ID
    assert result["proof_history_sha256"] == HISTORY_SHA256
    assert result["runtime_root"] == str(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "change",
    [
        "no_md_mapping",
        "wrong_repo",
        "wrong_issue",
        "wrong_author",
        "wrong_comment",
        "edited",
        "expired",
        "source_drift",
        "history_drift",
        "runtime_authority",
        "duplicate_key",
        "oversized_body",
        "wrong_history_name",
        "wrong_history_shape",
        "invalid_source_type",
        "invalid_history_type",
        "invalid_clock_type",
    ],
)
def test_snapshot_refuses_identity_mutation_and_stale_or_ambiguous_decision(tmp_path, change):
    repository, issue, comment = copy.deepcopy(evidence(tmp_path))
    changes = {}
    if change == "no_md_mapping":
        changes["md_account_id"] = None
    elif change == "wrong_repo":
        repository["id"] += 1
    elif change == "wrong_issue":
        issue["id"] += 1
    elif change == "wrong_author":
        comment["user"]["id"] += 1
    elif change == "wrong_comment":
        comment["id"] += 1
    elif change == "edited":
        comment["updated_at"] = "2026-10-20T17:31:00Z"
    elif change == "expired":
        changes["now"] = datetime(2026, 10, 20, 19, 0, tzinfo=UTC)
    elif change == "invalid_source_type":
        changes["source_head"] = None
    elif change == "invalid_history_type":
        changes["proof_history"] = [None] * 4
    elif change == "invalid_clock_type":
        changes["now"] = None
    elif change == "oversized_body":
        comment["body"] = "x" * 4097
    elif change == "duplicate_key":
        comment["body"] = comment["body"].replace(
            '"schema":', '"schema": "duplicate", "schema":', 1
        )
    else:
        body = json.loads(comment["body"])
        if change == "source_drift":
            body["source_head"] = "c" * 40
        elif change == "wrong_history_name":
            body["proof_history"][0]["attempt"] = "native-reader-proof-other"
        elif change == "wrong_history_shape":
            body["proof_history"][0]["unexpected"] = True
        elif change == "history_drift":
            body["proof_history_sha256"] = "0" * 64
        elif change == "runtime_authority":
            body["runtime_authority"] = True
        comment["body"] = json.dumps(body, sort_keys=True)
    with pytest.raises(approval.ApprovalRefused):
        check(tmp_path, repository, issue, comment, **changes)
    assert list(tmp_path.iterdir()) == []
