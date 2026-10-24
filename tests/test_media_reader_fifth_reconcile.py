"""Disposable read-only verification of the consumed fifth Media proof."""

import fcntl
import hashlib
import json
import os

import pytest

from qualification import media_reader_fifth_reconcile as fifth


def _raw(value):
    return json.dumps(value, sort_keys=True).encode()


@pytest.fixture
def retained(tmp_path):
    root = tmp_path / "retained"
    root.mkdir(mode=0o700)
    policy = {"attempt_limit": 5, "runtime_authority": False}
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    structured = {
        f"media-native-reader/{fifth.FIFTH}/policy.json": _raw(policy),
        f"media-native-reader/{fifth.FIFTH}/cases.json": _raw(
            {
                "status": "refused",
                "stage": "build",
                "support_release_confirmed": False,
                "runtime_authority": False,
                "build_failure": {
                    "kind": "validation",
                    "code": "reader_container_image_configuration",
                },
            }
        ),
        "media-elf-metadata-admission/active.marker": _raw(
            {"version": 1, "lease": fifth.LEASE, "policy": digest}
        ),
        "media-native-reader-extra-attempt-v3/grant.json": _raw(
            {
                "runtime_root": str(root),
                "source_head": fifth.SOURCE_HEAD,
                "source_tree": fifth.SOURCE_TREE,
                "attempt_limit_after": 5,
                "additional_attempts": 1,
                "runtime_authority": False,
            }
        ),
    }
    pins = {}
    for relative, (_, mode, bound) in fifth.FILE_PINS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = structured.get(relative, b"retained synthetic evidence")
        path.write_bytes(data)
        path.chmod(mode)
        pins[relative] = (hashlib.sha256(data).hexdigest(), mode, bound)
    for path in (
        root / "media-native-reader" / fifth.first.ATTEMPT_NAME / "docker-config",
        root / "media-native-reader" / fifth.FIFTH / "docker-config",
    ):
        path.mkdir(mode=0o700)
    lock = root / "media-elf-metadata-admission/admission.lock"
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    for directory in root.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o700)
    return root, pins


def test_read_only_audit_preserves_marker_and_evidence(retained):
    root, pins = retained
    marker = root / "media-elf-metadata-admission/active.marker"
    before = marker.read_bytes()
    result = fifth._audit(root, pins)
    assert result["status"] == "verified-retained-fifth-refusal-filesystem"
    assert result["attempts_used"] == 5
    assert result["marker_fenced"] is True
    assert result["docker_resources_checked"] is False
    assert result["marker_clear_authorized"] is False
    assert result["another_attempt_authorized"] is False
    assert marker.read_bytes() == before
    assert len(list((root / "media-native-reader").iterdir())) == 5


def test_changed_build_artifact_refuses_without_marker_change(retained):
    root, pins = retained
    marker = root / "media-elf-metadata-admission/active.marker"
    before = marker.read_bytes()
    (root / f"media-native-reader/{fifth.FIFTH}/build/Dockerfile").write_bytes(b"changed")
    with pytest.raises(fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_pin"):
        fifth._audit(root, pins)
    assert marker.read_bytes() == before


def test_extra_attempt_or_fifth_file_refuses(retained):
    root, pins = retained
    (root / "media-native-reader/native-reader-proof-sixth").mkdir(mode=0o700)
    with pytest.raises(fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_inventory"):
        fifth._audit(root, pins)
    (root / "media-native-reader/native-reader-proof-sixth").rmdir()
    (root / "media-native-reader" / fifth.FIFTH / "surprise").write_bytes(b"x")
    with pytest.raises(fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_inventory"):
        fifth._audit(root, pins)


def test_marker_drift_and_live_lock_refuse(retained):
    root, pins = retained
    lock = root / "media-elf-metadata-admission/admission.lock"
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(
            fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_live_lock"
        ):
            fifth._audit(root, pins)
    finally:
        os.close(fd)
    marker = root / "media-elf-metadata-admission/active.marker"
    marker.write_bytes(b'{"wrong":true}')
    with pytest.raises(fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_pin"):
        fifth._audit(root, pins)


def test_symlinked_artifact_and_wrong_root_refuse(retained, tmp_path):
    root, pins = retained
    with pytest.raises(fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_root"):
        fifth.audit(root)
    artifact = root / f"media-native-reader/{fifth.FIFTH}/build/00.binary.reference"
    artifact.unlink()
    artifact.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        fifth._audit(root, pins)


def test_matching_digest_cannot_hide_wrong_marker_binding(retained):
    root, pins = retained
    relative = "media-elf-metadata-admission/active.marker"
    path = root / relative
    value = json.loads(path.read_bytes())
    value["lease"] = "f" * 64
    raw = _raw(value)
    path.write_bytes(raw)
    _, mode, bound = pins[relative]
    pins[relative] = (hashlib.sha256(raw).hexdigest(), mode, bound)
    with pytest.raises(
        fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_marker_binding"
    ):
        fifth._audit(root, pins)


def test_matching_digest_cannot_hide_wrong_grant_source(retained):
    root, pins = retained
    relative = "media-native-reader-extra-attempt-v3/grant.json"
    path = root / relative
    value = json.loads(path.read_bytes())
    value["source_head"] = "0" * 40
    raw = _raw(value)
    path.write_bytes(raw)
    _, mode, bound = pins[relative]
    pins[relative] = (hashlib.sha256(raw).hexdigest(), mode, bound)
    with pytest.raises(
        fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_grant_binding"
    ):
        fifth._audit(root, pins)


def test_attempt_created_at_lock_acquisition_refuses(retained, monkeypatch):
    from contextlib import contextmanager

    root, pins = retained
    actual_lock = fifth._locked

    @contextmanager
    def add_before_lock(path):
        (root / "media-native-reader/native-reader-proof-sixth").mkdir(mode=0o700)
        with actual_lock(path):
            yield

    monkeypatch.setattr(fifth, "_locked", add_before_lock)
    with pytest.raises(fifth.FifthReconciliationRefused, match="reader_fifth_reconcile_inventory"):
        fifth._audit(root, pins)
