"""Offline lock handoff contract for a future reviewed Media admission path."""

import fcntl
import os
from pathlib import Path

import pytest

from qualification.admission import AdmissionController, AdmissionDenied

DIGEST = "a" * 64


def admission(tmp_path: Path) -> Path:
    root = tmp_path / "admission"
    root.mkdir(mode=0o700)
    (root / "admission.lock").touch(mode=0o600)
    (root / "active.marker").touch(mode=0o600)
    return root


def test_writer_runs_under_same_lock_before_durable_marker(tmp_path):
    root = admission(tmp_path)
    events = []

    def writer():
        contender = os.open(root / "admission.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
        assert (root / "active.marker").read_bytes() == b""
        events.append("written")

    lease = AdmissionController(root).acquire_after(DIGEST, writer)
    try:
        assert events == ["written"]
        assert lease.policy_digest == DIGEST
        assert (root / "active.marker").stat().st_size > 0
        with pytest.raises(AdmissionDenied, match="runtime_unavailable"):
            AdmissionController(root).acquire(DIGEST)
    finally:
        lease.finish(cleanup_confirmed=True)
    assert (root / "active.marker").read_bytes() == b""


def test_fenced_marker_refuses_before_writer(tmp_path):
    root = admission(tmp_path)
    (root / "active.marker").write_text("uncertain")
    with pytest.raises(AdmissionDenied, match="runtime_fenced"):
        AdmissionController(root).acquire_after(DIGEST, lambda: pytest.fail("writer ran"))
    assert (root / "active.marker").read_text() == "uncertain"


def test_failed_writer_does_not_allocate_lease(tmp_path):
    root = admission(tmp_path)
    with pytest.raises(RuntimeError, match="uncertain_write"):
        AdmissionController(root).acquire_after(
            DIGEST, lambda: (_ for _ in ()).throw(RuntimeError("uncertain_write"))
        )
    assert (root / "active.marker").read_bytes() == b""
    lease = AdmissionController(root).acquire(DIGEST)
    lease.finish(cleanup_confirmed=True)
