"""Disposable one-use sixth decision capture; no native attempt or Docker call."""

import json
from datetime import UTC, datetime

import pytest

from qualification import media_reader_approval_capture_v4 as capture
from qualification import media_reader_post_cleanup_ledger as post_cleanup
from qualification import media_reader_run_approval_v3 as prior
from qualification import media_reader_run_approval_v4 as approval
from qualification import media_reader_sixth_ledger as ledger
from qualification.admission import AdmissionDenied

ROOT_HEAD = "a" * 40
ROOT_TREE = "b" * 40
COMMENT_ID = 6391943001
ISSUED = datetime(2026, 10, 25, 17, 29, tzinfo=UTC)
NOW = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)


def _clock():
    first = True

    def read():
        nonlocal first
        if first:
            first = False
            return ISSUED
        return NOW

    return read


def _audited():
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


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    monkeypatch.setattr(post_cleanup, "RETAINED_ROOT", root)
    monkeypatch.setattr(ledger, "RETAINED_ROOT", root)
    monkeypatch.setattr(capture, "RETAINED_ROOT", root)
    monkeypatch.setattr(capture.preflight, "RETAINED_ROOT", root)
    monkeypatch.setattr(capture, "_source_identity", lambda: (ROOT_HEAD, ROOT_TREE))
    audits = []

    def audit(path):
        assert path == root
        audits.append(path)
        return _audited()

    monkeypatch.setattr(post_cleanup, "_audit_held", audit)
    checks = []
    monkeypatch.setattr(capture.preflight, "observe_held", lambda path: checks.append(path))
    responses = {
        "/repos/kANoZY341/zelo-agent": {
            "id": prior.REPOSITORY_ID,
            "full_name": "kANoZY341/zelo-agent",
            "url": prior.API_ROOT,
        },
        "/repos/kANoZY341/zelo-agent/issues/166": {
            "id": prior.ISSUE_ID,
            "number": prior.ISSUE_NUMBER,
            "repository_url": prior.API_ROOT,
        },
    }

    def request(challenge, history):
        body = {
            "schema": approval.SCHEMA,
            "decision": approval.DECISION,
            "challenge": challenge,
            "runtime_root": str(root),
            "source_head": ROOT_HEAD,
            "source_tree": ROOT_TREE,
            "proof_history": history,
            "proof_history_sha256": prior._history_digest(history),
            "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
            "reconciliation_sha256": ledger.RECONCILIATION_SHA256,
            "residual_cleanup_completion_sha256": post_cleanup.COMPLETION_SHA256,
            "attempts_used_before": 5,
            "additional_attempts": 1,
            "network": "none",
            "runtime_authority": False,
            "expires_utc": "2026-10-25T19:00:00Z",
        }
        responses[f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"] = {
            "id": COMMENT_ID,
            "issue_url": f"{prior.API_ROOT}/issues/166",
            "url": f"{prior.API_ROOT}/issues/comments/{COMMENT_ID}",
            "user": {"id": prior.MD_ACCOUNT_ID},
            "created_at": "2026-10-25T17:30:00Z",
            "updated_at": "2026-10-25T17:30:00Z",
            "body": json.dumps(body, sort_keys=True),
        }
        return COMMENT_ID

    def get(path):
        return responses[path]

    return root, audits, checks, request, get, responses


def test_approved_capture_is_durable_and_one_use_without_attempt(candidate):
    root, audits, checks, request, get, _ = candidate
    session = capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE))
    lease, snapshot = session.acquire("d" * 64, request, get=get, clock=_clock())
    try:
        assert snapshot["comment_id"] == COMMENT_ID
        assert len(audits) >= 4 and checks == [root, root, root]
        assert (root / capture.BASE / "receipt.json").is_file()
        assert (root / capture.BASE / "recheck.json").is_file()
        grant = json.loads((root / capture.GRANT_BASE / "grant.json").read_text())
        assert grant["attempt_limit_after"] == 6
        assert grant["approval_comment_id"] == COMMENT_ID
        assert grant["residual_cleanup_completion_sha256"] == post_cleanup.COMPLETION_SHA256
        assert (root / capture.GRANT_BASE / "seal.json").is_file()
        assert (root / "media-elf-metadata-admission/active.marker").stat().st_size > 0
        assert not (root / "media-native-reader").exists()
        with pytest.raises(capture.CaptureRefused, match="already_used"):
            session.acquire("d" * 64, request, get=get, clock=_clock())
    finally:
        lease.finish(cleanup_confirmed=True)
    with pytest.raises(capture.CaptureRefused, match="prior_consumption"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=_clock()
        )


