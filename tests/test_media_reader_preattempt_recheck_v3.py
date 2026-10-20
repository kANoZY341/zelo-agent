"""Disposable one-shot decision recheck; never allocates a native attempt."""

import json
from datetime import UTC, datetime

import pytest

from qualification import media_reader_approval_capture_v3 as capture
from qualification import media_reader_extra_attempt_v3 as v3
from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime
from tests.test_media_reader_approval_capture_v3 import (
    CHECKED,
    COMMENT_ID,
    ISSUED,
    RECHECKED,
    _github_fixture,
)


def _session(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.preflight, "observe", lambda _: None)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    session = capture.ApprovalCapture(root, expected_source=("e" * 40, "f" * 40))
    clock = iter((ISSUED, CHECKED, RECHECKED, RECHECKED)).__next__
    lease, snapshot = session.acquire("a" * 64, lambda challenge, history: COMMENT_ID, clock=clock)
    return root, fetched, session, lease, snapshot


def test_preattempt_recheck_persists_same_decision_once_without_proof(tmp_path, monkeypatch):
    root, _, session, lease, original = _session(tmp_path, monkeypatch)
    try:
        fresh = session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
        assert fresh["comment_body_sha256"] == original["comment_body_sha256"]
        assert json.loads((root / capture.BASE / "attempt-recheck.json").read_bytes()) == {
            "schema": "zelo-media-native-reader-v3-attempt-recheck-v1",
            "snapshot": fresh,
        }
        assert len(list((root / "media-native-reader").iterdir())) == 4
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_used"):
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
    finally:
        lease.finish(cleanup_confirmed=True)


def test_preattempt_changed_comment_consumes_recheck_without_proof(tmp_path, monkeypatch):
    root, fetched, session, lease, _ = _session(tmp_path, monkeypatch)
    endpoint = f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"
    fetched[endpoint]["body"] = fetched[endpoint]["body"].replace("19:00:00Z", "18:30:00Z")
    try:
        with pytest.raises(capture.approval.ApprovalRefused, match="comment_drift"):
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
        assert not (root / capture.BASE / "attempt-recheck.json").exists()
        assert len(list((root / "media-native-reader").iterdir())) == 4
    finally:
        lease.finish(cleanup_confirmed=True)


def test_preattempt_refuses_finished_lease_without_network(tmp_path, monkeypatch):
    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    lease.finish(cleanup_confirmed=True)
    monkeypatch.setattr(
        capture.approval, "gh_api_get", lambda endpoint: pytest.fail("network after lease finish")
    )
    with pytest.raises(capture.CaptureRefused, match="attempt_lease"):
        session.recheck_before_attempt(
            lease, clock=lambda: datetime(2026, 10, 20, 18, 2, tzinfo=UTC)
        )
    assert not (root / capture.BASE / "attempt-recheck.json").exists()


def test_preattempt_sync_uncertainty_fences_without_attempt(tmp_path, monkeypatch):
    import os

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    original_fsync = capture.os.fsync

    def fail_attempt_recheck(fd):
        if os.readlink(f"/proc/self/fd/{fd}").endswith("/attempt-recheck.json"):
            raise OSError("simulated sync failure")
        return original_fsync(fd)

    monkeypatch.setattr(capture.os, "fsync", fail_attempt_recheck)
    try:
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_storage"):
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
        assert (root / capture.BASE / capture.ATTEMPT_RECHECK).exists()
        assert len(list((root / "media-native-reader").iterdir())) == 4
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_used"):
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
    finally:
        lease.finish(cleanup_confirmed=True)


def test_finish_waits_for_live_preattempt_recheck(tmp_path, monkeypatch):
    import threading

    root, fetched, session, lease, _ = _session(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    outcome = []

    def stalled_get(endpoint):
        entered.set()
        if not release.wait(2):
            raise AssertionError("stalled test fetch")
        return fetched[endpoint]

    monkeypatch.setattr(capture.approval, "gh_api_get", stalled_get)

    def recheck():
        try:
            outcome.append(session.recheck_before_attempt(lease, clock=lambda: RECHECKED))
        except BaseException as exc:
            outcome.append(exc)

    def finish():
        lease.finish(cleanup_confirmed=True)
        finished.set()

    worker = threading.Thread(target=recheck)
    cleanup = threading.Thread(target=finish)
    worker.start()
    try:
        assert entered.wait(2)
        cleanup.start()
        assert not finished.wait(0.05)
    finally:
        release.set()
        worker.join(2)
        if cleanup.ident is not None:
            cleanup.join(2)
    assert finished.is_set()
    assert len(outcome) == 1 and isinstance(outcome[0], dict)
    assert (root / capture.BASE / capture.ATTEMPT_RECHECK).is_file()
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_concurrent_recheck_cannot_retry_after_first_fetch_failure(tmp_path, monkeypatch):
    import inspect
    import sys
    import threading

    root, fetched, session, lease, _ = _session(tmp_path, monkeypatch)
    first_paused = threading.Event()
    resume_first = threading.Event()
    outcomes = {}
    source, first_line = inspect.getsourcelines(capture.ApprovalCapture.recheck_before_attempt)
    claim_line = next(
        first_line + index
        for index, line in enumerate(source)
        if line.strip() == "self._attempt_rechecked = True"
    )

    def trace(frame, event, arg):
        if (
            event == "line"
            and frame.f_code is capture.ApprovalCapture.recheck_before_attempt.__code__
            and frame.f_lineno == claim_line
        ):
            sys.settrace(None)
            first_paused.set()
            assert resume_first.wait(2)
        return trace

    def get(endpoint):
        if threading.current_thread().name == "first-recheck":
            raise OSError("first fetch failed")
        return fetched[endpoint]

    monkeypatch.setattr(capture.approval, "gh_api_get", get)

    def invoke(label, *, traced=False):
        if traced:
            sys.settrace(trace)
        try:
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
            outcomes[label] = "succeeded"
        except Exception as exc:
            outcomes[label] = str(exc)
        finally:
            sys.settrace(None)

    first = threading.Thread(
        target=invoke, args=("first",), kwargs={"traced": True}, name="first-recheck"
    )
    second = threading.Thread(target=invoke, args=("second",), name="second-recheck")
    first.start()
    try:
        assert first_paused.wait(2)
        second.start()
    finally:
        resume_first.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
        lease.finish(cleanup_confirmed=True)
    assert "fetch_failed" in outcomes["first"]
    assert "attempt_recheck_used" in outcomes["second"]
    assert not (root / capture.BASE / capture.ATTEMPT_RECHECK).exists()
    assert len(list((root / "media-native-reader").iterdir())) == 4
