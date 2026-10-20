"""Offline refusal checks for proposed Media run-approval evidence only."""

import copy
import hashlib
import json
import traceback
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
CHALLENGE = "c" * 64
MD_ID = approval.MD_ACCOUNT_ID  # Confirmed identity; still no fifth-run decision.


def evidence(root):
    body = {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "challenge": CHALLENGE,
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
        "expected_challenge": CHALLENGE,
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
    assert result["challenge_sha256"] == hashlib.sha256(CHALLENGE.encode()).hexdigest()
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
        "wrong_challenge",
        "invalid_challenge_type",
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
    elif change == "invalid_challenge_type":
        changes["expected_challenge"] = None
    elif change == "oversized_body":
        comment["body"] = "x" * 4097
    elif change == "duplicate_key":
        comment["body"] = comment["body"].replace(
            '"schema":', '"schema": "duplicate", "schema":', 1
        )
    else:
        body = json.loads(comment["body"])
        if change == "wrong_challenge":
            body["challenge"] = "d" * 64
        elif change == "source_drift":
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


def test_caller_cannot_substitute_a_different_md_account_mapping(tmp_path):
    repository, issue, comment = evidence(tmp_path)
    comment["user"]["id"] = MD_ID + 1
    with pytest.raises(approval.ApprovalRefused, match="md_mapping"):
        check(tmp_path, repository, issue, comment, md_account_id=MD_ID + 1)
    assert list(tmp_path.iterdir()) == []


def test_live_snapshot_reads_only_three_fixed_endpoints_and_rechecks_changed_comment(tmp_path):
    repository, issue, comment = evidence(tmp_path)
    fetched = {
        "/repos/kANoZY341/zelo-agent": repository,
        "/repos/kANoZY341/zelo-agent/issues/166": issue,
        f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}": comment,
    }
    calls = []

    def get(endpoint):
        calls.append(endpoint)
        return copy.deepcopy(fetched[endpoint])

    binding = {
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "md_account_id": MD_ID,
        "expected_root": tmp_path,
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "proof_history": HISTORY,
        "clock": lambda: NOW,
    }
    snapshot = approval.fetch_validated_snapshot(get=get, **binding)
    assert calls == list(fetched)
    assert list(tmp_path.iterdir()) == []

    fetched[f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"]["body"] += " "
    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        approval.recheck_snapshot(get=get, original=snapshot, **binding)
    assert list(tmp_path.iterdir()) == []


def test_live_snapshot_refuses_fetch_failure_and_nonobject_response(tmp_path):
    binding = {
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "md_account_id": MD_ID,
        "expected_root": tmp_path,
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "proof_history": HISTORY,
        "clock": lambda: NOW,
    }

    def failure(_endpoint):
        raise OSError("private token or network diagnostic must not escape")

    with pytest.raises(approval.ApprovalRefused, match="fetch_failed") as error:
        approval.fetch_validated_snapshot(get=failure, **binding)
    assert "private token" not in str(error.value)
    assert "private token" not in "".join(traceback.format_exception(error.value))
    with pytest.raises(approval.ApprovalRefused, match="fetch_shape"):
        approval.fetch_validated_snapshot(get=lambda _endpoint: [], **binding)
    assert list(tmp_path.iterdir()) == []


def test_live_snapshot_refuses_invalid_comment_id_before_fetch(tmp_path):
    called = False

    def get(_endpoint):
        nonlocal called
        called = True
        raise AssertionError("must not fetch")

    with pytest.raises(approval.ApprovalRefused, match="comment_id"):
        approval.fetch_validated_snapshot(
            get=get,
            expected_comment_id="../../123",
            expected_challenge=CHALLENGE,
            md_account_id=MD_ID,
            expected_root=tmp_path,
            source_head="a" * 40,
            source_tree="b" * 40,
            proof_history=HISTORY,
            clock=lambda: NOW,
        )
    assert not called


def test_gh_reader_uses_get_only_and_never_exposes_cli_diagnostics(monkeypatch):
    import subprocess

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"id":1373657980}', stderr="")

    monkeypatch.setattr(approval.subprocess, "run", run)
    assert approval.gh_api_get("/repos/kANoZY341/zelo-agent") == {"id": 1373657980}
    assert calls[0][0] == [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--method",
        "GET",
        "/repos/kANoZY341/zelo-agent",
    ]
    assert calls[0][1]["timeout"] == 10
    with pytest.raises(approval.ApprovalRefused, match="endpoint"):
        approval.gh_api_get("/repos/kANoZY341/zelo-agent/issues/comments/123/../../other")
    assert len(calls) == 1

    def failed(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="secret diagnostic")

    monkeypatch.setattr(approval.subprocess, "run", failed)
    with pytest.raises(approval.ApprovalRefused) as error:
        approval.gh_api_get("/repos/kANoZY341/zelo-agent")
    assert "secret diagnostic" not in str(error.value)
    assert "secret diagnostic" not in "".join(traceback.format_exception(error.value))

    def raised(_argv, **_kwargs):
        raise OSError("private credential diagnostic")

    monkeypatch.setattr(approval.subprocess, "run", raised)
    with pytest.raises(approval.ApprovalRefused, match="fetch_failed") as raised_error:
        approval.gh_api_get("/repos/kANoZY341/zelo-agent")
    assert "private credential" not in "".join(traceback.format_exception(raised_error.value))


def test_live_snapshot_checks_expiry_after_remote_reads(tmp_path):
    repository, issue, comment = evidence(tmp_path)
    fetched = iter((repository, issue, comment))
    with pytest.raises(approval.ApprovalRefused, match="expired"):
        approval.fetch_validated_snapshot(
            get=lambda _endpoint: next(fetched),
            expected_comment_id=COMMENT_ID,
            expected_challenge=CHALLENGE,
            md_account_id=MD_ID,
            expected_root=tmp_path,
            source_head="a" * 40,
            source_tree="b" * 40,
            proof_history=HISTORY,
            clock=lambda: datetime(2026, 10, 20, 19, 0, tzinfo=UTC),
        )
    assert list(tmp_path.iterdir()) == []


def test_live_snapshot_refuses_malformed_challenge_before_remote_fetch(tmp_path):
    with pytest.raises(approval.ApprovalRefused, match="challenge"):
        approval.fetch_validated_snapshot(
            get=lambda _endpoint: pytest.fail("remote fetch before challenge validation"),
            expected_comment_id=COMMENT_ID,
            expected_challenge="fixed-reusable-approval",
            md_account_id=MD_ID,
            expected_root=tmp_path,
            source_head="a" * 40,
            source_tree="b" * 40,
            proof_history=HISTORY,
            clock=lambda: NOW,
        )
    assert list(tmp_path.iterdir()) == []
