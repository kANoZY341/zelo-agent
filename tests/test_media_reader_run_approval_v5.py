"""The six consumed proofs cannot be turned into a saved seventh-run permit."""

import copy
import json
from datetime import UTC, datetime

import pytest

from qualification import media_reader_run_approval_v3 as previous
from qualification import media_reader_run_approval_v5 as approval
from qualification import media_reader_seventh_ledger as ledger

NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)
ISSUED = datetime(2026, 10, 27, 17, 29, tzinfo=UTC)
CHALLENGE = "c" * 64
COMMENT_ID = 6419000000
HEAD = "a" * 40
TREE = "b" * 40


def audited():
    return {
        "status": "verified-six-refusals-and-exact-image-cleanup",
        "proof_history": tuple(ledger._history()),
        "attempts_used": 6,
        "attempt_limit": 6,
        "cleanup_completion_sha256": ledger.COMPLETION_SHA256,
        "marker_cleared": True,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def evidence():
    history = list(ledger._history())
    body = {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "challenge": CHALLENGE,
        "runtime_root": str(ledger.RETAINED_ROOT),
        "source_head": HEAD,
        "source_tree": TREE,
        "proof_history": history,
        "proof_history_sha256": previous._history_digest(history),
        "sixth_image_cleanup_completion_sha256": ledger.COMPLETION_SHA256,
        "attempts_used_before": 6,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
        "expires_utc": "2026-10-27T19:00:00Z",
    }
    repo = {
        "id": previous.REPOSITORY_ID,
        "full_name": "kANoZY341/zelo-agent",
        "url": previous.API_ROOT,
    }
    issue = {
        "id": previous.ISSUE_ID,
        "number": previous.ISSUE_NUMBER,
        "repository_url": previous.API_ROOT,
    }
    comment = {
        "id": COMMENT_ID,
        "issue_url": f"{previous.API_ROOT}/issues/{previous.ISSUE_NUMBER}",
        "url": f"{previous.API_ROOT}/issues/comments/{COMMENT_ID}",
        "user": {"id": previous.MD_ACCOUNT_ID},
        "created_at": "2026-10-27T17:30:00Z",
        "updated_at": "2026-10-27T17:30:00Z",
        "body": json.dumps(body, sort_keys=True),
    }
    return repo, issue, comment


def check(repo, issue, comment, **changes):
    binding = {
        "repository": repo,
        "issue": issue,
        "comment": comment,
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "challenge_issued_at": ISSUED,
        "expected_root": ledger.RETAINED_ROOT,
        "source_head": HEAD,
        "source_tree": TREE,
        "audited_ledger": audited(),
        "now": NOW,
    }
    return approval.validate_snapshot(**(binding | changes))


def test_exact_decision_is_evidence_with_six_pinned_refusals():
    result = check(*evidence())
    assert result["schema"] == "zelo-media-native-reader-v5-approval-snapshot-v1"
    assert result["sixth_image_cleanup_completion_sha256"] == (
        "6aebba764d8ff5c9dfa373feabd74be01165df08d886c93719171bf2805d078b"
    )
    assert result["proof_history_sha256"] == previous._history_digest(list(ledger._history()))
    assert "run_authorized" not in result


@pytest.mark.parametrize(
    "change",
    [
        "old_decision",
        "old_schema",
        "five_proofs",
        "seven_proofs",
        "old_cleanup",
        "missing_cleanup",
        "extra_budget",
        "runtime_authority",
        "wrong_author",
        "edited_comment",
        "wrong_source",
        "wrong_root",
        "changed_ledger",
        "false_authorization",
        "duplicate_key",
    ],
)
def test_refuses_stale_decision_and_history_drift(change):
    repo, issue, comment = copy.deepcopy(evidence())
    body = json.loads(comment["body"])
    changes = {}
    if change == "old_decision":
        body["decision"] = "authorize-one-sixth-native-media-reader-proof"
    elif change == "old_schema":
        body["schema"] = "zelo-media-native-reader-sixth-run-approval-v1"
    elif change == "five_proofs":
        body["proof_history"] = body["proof_history"][:5]
    elif change == "seven_proofs":
        body["proof_history"].append(body["proof_history"][0])
    elif change == "old_cleanup":
        body["sixth_image_cleanup_completion_sha256"] = "0" * 64
    elif change == "missing_cleanup":
        del body["sixth_image_cleanup_completion_sha256"]
    elif change == "extra_budget":
        body["additional_attempts"] = 2
    elif change == "runtime_authority":
        body["runtime_authority"] = True
    elif change == "wrong_author":
        comment["user"]["id"] += 1
    elif change == "edited_comment":
        comment["updated_at"] = "2026-10-27T17:31:00Z"
    elif change == "wrong_source":
        body["source_head"] = "d" * 40
    elif change == "wrong_root":
        changes["expected_root"] = ledger.RETAINED_ROOT.parent
    elif change == "changed_ledger":
        bad = audited()
        bad["proof_history"] = bad["proof_history"][:5]
        changes["audited_ledger"] = bad
    elif change == "false_authorization":
        bad = audited()
        bad["another_attempt_authorized"] = True
        changes["audited_ledger"] = bad
    elif change == "duplicate_key":
        comment["body"] = comment["body"].replace('"schema":', '"schema":"duplicate","schema":', 1)
    if change != "duplicate_key":
        comment["body"] = json.dumps(body, sort_keys=True)
    with pytest.raises(approval.ApprovalRefused):
        check(repo, issue, comment, **changes)


def test_fixed_fetch_reaudits_and_recheck_refuses_edited_comment(monkeypatch):
    repo, issue, comment = evidence()
    endpoints = {
        "/repos/kANoZY341/zelo-agent": repo,
        "/repos/kANoZY341/zelo-agent/issues/166": issue,
        f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}": comment,
    }
    calls = []

    def audit(root):
        assert root == ledger.RETAINED_ROOT
        calls.append("audit")
        return audited()

    def get(endpoint):
        calls.append(endpoint)
        return copy.deepcopy(endpoints[endpoint])

    monkeypatch.setattr(ledger, "audit", audit)
    binding = {
        "get": get,
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "challenge_issued_at": ISSUED,
        "source_head": HEAD,
        "source_tree": TREE,
        "clock": lambda: NOW,
    }
    original = approval.fetch_validated_snapshot(**binding)
    assert calls == ["audit", *endpoints, "audit"]
    endpoints[f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"]["body"] += " "
    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        approval.recheck_snapshot(original=original, **binding)


def test_fetch_refuses_ledger_drift_before_return(monkeypatch):
    repo, issue, comment = evidence()
    reads = iter((repo, issue, comment))
    count = 0

    def audit(_root):
        nonlocal count
        count += 1
        return audited() if count == 1 else {**audited(), "attempts_used": 5}

    monkeypatch.setattr(ledger, "audit", audit)
    with pytest.raises(approval.ApprovalRefused, match="ledger_drift"):
        approval.fetch_validated_snapshot(
            get=lambda _path: next(reads),
            expected_comment_id=COMMENT_ID,
            expected_challenge=CHALLENGE,
            challenge_issued_at=ISSUED,
            source_head=HEAD,
            source_tree=TREE,
            clock=lambda: NOW,
        )


def test_held_fetch_uses_caller_lock_and_refuses_changed_comment(monkeypatch):
    repo, issue, comment = evidence()
    endpoints = {
        "/repos/kANoZY341/zelo-agent": repo,
        "/repos/kANoZY341/zelo-agent/issues/166": issue,
        f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}": comment,
    }
    calls = []

    def held(root):
        assert root == ledger.RETAINED_ROOT
        calls.append("held")
        return audited()

    monkeypatch.setattr(ledger, "_audit_held", held)
    monkeypatch.setattr(
        ledger,
        "audit",
        lambda _root: pytest.fail("caller holds admission lock"),
    )
    binding = {
        "root": ledger.RETAINED_ROOT,
        "get": lambda endpoint: copy.deepcopy(endpoints[endpoint]),
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "challenge_issued_at": ISSUED,
        "source_head": HEAD,
        "source_tree": TREE,
        "clock": lambda: NOW,
    }
    original = approval.fetch_validated_snapshot_held(**binding)
    assert calls == ["held", "held"]
    endpoints[f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"]["body"] += " "
    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        approval.recheck_snapshot_held(original=original, **binding)
