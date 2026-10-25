"""A fifth-run decision cannot be mistaken for a sixth-run permit."""

import copy
import json
from datetime import UTC, datetime

import pytest

from qualification import media_reader_post_cleanup_ledger as post_cleanup
from qualification import media_reader_run_approval_v3 as previous
from qualification import media_reader_run_approval_v4 as approval
from qualification import media_reader_sixth_ledger as ledger
from qualification.admission import AdmissionController

NOW = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
ISSUED = datetime(2026, 10, 25, 17, 29, tzinfo=UTC)
CHALLENGE = "c" * 64
COMMENT_ID = 6391943000
HEAD = "a" * 40
TREE = "b" * 40


def audited():
    return {
        "status": "verified-five-refusals-and-reconciled-fence",
        "proof_history": tuple(approval._history()),
        "attempts_used": 5,
        "attempt_limit": 5,
        "marker_cleared": True,
        "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
        "reconciliation_sha256": ledger.RECONCILIATION_SHA256,
        "residual_cleanup_completion_sha256": post_cleanup.COMPLETION_SHA256,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def evidence():
    history = approval._history()
    body = {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "challenge": CHALLENGE,
        "runtime_root": str(ledger.RETAINED_ROOT),
        "source_head": HEAD,
        "source_tree": TREE,
        "proof_history": history,
        "proof_history_sha256": previous._history_digest(history),
        "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
        "reconciliation_sha256": ledger.RECONCILIATION_SHA256,
        "residual_cleanup_completion_sha256": post_cleanup.COMPLETION_SHA256,
        "attempts_used_before": 5,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
        "expires_utc": "2026-10-25T19:00:00Z",
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
        "created_at": "2026-10-25T17:30:00Z",
        "updated_at": "2026-10-25T17:30:00Z",
        "body": json.dumps(body, sort_keys=True),
    }
    return repo, issue, comment


def check(repo, issue, comment, **changes):
    kwargs = {
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
    kwargs.update(changes)
    return approval.validate_snapshot(**kwargs)


def test_exact_sixth_comment_is_read_only_evidence():
    repo, issue, comment = evidence()
    result = check(repo, issue, comment)
    assert result["schema"] == "zelo-media-native-reader-v4-approval-snapshot-v1"
    assert result["proof_history_sha256"] == previous._history_digest(approval._history())
    assert result["cleanup_completion_sha256"] == ledger.CLEANUP_SHA256
    assert result["reconciliation_sha256"] == ledger.RECONCILIATION_SHA256
    assert result["residual_cleanup_completion_sha256"] == post_cleanup.COMPLETION_SHA256
    assert "run_authorized" not in result


@pytest.mark.parametrize(
    "change",
    [
        "old_fifth_decision",
        "old_fifth_schema",
        "four_history",
        "six_history",
        "other_cleanup",
        "other_reconciliation",
        "other_residual_cleanup",
        "missing_residual_cleanup",
        "changed_residual_ledger",
        "extra_budget",
        "runtime_authority",
        "wrong_author",
        "edited_comment",
        "old_comment",
        "wrong_source",
        "changed_ledger",
        "false_ledger_authorization",
        "wrong_root",
        "duplicate_key",
    ],
)
def test_refuses_prior_budget_and_drift(change):
    repo, issue, comment = copy.deepcopy(evidence())
    changes = {}
    body = json.loads(comment["body"])
    if change == "old_fifth_decision":
        body["decision"] = previous.DECISION
    elif change == "old_fifth_schema":
        body["schema"] = previous.SCHEMA
    elif change == "four_history":
        body["proof_history"] = body["proof_history"][:4]
    elif change == "six_history":
        body["proof_history"].append(body["proof_history"][0])
    elif change == "other_cleanup":
        body["cleanup_completion_sha256"] = "0" * 64
    elif change == "other_reconciliation":
        body["reconciliation_sha256"] = "0" * 64
    elif change == "other_residual_cleanup":
        body["residual_cleanup_completion_sha256"] = "0" * 64
    elif change == "missing_residual_cleanup":
        del body["residual_cleanup_completion_sha256"]
    elif change == "changed_residual_ledger":
        bad = audited()
        bad["residual_cleanup_completion_sha256"] = "0" * 64
        changes["audited_ledger"] = bad
    elif change == "extra_budget":
        body["additional_attempts"] = 2
    elif change == "runtime_authority":
        body["runtime_authority"] = True
    elif change == "wrong_author":
        comment["user"]["id"] += 1
    elif change == "edited_comment":
        comment["updated_at"] = "2026-10-25T17:31:00Z"
    elif change == "old_comment":
        comment["created_at"] = "2026-10-24T17:30:00Z"
    elif change == "wrong_source":
        body["source_head"] = "d" * 40
    elif change == "changed_ledger":
        bad = audited()
        bad["proof_history"] = tuple(approval._history()[:4])
        changes["audited_ledger"] = bad
    elif change == "false_ledger_authorization":
        bad = audited()
        bad["another_attempt_authorized"] = True
        changes["audited_ledger"] = bad
    elif change == "wrong_root":
        changes["expected_root"] = ledger.RETAINED_ROOT.parent
    elif change == "duplicate_key":
        comment["body"] = comment["body"].replace('"schema":', '"schema":"duplicate","schema":', 1)
    if change != "duplicate_key":
        comment["body"] = json.dumps(body, sort_keys=True)
    with pytest.raises(approval.ApprovalRefused):
        check(repo, issue, comment, **changes)


def test_fetch_reaudits_before_and_after_fixed_github_reads(monkeypatch):
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

    def get(path):
        calls.append(path)
        return copy.deepcopy(endpoints[path])

    monkeypatch.setattr(post_cleanup, "audit", audit)
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


def test_fetch_refuses_ledger_change_before_comment_is_returned(monkeypatch):
    repo, issue, comment = evidence()
    responses = iter((repo, issue, comment))
    count = 0

    def audit(_root):
        nonlocal count
        count += 1
        if count == 2:
            raise ledger.SixthLedgerRefused("marker_drift")
        return audited()

    monkeypatch.setattr(post_cleanup, "audit", audit)
    with pytest.raises(approval.ApprovalRefused, match="ledger"):
        approval.fetch_validated_snapshot(
            get=lambda _path: next(responses),
            expected_comment_id=COMMENT_ID,
            expected_challenge=CHALLENGE,
            challenge_issued_at=ISSUED,
            source_head=HEAD,
            source_tree=TREE,
            clock=lambda: NOW,
        )


def test_symlink_loop_in_retained_ledger_is_bounded_refusal(monkeypatch):
    def broken(_root):
        raise RuntimeError("Symlink loop from retained root")

    monkeypatch.setattr(post_cleanup, "audit", broken)
    with pytest.raises(approval.ApprovalRefused, match="ledger") as error:
        approval.fetch_validated_snapshot(
            get=lambda _path: pytest.fail("must not fetch after ledger refusal"),
            expected_comment_id=COMMENT_ID,
            expected_challenge=CHALLENGE,
            challenge_issued_at=ISSUED,
            source_head=HEAD,
            source_tree=TREE,
            clock=lambda: NOW,
        )
    assert "Symlink loop" not in str(error.value)


def test_held_fetch_and_recheck_do_not_reacquire_admission_lock(monkeypatch):
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

    def public(_root):
        pytest.fail("would reacquire the already held admission lock")

    def get(path):
        calls.append(path)
        return copy.deepcopy(endpoints[path])

    monkeypatch.setattr(post_cleanup, "_audit_held", held)
    monkeypatch.setattr(post_cleanup, "audit", public)
    binding = {
        "root": ledger.RETAINED_ROOT,
        "get": get,
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "challenge_issued_at": ISSUED,
        "source_head": HEAD,
        "source_tree": TREE,
        "clock": lambda: NOW,
    }
    original = approval.fetch_validated_snapshot_held(**binding)
    assert calls == ["held", *endpoints, "held"]
    endpoints[f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"]["body"] += " "
    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        approval.recheck_snapshot_held(original=original, **binding)
    assert calls == ["held", *endpoints, "held", "held", *endpoints, "held"]


def test_held_fetch_refuses_wrong_root_before_github_read(monkeypatch):
    monkeypatch.setattr(post_cleanup, "_audit_held", lambda _root: pytest.fail("no audit"))
    with pytest.raises(approval.ApprovalRefused, match="root"):
        approval.fetch_validated_snapshot_held(
            root=ledger.RETAINED_ROOT.parent,
            get=lambda _path: pytest.fail("no fetch"),
            expected_comment_id=COMMENT_ID,
            expected_challenge=CHALLENGE,
            challenge_issued_at=ISSUED,
            source_head=HEAD,
            source_tree=TREE,
            clock=lambda: NOW,
        )


def test_live_lease_recheck_reads_only_fixed_endpoints_between_held_audits(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    monkeypatch.setattr(ledger, "RETAINED_ROOT", root)
    monkeypatch.setattr(post_cleanup, "RETAINED_ROOT", root)
    repo, issue, comment = evidence()
    original = check(repo, issue, comment)
    endpoints = {
        "/repos/kANoZY341/zelo-agent": repo,
        "/repos/kANoZY341/zelo-agent/issues/166": issue,
        f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}": comment,
    }
    calls = []
    lease = AdmissionController(admission).acquire("b" * 64)

    def live(path, active, before):
        assert path == root and active is lease and before == audited()
        assert lease._retained_thread is not None
        calls.append("live")
        return audited()

    def get(path):
        calls.append(path)
        return copy.deepcopy(endpoints[path])

    monkeypatch.setattr(post_cleanup, "_audit_held_live", live)
    monkeypatch.setattr(post_cleanup, "audit", lambda _root: pytest.fail("nested lock"))
    binding = {
        "root": root,
        "lease": lease,
        "before": audited(),
        "get": get,
        "expected_comment_id": COMMENT_ID,
        "expected_challenge": CHALLENGE,
        "challenge_issued_at": ISSUED,
        "source_head": HEAD,
        "source_tree": TREE,
        "clock": lambda: NOW,
    }
    with lease.retained():
        assert approval.recheck_snapshot_lease(original=original, **binding) == original
        assert calls == ["live", *endpoints, "live"]
        endpoints[f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"]["body"] += " "
        with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
            approval.recheck_snapshot_lease(original=original, **binding)
    lease.finish(cleanup_confirmed=True)


def test_live_lease_recheck_refuses_ledger_before_remote_read(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    monkeypatch.setattr(ledger, "RETAINED_ROOT", root)
    lease = AdmissionController(admission).acquire("b" * 64)
    monkeypatch.setattr(
        post_cleanup,
        "_audit_held_live",
        lambda *_: (_ for _ in ()).throw(post_cleanup.PostCleanupRefused("changed marker")),
    )
    with lease.retained():
        with pytest.raises(approval.ApprovalRefused, match="ledger"):
            approval.recheck_snapshot_lease(
                original={},
                root=root,
                lease=lease,
                before=audited(),
                get=lambda _path: pytest.fail("must not fetch"),
                expected_comment_id=COMMENT_ID,
                expected_challenge=CHALLENGE,
                challenge_issued_at=ISSUED,
                source_head=HEAD,
                source_tree=TREE,
                clock=lambda: NOW,
            )
    lease.finish(cleanup_confirmed=True)
