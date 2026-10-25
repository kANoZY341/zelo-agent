"""Disposable read-only checks; never grants or runs a sixth native proof."""

import hashlib
import json
import os

import pytest

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_sixth_ledger as ledger
from scripts import cleanup_media_native_reader_fifth_image as cleanup


def _raw(value):
    return json.dumps(value, sort_keys=True).encode()


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    policy = {"attempt_limit": 5, "runtime_authority": False}
    proof = {
        "status": "refused",
        "stage": "build",
        "support_release_confirmed": False,
        "runtime_authority": False,
        "build_failure": {"kind": "validation", "code": "reader_container_image_configuration"},
    }
    grant = {
        "runtime_root": str(root),
        "source_head": fifth.SOURCE_HEAD,
        "source_tree": fifth.SOURCE_TREE,
        "attempt_limit_after": 5,
        "additional_attempts": 1,
        "runtime_authority": False,
    }
    structured = {
        f"media-native-reader/{fifth.FIFTH}/policy.json": _raw(policy),
        f"media-native-reader/{fifth.FIFTH}/cases.json": _raw(proof),
        "media-native-reader-extra-attempt-v3/grant.json": _raw(grant),
    }
    pins = {}
    for relative, (_, mode, bound) in ledger.FILE_PINS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = structured.get(relative, b"synthetic retained evidence")
        path.write_bytes(raw)
        path.chmod(mode)
        pins[relative] = (hashlib.sha256(raw).hexdigest(), mode, bound)
    marker = root / "media-elf-metadata-admission/active.marker"
    marker.parent.mkdir(mode=0o700)
    marker.touch(mode=0o600)
    lock = root / "media-elf-metadata-admission/admission.lock"
    lock.touch(mode=0o600)
    for path in (
        root / "media-native-reader" / ledger.first.ATTEMPT_NAME / "docker-config",
        root / "media-native-reader" / fifth.FIFTH / "docker-config",
    ):
        path.mkdir(mode=0o700)
    cleanup_pending = root / cleanup.BASE / ledger.CLEANUP_NAME / "pending.json"
    cleanup_pending.write_bytes(b"synthetic cleanup pending")
    cleanup_pending.chmod(0o600)
    receipt = {
        "schema": "zelo-media-native-reader-fifth-reconciliation-v1",
        "status": "pending-marker-clear",
        "runtime_root": str(root),
        "failed_attempt": fifth.FIFTH,
        "failed_lease": fifth.LEASE,
        "failed_source_head": fifth.SOURCE_HEAD,
        "failed_source_tree": fifth.SOURCE_TREE,
        "proof_sha256": pins[f"media-native-reader/{fifth.FIFTH}/cases.json"][0],
        "policy_sha256": pins[f"media-native-reader/{fifth.FIFTH}/policy.json"][0],
        "marker_sha256": fifth.MARKER_SHA256,
        "cleanup_completion_sha256": pins[ledger.CLEANUP_FILE][0],
        "grant_sha256": pins["media-native-reader-extra-attempt-v3/grant.json"][0],
        "image_id_sha256": pins[f"media-native-reader/{fifth.FIFTH}/image-id"][0],
        "attempts_used": 5,
        "attempt_limit": 5,
        "attempt_reset": False,
        "proof_rewritten": False,
        "proof_deleted": False,
        "original_image_id_present": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
        "docker_container_count_before": 0,
        "docker_container_count_after": 0,
        "docker_image_count_before": 0,
        "docker_image_count_after": 0,
    }
    recon = root / ledger.RECONCILIATION_FILE
    raw = _raw(receipt)
    recon.write_bytes(raw)
    pins[ledger.RECONCILIATION_FILE] = (hashlib.sha256(raw).hexdigest(), 0o600, 65536)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
    monkeypatch.setattr(
        ledger.cleanup,
        "completed_cleanup",
        lambda actual_root, actual_pins, image_id: pins[ledger.CLEANUP_FILE][0],
    )
    return root, pins, marker


