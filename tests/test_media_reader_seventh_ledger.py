"""Offline six-refusal preflight; no Docker or native process is invoked."""

import pytest

from qualification import media_reader_seventh_ledger as ledger


def test_six_refusals_and_cleanup_are_pinned_without_grant(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(parents=True, mode=0o700)
    (admission / "admission.lock").touch(mode=0o600)
    marker = admission / "active.marker"
    marker.touch(mode=0o600)
    calls = []

    def completion(path):
        assert path == root
        calls.append(True)
        return ledger.COMPLETION_SHA256

    monkeypatch.setattr(ledger.cleanup, "_completion_held", completion)
    monkeypatch.setattr(
        ledger.cleanup, "_snapshot", lambda *_args, **_kwargs: pytest.fail("no Docker inspection")
    )
    result = ledger._audit(root)
    assert len(calls) == 2
    assert result == {
        "status": "verified-six-refusals-and-exact-image-cleanup",
        "proof_history": ledger._history(),
        "attempts_used": 6,
        "attempt_limit": 6,
        "cleanup_completion_sha256": ledger.COMPLETION_SHA256,
        "marker_cleared": True,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    assert result["proof_history"][-1] == {
        "attempt": ledger.cleanup.ATTEMPT,
        "policy_sha256": ledger.cleanup.PINS[
            f"media-native-reader/{ledger.cleanup.ATTEMPT}/policy.json"
        ],
        "proof_sha256": ledger.cleanup.PINS[
            f"media-native-reader/{ledger.cleanup.ATTEMPT}/cases.json"
        ],
        "source_head": ledger.cleanup.SOURCE_HEAD,
        "source_tree": ledger.cleanup.SOURCE_TREE,
    }
    assert marker.read_bytes() == b""
    assert not (root / "media-native-reader-extra-attempt-v5").exists()


def test_changed_cleanup_or_marker_refuses(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(parents=True, mode=0o700)
    (admission / "admission.lock").touch(mode=0o600)
    marker = admission / "active.marker"
    marker.touch(mode=0o600)
    monkeypatch.setattr(ledger.cleanup, "_completion_held", lambda _: "0" * 64)
    with pytest.raises(ledger.SeventhLedgerRefused, match="completion"):
        ledger._audit(root)
    marker.write_bytes(b"active")
    with pytest.raises(ledger.SeventhLedgerRefused, match="marker"):
        ledger._audit(root)


def test_completion_drift_during_locked_audit_refuses(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(parents=True, mode=0o700)
    (admission / "admission.lock").touch(mode=0o600)
    (admission / "active.marker").touch(mode=0o600)
    completions = iter((ledger.COMPLETION_SHA256, "0" * 64))
    monkeypatch.setattr(ledger.cleanup, "_completion_held", lambda _: next(completions))
    with pytest.raises(ledger.SeventhLedgerRefused, match="completion_drift"):
        ledger._audit(root)
    assert not (root / "media-native-reader-extra-attempt-v5").exists()


def test_public_audit_is_fixed_to_retained_root(tmp_path):
    with pytest.raises(ledger.SeventhLedgerRefused, match="root"):
        ledger.audit(tmp_path)