def test_changed_comment_fences_receipt_without_grant_or_marker(candidate):
    root, _, _, request, get, responses = candidate
    calls = 0

    def drift(path):
        nonlocal calls
        if path.endswith(f"/{COMMENT_ID}"):
            calls += 1
            if calls == 2:
                responses[path]["body"] += " "
        return get(path)

    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=drift, clock=_clock()
        )
    assert (root / capture.BASE / "receipt.json").is_file()
    assert not (root / capture.GRANT_BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_live_resource_refusal_precedes_decision_and_any_receipt(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    monkeypatch.setattr(
        capture.preflight,
        "observe_held",
        lambda _root: (_ for _ in ()).throw(ValueError("occupied namespace")),
    )
    with pytest.raises(ValueError, match="occupied namespace"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64,
            lambda *_: pytest.fail("decision requested despite occupied Docker"),
            get=get,
            clock=_clock(),
        )
    assert not (root / capture.BASE).exists()
    assert not (root / capture.GRANT_BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_uncertain_receipt_write_consumes_decision_without_grant_or_marker(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    original = capture._write_once

    def uncertain(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == "receipt.json":
            raise OSError("uncertain receipt return")
        return result

    monkeypatch.setattr(capture, "_write_once", uncertain)
    with pytest.raises(AdmissionDenied, match="runtime_unavailable"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=_clock()
        )
    assert (root / capture.BASE / "receipt.json").is_file()
    assert not (root / capture.GRANT_BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""
    with pytest.raises(capture.CaptureRefused, match="prior_consumption"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=_clock()
        )


def test_wrong_account_refuses_before_a_receipt(candidate):
    root, _, _, request, get, responses = candidate

    def wrong_author(challenge, history):
        comment_id = request(challenge, history)
        responses[f"/repos/kANoZY341/zelo-agent/issues/comments/{comment_id}"]["user"]["id"] += 1
        return comment_id

    with pytest.raises(approval.ApprovalRefused, match="identity"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, wrong_author, get=get, clock=_clock()
        )
    assert not (root / capture.BASE).exists()
    assert not (root / capture.GRANT_BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_resource_appearing_after_github_reads_refuses_before_receipt(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    checks = 0

    def preflight(_root):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("new labelled resource")

    monkeypatch.setattr(capture.preflight, "observe_held", preflight)
    with pytest.raises(ValueError, match="new labelled resource"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=_clock()
        )
    assert checks == 2
    assert not (root / capture.BASE).exists()
    assert not (root / capture.GRANT_BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_resource_appearing_during_final_comment_recheck_refuses_grant(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    checks = 0

    def preflight(_root):
        nonlocal checks
        checks += 1
        if checks == 3:
            raise ValueError("resource appeared during recheck")

    monkeypatch.setattr(capture.preflight, "observe_held", preflight)
    with pytest.raises(ValueError, match="resource appeared during recheck"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=_clock()
        )
    assert checks == 3
    assert (root / capture.BASE / "recheck.json").is_file()
    assert not (root / capture.GRANT_BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_expiry_after_durable_grant_and_marker_refuses_return(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    original = capture._write_once
    state = {"first": True, "late": False}

    def clock():
        if state["first"]:
            state["first"] = False
            return ISSUED
        return datetime(2026, 10, 25, 19, 1, tzinfo=UTC) if state["late"] else NOW

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == "seal.json":
            state["late"] = True
        return result

    monkeypatch.setattr(capture, "_write_once", delayed)
    with pytest.raises(capture.CaptureRefused, match="expired_after_marker"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=clock
        )
    assert (root / capture.GRANT_BASE / "seal.json").is_file()
    assert (root / "media-elf-metadata-admission/active.marker").stat().st_size > 0


def test_source_drift_after_marker_leaves_fence_and_no_lease(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    original = capture._write_once
    state = {"changed": False}

    def source():
        return ("c" * 40, ROOT_TREE) if state["changed"] else (ROOT_HEAD, ROOT_TREE)

    def change_after_seal(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == "seal.json":
            state["changed"] = True
        return result

    monkeypatch.setattr(capture, "_source_identity", source)
    monkeypatch.setattr(capture, "_write_once", change_after_seal)
    with pytest.raises(capture.CaptureRefused, match="post_marker_drift"):
        capture.ApprovalCapture(root, expected_source=(ROOT_HEAD, ROOT_TREE)).acquire(
            "d" * 64, request, get=get, clock=_clock()
        )
    assert (root / capture.GRANT_BASE / "seal.json").is_file()
    assert (root / "media-elf-metadata-admission/active.marker").stat().st_size > 0
