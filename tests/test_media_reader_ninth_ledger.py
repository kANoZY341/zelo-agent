"""Offline eighth-refusal ledger: fixed files only, no Docker or grant."""

import hashlib
import importlib
import json

import pytest

from qualification.admission import AdmissionController


def _raw(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


@pytest.fixture
def retained(tmp_path, monkeypatch):
    ledger = importlib.import_module("qualification.media_reader_ninth_ledger")
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    case = {
        "status": "refused",
        "stage": "matrix",
        "matrix_failure": {
            "kind": "validation",
            "code": "reader_runtime_probe",
            "cause_code": "reader_container_limits",
            "limit_differences": [{"field": "OomKillDisable", "observed_kind": "null"}],
        },
        "support_release_confirmed": False,
        "runtime_authority": False,
    }
    grant = {
        "schema": "zelo-media-native-reader-extra-attempt-v6",
        "status": "one-additional-attempt-granted",
        "runtime_root": str(root),
        "source_head": ledger.SOURCE_HEAD,
        "source_tree": ledger.SOURCE_TREE,
        "approval_comment_id": 6455367560,
        "attempts_used_before": 7,
        "attempt_limit_after": 8,
        "additional_attempts": 1,
        "attempt_reset": False,
        "proof_rewritten": False,
        "proof_deleted": False,
        "network": "none",
        "runtime_authority": False,
    }
    grant_raw = _raw(grant)
    structured = {
        f"media-native-reader/{ledger.ATTEMPT}/cases.json": _raw(case),
        f"media-native-reader/{ledger.ATTEMPT}/policy.json": _raw(
            {"attempt_limit": 8, "runtime_authority": False}
        ),
        "media-native-reader-extra-attempt-v6/grant.json": grant_raw,
        "media-native-reader-extra-attempt-v6/seal.json": _raw(
            {
                "grant_sha256": hashlib.sha256(grant_raw).hexdigest(),
                "source_head": ledger.SOURCE_HEAD,
                "source_tree": ledger.SOURCE_TREE,
            }
        ),
    }
    pins = {}
    for relative, (_, mode, bound) in ledger.PINS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = structured.get(relative, b"historical pinned evidence")
        path.write_bytes(raw)
        path.chmod(mode)
        pins[relative] = (hashlib.sha256(raw).hexdigest(), mode, bound)
    for name in (ledger.historical.first.ATTEMPT_NAME, ledger.fifth.FIFTH):
        (root / "media-native-reader" / name / "docker-config").mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(exist_ok=True, mode=0o700)
    (admission / "admission.lock").touch(mode=0o600)
    marker = admission / "active.marker"
    marker.touch(mode=0o600)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
    monkeypatch.setattr(ledger, "RETAINED_ROOT", root)
    monkeypatch.setattr(ledger, "PINS", pins)
    return ledger, root, marker


def test_eight_exact_refusals_are_read_only_and_grant_no_authority(retained):
    ledger, root, marker = retained
    result = ledger.audit(root)
    assert result["status"] == "verified-eight-terminal-refusals"
    assert result["attempts_used"] == result["attempt_limit"] == 8
    assert len(result["proof_history"]) == 8
    assert result["proof_history"][-1]["attempt"] == "native-reader-proof-plhsvu3q"
    assert (
        result["proof_history"][-1]["proof_sha256"]
        == ledger.PINS["media-native-reader/native-reader-proof-plhsvu3q/cases.json"][0]
    )
    assert result["marker_cleared"] is True
    assert result["docker_resources_checked"] is False
    assert result["another_attempt_authorized"] is False
    assert result["runtime_authority"] is False
    assert marker.read_bytes() == b""
    assert not (root / "media-native-reader-extra-attempt-v7").exists()


@pytest.mark.parametrize(
    "change",
    ["case", "grant", "fifth_pending", "extra_proof", "extra_approval", "future_grant", "marker"],
)
def test_drift_or_ninth_artifact_refuses(retained, change):
    ledger, root, marker = retained
    if change == "case":
        (root / f"media-native-reader/{ledger.ATTEMPT}/cases.json").write_bytes(b"changed")
    elif change == "grant":
        (root / "media-native-reader-extra-attempt-v6/grant.json").write_bytes(b"changed")
    elif change == "fifth_pending":
        (
            root / ledger.historical.cleanup.BASE / ledger.historical.CLEANUP_NAME / "pending.json"
        ).write_bytes(b"changed")
    elif change == "extra_proof":
        (root / "media-native-reader/native-reader-proof-ninth").mkdir(mode=0o700)
    elif change == "extra_approval":
        (root / "media-native-reader-extra-attempt-v7").mkdir(mode=0o700)
    elif change == "future_grant":
        (root / "media-native-reader-extra-attempt-v8").mkdir(mode=0o700)
    else:
        marker.write_bytes(b"occupied")
    with pytest.raises(ledger.NinthLedgerRefused):
        ledger.audit(root)
    assert not (root / "media-native-reader-extra-attempt-v7/grant.json").exists()


def test_public_audit_refuses_any_other_root(tmp_path):
    ledger = importlib.import_module("qualification.media_reader_ninth_ledger")
    with pytest.raises(ledger.NinthLedgerRefused, match="root"):
        ledger.audit(tmp_path)


def test_live_lease_rechecks_eighth_pins_under_occupied_marker(retained):
    ledger, root, marker = retained
    before = ledger.audit(root)
    lease = AdmissionController(root / "media-elf-metadata-admission").acquire("a" * 64)
    try:
        with pytest.raises(ledger.NinthLedgerRefused, match="live_binding"):
            ledger._audit_held_live(root, lease, before)
        with lease.retained():
            assert ledger._audit_held_live(root, lease, before) == before
            assert marker.stat().st_size > 0
            (root / f"media-native-reader/{ledger.ATTEMPT}/cases.json").write_bytes(b"changed")
            with pytest.raises(ledger.NinthLedgerRefused):
                ledger._audit_held_live(root, lease, before)
    finally:
        lease.finish(cleanup_confirmed=True)


def test_inflight_inventory_accepts_only_pinned_receipt_stage(retained):
    ledger, root, _ = retained
    base = "media-native-reader-run-approval-v7"
    receipt = root / base / "receipt.json"
    receipt.parent.mkdir(mode=0o700)
    receipt.write_bytes(b"one bounded receipt")
    receipt.chmod(0o600)
    pins = {(base, "receipt.json"): hashlib.sha256(receipt.read_bytes()).hexdigest()}
    with ledger.fifth._locked(root):
        with pytest.raises(ledger.NinthLedgerRefused):
            ledger._audit_held(root)
        assert ledger._audit_held_progress(root, pins)["attempts_used"] == 8
        receipt.write_bytes(b"changed receipt")
        with pytest.raises(ledger.NinthLedgerRefused):
            ledger._audit_held_progress(root, pins)
        pins[(base, "recheck.json")] = "a" * 64
        with pytest.raises(ledger.NinthLedgerRefused):
            ledger._audit_held_progress(root, pins)
