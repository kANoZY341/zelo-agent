"""Disposable four-attempt admission tests; no retained root or native Docker."""

import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime

import pytest

from qualification import media_reader_approval_capture_v3 as capture
from qualification import media_reader_extra_attempt_v3 as v3
from qualification import media_reader_run_approval_v3 as approval
from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime

ISSUED = datetime(2026, 10, 20, 17, 29, tzinfo=UTC)
CHECKED = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
COMMENT_ID = 123456


def _github_fixture(root, history, challenge):
    body = {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "challenge": challenge,
        "runtime_root": str(root),
        "source_head": "e" * 40,
        "source_tree": "f" * 40,
        "proof_history": history,
        "proof_history_sha256": approval._history_digest(history),
        "attempts_used_before": 4,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
        "expires_utc": "2026-10-20T19:00:00Z",
    }
    base = "/repos/kANoZY341/zelo-agent"
    return {
        base: {
            "id": approval.REPOSITORY_ID,
            "full_name": "kANoZY341/zelo-agent",
            "url": approval.API_ROOT,
        },
        f"{base}/issues/166": {
            "id": approval.ISSUE_ID,
            "number": 166,
            "repository_url": approval.API_ROOT,
        },
        f"{base}/issues/comments/{COMMENT_ID}": {
            "id": COMMENT_ID,
            "issue_url": f"{approval.API_ROOT}/issues/166",
            "url": f"{approval.API_ROOT}/issues/comments/{COMMENT_ID}",
            "user": {"id": approval.MD_ACCOUNT_ID},
            "created_at": "2026-10-20T17:30:00Z",
            "updated_at": "2026-10-20T17:30:00Z",
            "body": json.dumps(body, sort_keys=True),
        },
    }


def test_capture_generates_challenge_under_lock_and_writes_receipt_without_grant_or_proof(
    tmp_path, monkeypatch
):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    _, record = v3._history(root)
    seen = []
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: seen.append(n) or "c" * 64)

    def request(challenge, history):
        fd = os.open(root / "media-elf-metadata-admission" / "admission.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        assert challenge == "c" * 64
        assert history == record["proof_history"]
        fetched = _github_fixture(root, history, challenge)
        monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
        return COMMENT_ID

    session = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40))
    clock = iter((ISSUED, CHECKED, CHECKED)).__next__
    lease, snapshot = session.acquire("a" * 64, request, clock=clock)
    assert seen == [32, 32]  # Challenge and the admission lease identity.
    assert snapshot["comment_id"] == COMMENT_ID
    receipt = json.loads((root / capture.BASE / capture.RECEIPT).read_bytes())
    assert receipt["snapshot"]["comment_body_sha256"] == snapshot["comment_body_sha256"]
    assert (
        receipt["snapshot"]["challenge_sha256"] == hashlib.sha256(("c" * 64).encode()).hexdigest()
    )
    assert not (root / v3.BASE).exists()
    assert len(list((root / "media-native-reader").iterdir())) == 4
    lease.finish(cleanup_confirmed=True)
    with pytest.raises(capture.CaptureRefused, match="already_used"):
        session.acquire("a" * 64, request, clock=lambda: CHECKED)


def test_capture_refuses_stale_comment_before_receipt_and_native_effect(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "d" * 64)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    session = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40))
    with pytest.raises(approval.ApprovalRefused, match="body_binding"):
        clock = iter((ISSUED, CHECKED)).__next__
        session.acquire("a" * 64, lambda challenge, history: COMMENT_ID, clock=clock)
    assert not (root / capture.BASE).exists()
    assert not (root / v3.BASE).exists()
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_capture_parent_sync_failure_never_marks_admission_or_grants(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    original_fsync = capture.os.fsync
    root_inode = root.stat().st_ino

    def fail_root_sync(fd):
        if os.fstat(fd).st_ino == root_inode:
            raise OSError("simulated parent sync failure")
        return original_fsync(fd)

    monkeypatch.setattr(capture.os, "fsync", fail_root_sync)
    session = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40))
    with pytest.raises(capture.CaptureRefused, match="storage"):
        clock = iter((ISSUED, CHECKED)).__next__
        session.acquire("a" * 64, lambda challenge, history: COMMENT_ID, clock=clock)
    assert (root / capture.BASE).is_dir()  # Uncertain entry is preserved for reconciliation.
    assert not (root / "media-elf-metadata-admission" / "active.marker").read_bytes()
    assert not (root / v3.BASE).exists()
    assert len(list((root / "media-native-reader").iterdir())) == 4
    with pytest.raises(capture.CaptureRefused, match="already_used"):
        session.acquire("a" * 64, lambda challenge, history: COMMENT_ID)
    monkeypatch.setattr(capture.os, "fsync", original_fsync)
    with pytest.raises(capture.CaptureRefused, match="preflight"):
        capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40)).acquire(
            "a" * 64, lambda challenge, history: COMMENT_ID
        )


def test_capture_refetch_drift_leaves_receipt_fenced_without_grant(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    calls = []

    def changed_get(endpoint):
        calls.append(endpoint)
        if len(calls) == 6:
            comment = dict(fetched[endpoint])
            comment["body"] = comment["body"].replace("19:00:00Z", "18:30:00Z")
            return comment
        return fetched[endpoint]

    monkeypatch.setattr(capture.approval, "gh_api_get", changed_get)
    session = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40))
    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        clock = iter((ISSUED, CHECKED, CHECKED)).__next__
        session.acquire("a" * 64, lambda challenge, history: COMMENT_ID, clock=clock)
    assert len(calls) == 6
    assert (root / capture.BASE / capture.RECEIPT).is_file()
    assert not (root / capture.BASE / capture.RECHECK).exists()
    assert not (root / "media-elf-metadata-admission" / "active.marker").read_bytes()
    assert not (root / v3.BASE).exists()
    assert len(list((root / "media-native-reader").iterdir())) == 4
    with pytest.raises(capture.CaptureRefused, match="preflight"):
        capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40)).acquire(
            "a" * 64, lambda challenge, history: COMMENT_ID
        )


def test_capture_persists_the_successful_refetch_before_return(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    clock = iter((ISSUED, CHECKED, CHECKED)).__next__
    lease, snapshot = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40)).acquire(
        "a" * 64, lambda challenge, history: COMMENT_ID, clock=clock
    )
    try:
        durable = json.loads((root / capture.BASE / capture.RECHECK).read_bytes())
        assert durable == {
            "schema": "zelo-media-native-reader-v3-approval-recheck-v1",
            "snapshot": snapshot,
        }
        assert snapshot["checked_utc"] == "2026-10-20T18:00:00Z"
        assert not (root / v3.BASE).exists()
    finally:
        lease.finish(cleanup_confirmed=True)


def test_capture_refetch_sync_failure_fences_without_marker_or_grant(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    original_fsync = capture.os.fsync

    def fail_recheck_sync(fd):
        if os.readlink(f"/proc/self/fd/{fd}").endswith("/recheck.json"):
            raise OSError("simulated recheck sync failure")
        return original_fsync(fd)

    monkeypatch.setattr(capture.os, "fsync", fail_recheck_sync)
    session = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40))
    with pytest.raises(capture.CaptureRefused, match="storage"):
        clock = iter((ISSUED, CHECKED, CHECKED)).__next__
        session.acquire("a" * 64, lambda challenge, history: COMMENT_ID, clock=clock)
    assert (root / capture.BASE / capture.RECEIPT).is_file()
    assert not (root / "media-elf-metadata-admission" / "active.marker").read_bytes()
    assert not (root / v3.BASE).exists()
    assert len(list((root / "media-native-reader").iterdir())) == 4
