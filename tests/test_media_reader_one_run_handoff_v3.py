"""Disposable allocation handoff; no native Docker or retained runtime."""

import fcntl
import os

import pytest

from qualification import media_reader_approval_capture_v3 as capture
from tests.test_media_reader_approval_capture_v3 import (
    COMMENT_ID,
    RECHECKED,
)
from tests.test_media_reader_preattempt_recheck_v3 import _session


def test_allocation_callback_runs_once_with_fresh_evidence_and_held_lock(tmp_path, monkeypatch):
    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    calls = []

    def allocate(runtime, gate):
        assert type(gate) is object
        assert runtime == root
        assert (root / capture.BASE / capture.ATTEMPT_RECHECK).is_file()
        fd = os.open(root / "media-elf-metadata-admission" / "admission.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        calls.append("allocated")
        return "disposable-attempt"

    try:
        evidence, value = session.recheck_before_attempt(
            lease, allocate=allocate, clock=lambda: RECHECKED
        )
        assert evidence["comment_id"] == COMMENT_ID
        assert value == "disposable-attempt"
        assert calls == ["allocated"]
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_used"):
            session.recheck_before_attempt(lease, allocate=allocate, clock=lambda: RECHECKED)
    finally:
        lease.finish(cleanup_confirmed=True)
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_changed_comment_never_reaches_allocation(tmp_path, monkeypatch):
    root, fetched, session, lease, _ = _session(tmp_path, monkeypatch)
    endpoint = f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"
    fetched[endpoint]["body"] = fetched[endpoint]["body"].replace("19:00:00Z", "18:30:00Z")
    try:
        with pytest.raises(capture.approval.ApprovalRefused, match="comment_drift"):
            session.recheck_before_attempt(
                lease,
                allocate=lambda _, check: pytest.fail("allocation reached"),
                clock=lambda: RECHECKED,
            )
    finally:
        lease.finish(cleanup_confirmed=True)
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_expiry_before_allocation_consumes_recheck_without_attempt(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    times = iter((RECHECKED, datetime(2026, 10, 20, 19, 0, tzinfo=UTC)))
    try:
        with pytest.raises(capture.CaptureRefused, match="attempt_expired"):
            session.recheck_before_attempt(
                lease,
                allocate=lambda _, check: pytest.fail("allocation reached"),
                clock=times.__next__,
            )
        assert (root / capture.BASE / capture.ATTEMPT_RECHECK).is_file()
        assert len(list((root / "media-native-reader").iterdir())) == 4
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_used"):
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
    finally:
        lease.finish(cleanup_confirmed=True)


def test_fifth_directory_requires_live_recheck_and_final_clock(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(root, extra_grant=True, recovery_version=3)
    try:
        _, attempt = session.recheck_before_attempt(
            lease,
            allocate=lambda runtime, check: reader._attempt_directory(
                runtime, extra_grant=True, recovery_version=3, v3_before_mkdir=check
            ),
            clock=lambda: RECHECKED,
        )
        assert attempt.parent == root / "media-native-reader"
        assert attempt.name.startswith(reader.PREFIX)
        assert len(list(attempt.parent.iterdir())) == 5
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            reader._attempt_directory(root, extra_grant=True, recovery_version=3)
    finally:
        lease.finish(cleanup_confirmed=False)


def test_saved_grant_and_caller_callback_cannot_allocate_fifth(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, _, lease, _ = _session(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            reader._attempt_directory(
                root, extra_grant=True, recovery_version=3, v3_before_mkdir=lambda: None
            )
        assert len(list((root / "media-native-reader").iterdir())) == 4
    finally:
        lease.finish(cleanup_confirmed=True)


def test_gate_cannot_be_saved_for_later_allocation(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    saved = []
    try:
        session.recheck_before_attempt(
            lease, allocate=lambda runtime, gate: saved.append(gate), clock=lambda: RECHECKED
        )
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            reader._attempt_directory(
                root, extra_grant=True, recovery_version=3, v3_before_mkdir=saved[0]
            )
        assert len(list((root / "media-native-reader").iterdir())) == 4
    finally:
        lease.finish(cleanup_confirmed=True)


def test_expiry_during_attempt_validation_refuses_before_mkdir(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    times = iter((RECHECKED, RECHECKED, datetime(2026, 10, 20, 19, 0, tzinfo=UTC)))
    try:
        with pytest.raises(capture.CaptureRefused, match="attempt_expired"):
            session.recheck_before_attempt(
                lease,
                allocate=lambda runtime, gate: reader._attempt_directory(
                    runtime, extra_grant=True, recovery_version=3, v3_before_mkdir=gate
                ),
                clock=times.__next__,
            )
        assert len(list((root / "media-native-reader").iterdir())) == 4
        assert not capture._LIVE_ATTEMPT_GATES
    finally:
        lease.finish(cleanup_confirmed=True)
