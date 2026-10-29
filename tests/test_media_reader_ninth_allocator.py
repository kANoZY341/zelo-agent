"""A ninth directory requires one live v7 gate under the same lease."""

import os
import threading

import pytest

from qualification import media_reader_approval_capture_v7 as capture
from qualification.admission import AdmissionController
from scripts import qualify_media_native_reader as reader
from tests.test_media_reader_ninth_ledger import retained as retained_fixture


@pytest.fixture
def retained(tmp_path, monkeypatch):
    return retained_fixture.__wrapped__(tmp_path, monkeypatch)


def test_ninth_directory_cannot_use_saved_grant_or_replay_gate(retained, monkeypatch):
    ledger, root, marker = retained
    first = ledger.historical.first.ATTEMPT_NAME
    monkeypatch.setattr(
        reader.reconcile,
        "POLICY_SHA256",
        ledger.PINS[f"media-native-reader/{first}/policy.json"][0],
    )
    monkeypatch.setattr(
        reader.reconcile,
        "CASES_SHA256",
        ledger.PINS[f"media-native-reader/{first}/cases.json"][0],
    )
    lease = AdmissionController(marker.parent).acquire("b" * 64)
    try:
        with pytest.raises(ValueError, match="attempt_limit"):
            reader._attempt_directory(root, extra_grant=True, recovery_version=7)
        with pytest.raises(capture.CaptureRefused, match="attempt_gate"):
            reader._attempt_directory(
                root, extra_grant=True, recovery_version=7, v7_before_mkdir=object()
            )
        assert len(list((root / "media-native-reader").iterdir())) == 8
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
                root, extra_grant=True, recovery_version=7, v7_before_mkdir=gate
            )
            assert attempt.is_dir()
            assert len(list((root / "media-native-reader").iterdir())) == 9
            with pytest.raises(capture.CaptureRefused, match="attempt_gate"):
                capture._consume_attempt_gate(root, gate)
    finally:
        lease.finish(cleanup_confirmed=False)
