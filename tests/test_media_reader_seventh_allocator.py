"""A seventh directory needs the live one-use v5 gate under the same lease."""

import os
import threading

import pytest

from qualification import media_reader_approval_capture_v5 as capture
from qualification import media_reader_sixth_ledger as sixth
from qualification.admission import AdmissionController
from scripts import qualify_media_native_reader as reader
from tests.test_media_reader_sixth_ledger import retained as sixth_retained


@pytest.fixture
def retained_fixture(tmp_path, monkeypatch):
    return sixth_retained.__wrapped__(tmp_path, monkeypatch)


def test_seventh_directory_requires_issued_live_gate(retained_fixture, monkeypatch):
    root, pins, marker = retained_fixture
    sixth_attempt = root / "media-native-reader/native-reader-proof-sixth-disposable"
    sixth_attempt.mkdir(mode=0o700)
    for name in ("policy.json", "cases.json"):
        path = sixth_attempt / name
        path.write_bytes(b"disposable refused evidence")
        path.chmod(0o600)
    monkeypatch.setattr(
        reader.reconcile,
        "POLICY_SHA256",
        pins[f"media-native-reader/{sixth.first.ATTEMPT_NAME}/policy.json"][0],
    )
    monkeypatch.setattr(
        reader.reconcile,
        "CASES_SHA256",
        pins[f"media-native-reader/{sixth.first.ATTEMPT_NAME}/cases.json"][0],
    )
    lease = AdmissionController(marker.parent).acquire("b" * 64)
    try:
        with pytest.raises(ValueError, match="attempt_limit"):
            reader._attempt_directory(root, extra_grant=True, recovery_version=5)
        with pytest.raises(capture.CaptureRefused, match="attempt_gate"):
            reader._attempt_directory(
                root, extra_grant=True, recovery_version=5, v5_before_mkdir=object()
            )
        assert len(list((root / "media-native-reader").iterdir())) == 6
        with lease.retained():
            gate = object()
            with capture._GATE_LOCK:
                capture._GATES[gate] = (
                    root,
                    lease,
                    lambda: None,
                    os.getpid(),
                    threading.get_ident(),
                )
            attempt = reader._attempt_directory(
                root, extra_grant=True, recovery_version=5, v5_before_mkdir=gate
            )
            assert attempt.is_dir()
            assert len(list((root / "media-native-reader").iterdir())) == 7
            with pytest.raises(capture.CaptureRefused, match="attempt_gate"):
                capture._consume_attempt_gate(root, gate)
    finally:
        lease.finish(cleanup_confirmed=False)