def test_exact_five_refusals_and_cleared_fence_are_read_only(retained, monkeypatch):
    root, pins, marker = retained
    monkeypatch.setattr(
        ledger.reconciliation,
        "_discover",
        lambda *args: pytest.fail("read-only ledger must not inspect Docker"),
    )
    result = ledger._audit(root, pins)
    assert result["status"] == "verified-five-refusals-and-reconciled-fence"
    assert len(result["proof_history"]) == 5
    assert result["proof_history"][-1]["attempt"] == fifth.FIFTH
    assert result["attempts_used"] == result["attempt_limit"] == 5
    assert result["marker_cleared"] is True
    assert result["docker_resources_checked"] is False
    assert result["another_attempt_authorized"] is False
    assert marker.read_bytes() == b""
    assert ledger.RETAINED_ROOT != root
    with pytest.raises(ledger.SixthLedgerRefused, match="root"):
        ledger.audit(root)


@pytest.mark.parametrize("change", ["sixth", "marker", "proof", "completion", "reconciliation"])
def test_drift_refuses_without_authorizing_an_attempt(retained, change):
    root, pins, marker = retained
    if change == "sixth":
        (root / "media-native-reader/native-reader-proof-sixth").mkdir(mode=0o700)
    elif change == "marker":
        marker.write_bytes(b"unexpected lease")
    elif change == "proof":
        (root / f"media-native-reader/{fifth.FIFTH}/cases.json").write_bytes(b"changed")
    elif change == "completion":
        (root / ledger.CLEANUP_FILE).write_bytes(b"changed")
    elif change == "reconciliation":
        (root / ledger.RECONCILIATION_FILE).write_bytes(b"changed")
    with pytest.raises((ledger.SixthLedgerRefused, fifth.FifthReconciliationRefused)):
        ledger._audit(root, pins)
    assert not (root / "media-native-reader-extra-attempt-v4").exists()


def test_symlink_marker_and_fifo_lock_refuse_without_blocking(retained):
    root, pins, marker = retained
    marker.unlink()
    marker.symlink_to(root / "missing")
    with pytest.raises(OSError):
        ledger._audit(root, pins)
    marker.unlink()
    marker.touch(mode=0o600)
    lock = root / "media-elf-metadata-admission/admission.lock"
    lock.unlink()
    os.mkfifo(lock, mode=0o600)
    with pytest.raises(fifth.FifthReconciliationRefused, match="lock"):
        ledger._audit(root, pins)


def test_matching_digest_does_not_hide_unauthorized_attempt(retained):
    root, pins, marker = retained
    relative = ledger.RECONCILIATION_FILE
    path = root / relative
    receipt = json.loads(path.read_bytes())
    receipt["another_attempt_authorized"] = True
    raw = _raw(receipt)
    path.write_bytes(raw)
    pins[relative] = (hashlib.sha256(raw).hexdigest(), 0o600, 65536)
    with pytest.raises(ledger.SixthLedgerRefused, match="reconciliation_binding"):
        ledger._audit(root, pins)
    assert marker.read_bytes() == b""


def test_cleanup_receipt_must_link_even_if_files_are_pinned(retained, monkeypatch):
    root, pins, _ = retained
    monkeypatch.setattr(ledger.cleanup, "completed_cleanup", lambda *args: "f" * 64)
    with pytest.raises(ledger.SixthLedgerRefused, match="cleanup_completion"):
        ledger._audit(root, pins)


def test_held_audit_accepts_only_exact_live_marker_without_clearing_it(retained):
    root, pins, marker = retained
    payload = _raw({"version": 1, "lease": "a" * 64, "policy": "b" * 64})
    marker.write_bytes(payload)
    with fifth._locked(root):
        result = ledger._audit_held(root, pins, expected_marker=payload)
    assert result["marker_cleared"] is False
    assert marker.read_bytes() == payload
    with fifth._locked(root):
        with pytest.raises(ledger.SixthLedgerRefused, match="marker"):
            ledger._audit_held(root, pins)
    marker.write_bytes(payload + b" ")
    with fifth._locked(root):
        with pytest.raises(ledger.SixthLedgerRefused, match="marker"):
            ledger._audit_held(root, pins, expected_marker=payload)
