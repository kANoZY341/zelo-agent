"""Disposable exact fifth-image cleanup; no Docker or retained-runtime mutation."""

import fcntl
import hashlib
import json
import os
from pathlib import Path

import pytest

from qualification import media_reader_fifth_reconcile as fifth
from scripts import cleanup_media_native_reader_fifth_image as c


@pytest.fixture
def fenced(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    marker = admission / "active.marker"
    marker_raw = b'{"version":1,"lease":"retained","policy":"pinned"}'
    marker.write_bytes(marker_raw)
    marker.chmod(0o600)
    lock = admission / "admission.lock"
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    image_path = root / f"media-native-reader/{fifth.FIFTH}/image-id"
    image_path.parent.mkdir(parents=True, mode=0o700)
    image_path.write_bytes((c.IMAGE_ID + "\n").encode())
    image_path.chmod(0o644)
    pins = {
        "media-elf-metadata-admission/active.marker": (
            hashlib.sha256(marker_raw).hexdigest(),
            0o600,
            4096,
        ),
        f"media-native-reader/{fifth.FIFTH}/image-id": (
            hashlib.sha256(image_path.read_bytes()).hexdigest(),
            0o644,
            256,
        ),
        f"media-native-reader/{fifth.FIFTH}/cases.json": ("a" * 64, 0o600, 4096),
        "media-native-reader-extra-attempt-v3/grant.json": ("b" * 64, 0o600, 4096),
    }
    audits = []

    def audit_held(path, actual_pins):
        assert path == root and actual_pins is pins
        assert marker.read_bytes() == marker_raw
        audits.append(True)
        return {"marker_fenced": True}

    monkeypatch.setattr(c.fifth, "_audit_held", audit_held)
    return root, pins, marker, marker_raw, audits


def image():
    return {
        "Id": c.IMAGE_ID,
        "Architecture": "amd64",
        "Os": "linux",
        "Size": 1735813,
        "RepoTags": [],
        "RepoDigests": [],
        "RootFS": {"Type": "layers", "Layers": ["sha256:" + str(n) * 64 for n in range(8)]},
        "Config": {
            "Labels": {c.recon.LABEL: fifth.LEASE, "org.zelo.media-reader.bundle": c.BUNDLE},
            "Env": c.DEFAULT_ENV,
            "Entrypoint": None,
            "Cmd": None,
            "User": None,
            "WorkingDir": None,
        },
    }


def run(
    root, pins, *, present, remove, dependents=lambda config: (), inspect=lambda config: image()
):
    def discover(kind, config):
        return (c.IMAGE_ID,) if kind == "image" and present[0] else ()

    return c._cleanup(
        root,
        pins,
        discover=discover,
        image_exists=lambda identity, config: present[0],
        dependents=dependents,
        inspect=inspect,
        remove=remove,
        quiet_window=lambda config: ("2026-10-24T00:00:00Z", "2026-10-24T00:00:02Z"),
    )


def test_exact_owned_image_removed_after_durable_pending_and_marker_retained(fenced):
    root, pins, marker, marker_raw, audits = fenced
    present = [True]
    before = []

    def remove(config):
        pending = list((root / c.BASE).glob("cleanup-*/pending.json"))
        assert len(pending) == 1
        assert json.loads(pending[0].read_bytes())["marker_clear_authorized"] is False
        assert marker.read_bytes() == marker_raw
        before.append(pending[0].read_bytes())
        present[0] = False
        return b"Untagged: exact image\n"

    result = run(root, pins, present=present, remove=remove)
    assert len(before) == 1
    assert audits == [True, True, True]
    assert marker.read_bytes() == marker_raw
    assert result["admission_marker_cleared"] is False
    assert result["another_attempt_authorized"] is False
    record = json.loads(Path(result["evidence"]).read_bytes())
    assert record["cleanup_confirmed"] is True
    assert record["marker_fenced"] is True
    assert not list(root.glob("reader-fifth-image-cleanup-*"))


@pytest.mark.parametrize("case", ["missing", "dependent", "wrong_label", "tagged"])
def test_unowned_or_dependency_refuses_without_cleanup(fenced, case):
    root, pins, marker, marker_raw, _ = fenced
    present = [case != "missing"]
    value = image()
    if case == "wrong_label":
        value["Config"]["Labels"][c.recon.LABEL] = "wrong"
    if case == "tagged":
        value["RepoTags"] = ["user/keep:latest"]
    called = []

    def remove(config):
        called.append(True)
        raise AssertionError("must not remove")

    with pytest.raises(fifth.FifthReconciliationRefused):
        run(
            root,
            pins,
            present=present,
            remove=remove,
            dependents=(lambda config: ("a" * 64,)) if case == "dependent" else (lambda config: ()),
            inspect=lambda config: value,
        )
    assert not called
    assert marker.read_bytes() == marker_raw
    assert not (root / c.BASE).exists()


def test_uncertain_removal_consumes_cleanup_record_but_keeps_marker(fenced):
    root, pins, marker, marker_raw, _ = fenced
    present = [True]

    def uncertain(config):
        present[0] = False
        raise TimeoutError("uncertain Docker removal")

    with pytest.raises(TimeoutError):
        run(root, pins, present=present, remove=uncertain)
    assert marker.read_bytes() == marker_raw
    assert list((root / c.BASE).glob("cleanup-*/pending.json"))
    with pytest.raises(fifth.FifthReconciliationRefused, match="inventory"):
        run(root, pins, present=present, remove=uncertain)
    assert not list((root / c.BASE).glob("cleanup-*/completed.json"))


def test_late_resource_refuses_before_remove(fenced):
    root, pins, marker, marker_raw, _ = fenced
    present = [True]
    calls = []

    def dependent(config):
        calls.append(True)
        return ("a" * 64,) if len(calls) == 3 else ()

    with pytest.raises(fifth.FifthReconciliationRefused, match="resource_drift"):
        run(root, pins, present=present, remove=lambda config: b"", dependents=dependent)
    assert marker.read_bytes() == marker_raw
    assert list((root / c.BASE).glob("cleanup-*/pending.json"))


def test_live_lock_and_generic_cli_refusal(fenced, monkeypatch, capsys):
    root, pins, marker, marker_raw, _ = fenced
    fd = os.open(root / "media-elf-metadata-admission/admission.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(fifth.FifthReconciliationRefused, match="live_lock"):
            run(root, pins, present=[True], remove=lambda config: b"")
    finally:
        os.close(fd)
    assert marker.read_bytes() == marker_raw
    monkeypatch.setattr(c, "cleanup", lambda: (_ for _ in ()).throw(ValueError("PRIVATE")))
    monkeypatch.setattr(c.os.sys, "argv", ["cleanup", "--run"])
    assert c.main() == 1
    assert capsys.readouterr().err == "reader_fifth_cleanup_refused\n"


def test_completion_receipt_binds_exact_cleanup_and_rejects_tampering(fenced):
    root, pins, marker, marker_raw, _ = fenced
    present = [True]
    assert not (root / c.BASE).exists()

    def remove(config):
        present[0] = False
        return b"deleted\n"

    result = run(root, pins, present=present, remove=remove)
    digest = c.completed_cleanup(root, pins, c.IMAGE_ID)
    assert digest == result["evidence_sha256"]
    assert marker.read_bytes() == marker_raw
    with pytest.raises(fifth.FifthReconciliationRefused, match="completion_image_id"):
        c.completed_cleanup(root, pins, "sha256:" + "f" * 64)
    completion = Path(result["evidence"])
    data = json.loads(completion.read_bytes())
    data["marker_fenced"] = False
    completion.write_text(json.dumps(data))
    with pytest.raises(fifth.FifthReconciliationRefused, match="completion_binding"):
        c.completed_cleanup(root, pins, c.IMAGE_ID)


def test_cleanup_evidence_sync_failure_keeps_marker_and_does_not_remove(fenced, monkeypatch):
    root, pins, marker, marker_raw, _ = fenced
    sync = c.recon._sync_dir
    calls = []

    def uncertain(path):
        if path == root and (root / c.BASE).exists():
            calls.append(True)
            raise OSError("uncertain cleanup parent fsync")
        return sync(path)

    monkeypatch.setattr(c.recon, "_sync_dir", uncertain)
    with pytest.raises(OSError, match="uncertain cleanup parent fsync"):
        run(root, pins, present=[True], remove=lambda config: pytest.fail("must not remove"))
    assert calls
    assert marker.read_bytes() == marker_raw
