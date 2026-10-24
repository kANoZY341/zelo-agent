"""Disposable fifth-fence reconciliation; never run against the retained runtime."""

import fcntl
import hashlib
import json
import os
from pathlib import Path

import pytest

from qualification import media_reader_fifth_reconcile as fifth
from scripts import cleanup_media_native_reader_fifth_image as c
from scripts import reconcile_media_native_reader_fifth as r


@pytest.fixture
def fenced(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    marker = admission / "active.marker"
    marker_raw = b'{"version": 1, "lease": "retained", "policy": "pinned"}'
    marker.write_bytes(marker_raw)
    marker.chmod(0o600)
    lock = admission / "admission.lock"
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    image_id = root / f"media-native-reader/{fifth.FIFTH}/image-id"
    image_id.parent.mkdir(parents=True, mode=0o700)
    image_id.write_bytes((c.IMAGE_ID + "\n").encode())
    image_id.chmod(0o644)
    pins = {
        "media-elf-metadata-admission/active.marker": (
            hashlib.sha256(marker_raw).hexdigest(),
            0o600,
            4096,
        ),
        f"media-native-reader/{fifth.FIFTH}/image-id": (
            hashlib.sha256(image_id.read_bytes()).hexdigest(),
            0o644,
            256,
        ),
        f"media-native-reader/{fifth.FIFTH}/policy.json": ("b" * 64, 0o600, 4096),
        f"media-native-reader/{fifth.FIFTH}/cases.json": ("c" * 64, 0o600, 4096),
        "media-native-reader-extra-attempt-v3/grant.json": ("d" * 64, 0o600, 4096),
        "media-native-reader-run-approval-v3/receipt.json": ("e" * 64, 0o600, 4096),
        "media-native-reader-run-approval-v3/attempt-recheck.json": ("f" * 64, 0o600, 4096),
    }
    audits = []

    def audit_held(path, actual_pins):
        assert path == root and actual_pins is pins
        assert marker.read_bytes() == marker_raw
        audits.append(True)
        return {"marker_fenced": True}

    monkeypatch.setattr(r.fifth, "_audit_held", audit_held)
    return root, pins, marker, marker_raw, audits


def empty(kind, config):
    assert kind in ("container", "image")
    assert Path(config).name.startswith("reader-fifth-reconcile-docker-")
    return ()


def absent(image_id, config):
    assert image_id == c.IMAGE_ID
    return False


def quiet(config):
    assert Path(config).name.startswith("reader-fifth-reconcile-docker-")
    return "2026-10-24T00:00:00Z", "2026-10-24T00:00:02Z"


def test_pins_resources_and_evidence_before_clear(fenced, monkeypatch):
    root, pins, marker, marker_raw, audits = fenced
    before = []
    original_truncate = r.os.ftruncate

    def truncate(fd, size):
        base = root / r.RECON_BASE
        records = list(base.glob("reconcile-*/pending.json"))
        assert len(records) == 1 and records[0].stat().st_size > 0
        value = json.loads(records[0].read_bytes())
        assert value["attempts_used"] == value["attempt_limit"] == 5
        assert value["marker_sha256"] == hashlib.sha256(marker_raw).hexdigest()
        assert value["another_attempt_authorized"] is False
        assert audits == [True, True, True]
        before.append(marker.read_bytes())
        return original_truncate(fd, size)

    monkeypatch.setattr(r.os, "ftruncate", truncate)
    result = r._reconcile(
        root,
        pins,
        discover=empty,
        image_exists=absent,
        quiet_window=quiet,
        completed_cleanup=lambda *_: "e" * 64,
    )
    assert before == [marker_raw]
    assert marker.read_bytes() == b""
    assert result["admission_marker_cleared"] is True
    assert result["another_attempt_authorized"] is False
    assert result["runtime_authority"] is False
    assert not list(root.glob("reader-fifth-reconcile-docker-*"))


@pytest.mark.parametrize("presence", ["container", "image", "original_image"])
def test_live_resource_refuses_before_record_or_clear(fenced, presence):
    root, pins, marker, marker_raw, _ = fenced

    def discover(kind, config):
        if kind == presence:
            return ("a" * 64,)
        return ()

    def image_exists(image_id, config):
        return presence == "original_image"

    with pytest.raises(fifth.FifthReconciliationRefused, match="resources_present"):
        r._reconcile(
            root,
            pins,
            discover=discover,
            image_exists=image_exists,
            quiet_window=quiet,
            completed_cleanup=lambda *_: "e" * 64,
        )
    assert marker.read_bytes() == marker_raw
    assert not (root / r.RECON_BASE).exists()


def test_daemon_busy_or_uncertain_evidence_keeps_marker(fenced, monkeypatch):
    root, pins, marker, marker_raw, _ = fenced

    def busy(config):
        raise ValueError("reader_reconcile_daemon_busy")

    with pytest.raises(ValueError, match="daemon_busy"):
        r._reconcile(
            root,
            pins,
            discover=empty,
            image_exists=absent,
            quiet_window=busy,
            completed_cleanup=lambda *_: "e" * 64,
        )
    assert marker.read_bytes() == marker_raw
    assert not (root / r.RECON_BASE).exists()

    def uncertain(path, value):
        raise OSError("uncertain fsync")

    monkeypatch.setattr(r.historical, "_write_pending", uncertain)
    with pytest.raises(OSError, match="uncertain fsync"):
        r._reconcile(
            root,
            pins,
            discover=empty,
            image_exists=absent,
            quiet_window=quiet,
            completed_cleanup=lambda *_: "e" * 64,
        )
    assert marker.read_bytes() == marker_raw
    assert list((root / r.RECON_BASE).glob("reconcile-*"))
    with pytest.raises(fifth.FifthReconciliationRefused, match="inventory"):
        r._reconcile(
            root,
            pins,
            discover=empty,
            image_exists=absent,
            quiet_window=quiet,
            completed_cleanup=lambda *_: "e" * 64,
        )
    assert marker.read_bytes() == marker_raw


def test_live_lock_refuses_without_side_effect(fenced):
    root, pins, marker, marker_raw, _ = fenced
    fd = os.open(root / "media-elf-metadata-admission/admission.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(fifth.FifthReconciliationRefused, match="live_lock"):
            r._reconcile(
                root,
                pins,
                discover=empty,
                image_exists=absent,
                quiet_window=quiet,
                completed_cleanup=lambda *_: "e" * 64,
            )
    finally:
        os.close(fd)
    assert marker.read_bytes() == marker_raw
    assert not (root / r.RECON_BASE).exists()


def test_late_resource_refuses_after_pending_evidence(fenced):
    root, pins, marker, marker_raw, _ = fenced
    calls = []

    def discover(kind, config):
        if kind == "container":
            calls.append(kind)
            if len(calls) == 3:
                return ("a" * 64,)
        return ()

    with pytest.raises(fifth.FifthReconciliationRefused, match="resources_present"):
        r._reconcile(
            root,
            pins,
            discover=discover,
            image_exists=absent,
            quiet_window=quiet,
            completed_cleanup=lambda *_: "e" * 64,
        )
    assert marker.read_bytes() == marker_raw
    assert list((root / r.RECON_BASE).glob("reconcile-*/pending.json"))


def test_cli_error_is_generic(monkeypatch, capsys):
    monkeypatch.setattr(r, "reconcile", lambda: (_ for _ in ()).throw(ValueError("PRIVATE")))
    monkeypatch.setattr(r.os.sys, "argv", ["reconcile", "--run"])
    assert r.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "reader_fifth_reconcile_refused\n"


def test_failed_post_record_parent_sync_keeps_fence_and_consumes_reconciliation(
    fenced, monkeypatch
):
    root, pins, marker, marker_raw, _ = fenced
    original = r._sync_dir
    pending_written = []
    write = r.historical._write_pending

    def record(path, value):
        result = write(path, value)
        pending_written.append(True)
        return result

    def sync(path):
        if pending_written and path == root:
            raise OSError("parent sync uncertain")
        return original(path)

    monkeypatch.setattr(r.historical, "_write_pending", record)
    monkeypatch.setattr(r, "_sync_dir", sync)
    with pytest.raises(OSError, match="parent sync uncertain"):
        r._reconcile(
            root,
            pins,
            discover=empty,
            image_exists=absent,
            quiet_window=quiet,
            completed_cleanup=lambda *_: "e" * 64,
        )
    assert marker.read_bytes() == marker_raw
    assert list((root / r.RECON_BASE).glob("reconcile-*/pending.json"))
    with pytest.raises(fifth.FifthReconciliationRefused, match="inventory"):
        r._reconcile(
            root,
            pins,
            discover=empty,
            image_exists=absent,
            quiet_window=quiet,
            completed_cleanup=lambda *_: "e" * 64,
        )


def test_pending_record_and_parent_sync_precede_marker_truncate(fenced, monkeypatch):
    root, pins, marker, _, _ = fenced
    events = []
    write = r.historical._write_pending
    sync = r._sync_dir
    truncate = r.os.ftruncate

    def record(path, value):
        result = write(path, value)
        events.append("record")
        return result

    def sync_directory(path):
        if events:
            events.append("root" if path == root else "base")
        return sync(path)

    def clear(fd, size):
        assert events[-3:] == ["base", "root", "root"]
        events.append("clear")
        return truncate(fd, size)

    monkeypatch.setattr(r.historical, "_write_pending", record)
    monkeypatch.setattr(r, "_sync_dir", sync_directory)
    monkeypatch.setattr(r.os, "ftruncate", clear)
    r._reconcile(
        root,
        pins,
        discover=empty,
        image_exists=absent,
        quiet_window=quiet,
        completed_cleanup=lambda *_: "e" * 64,
    )
    assert marker.read_bytes() == b""
    assert events.index("record") < events.index("clear")


def test_missing_completed_cleanup_refuses_before_marker_clear(fenced):
    root, pins, marker, marker_raw, _ = fenced
    with pytest.raises(fifth.FifthReconciliationRefused, match="cleanup_receipt"):
        r._reconcile(root, pins, discover=empty, image_exists=absent, quiet_window=quiet)
    assert marker.read_bytes() == marker_raw
    assert not (root / r.RECON_BASE).exists()


def test_fifo_lock_refuses_without_blocking(fenced):
    root, pins, marker, marker_raw, _ = fenced
    lock = root / "media-elf-metadata-admission/admission.lock"
    lock.unlink()
    os.mkfifo(lock, mode=0o600)
    with pytest.raises(ValueError, match="reader_reconcile_private"):
        r._reconcile(root, pins, discover=empty, image_exists=absent, quiet_window=quiet)
    assert marker.read_bytes() == marker_raw
