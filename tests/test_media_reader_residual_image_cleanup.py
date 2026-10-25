"""Disposable exact eight-image cleanup coordination; never invokes Docker."""

import json
import os
import sys
import threading

import pytest

from qualification import media_reader_post_cleanup_ledger as post_cleanup
from qualification.admission import AdmissionController
from scripts import cleanup_media_native_reader_residual_images as cleanup


@pytest.fixture
def fenced(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    (admission / "admission.lock").touch(mode=0o600)
    (admission / "active.marker").touch(mode=0o600)
    audits = []

    def audit(path, pins):
        assert path == root
        assert pins == cleanup.ledger.FILE_PINS
        audits.append(True)

    monkeypatch.setattr(cleanup.ledger, "_audit_held", audit)
    monkeypatch.setattr(cleanup.ledger, "RETAINED_ROOT", root)
    return root, audits


def test_exact_batch_has_durable_one_use_receipts_and_preserves_marker(fenced):
    root, audits = fenced
    snapshots = []
    removed = []

    def snapshot(_config, remaining):
        snapshots.append(remaining)
        assert remaining == tuple(image for image in cleanup.IDS if image not in removed)

    def remove(_config, image_id):
        assert image_id == cleanup.IDS[len(removed)]
        removed.append(image_id)
        return b"exact image removed"

    result = cleanup._cleanup(
        root,
        snapshot=snapshot,
        remove=remove,
        quiet_window=lambda _config: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
    )
    assert result["status"] == "exact-residual-images-cleaned"
    assert result["images_removed"] == 8
    assert removed == list(cleanup.IDS)
    assert snapshots[:2] == [cleanup.IDS, cleanup.IDS]
    assert snapshots[-1] == ()
    assert len(audits) >= len(cleanup.IDS) + 2
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""
    record = json.loads(
        (
            root / cleanup.BASE / next((root / cleanup.BASE).iterdir()).name / "completed.json"
        ).read_text()
    )
    assert record["images_removed"] == list(cleanup.IDS)
    assert len(record["step_sha256"]) == 8
    assert record["another_attempt_authorized"] is False
    assert cleanup.completed_cleanup(root) == result["completion_sha256"]
    with pytest.raises(cleanup.ResidualCleanupRefused, match="prior_consumption"):
        cleanup._cleanup(root, snapshot=snapshot, remove=remove, quiet_window=lambda _: ("", ""))
    assert removed == list(cleanup.IDS)


def test_default_remove_uses_no_prune_and_exact_pinned_ids(fenced, monkeypatch):
    root, _ = fenced
    calls = []

    def command(_config, *args):
        calls.append(args)
        return b"exact image removed"

    monkeypatch.setattr(cleanup, "_command", command)
    cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        quiet_window=lambda _: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
    )
    assert calls == [("image", "rm", "--no-prune", image_id) for image_id in cleanup.IDS]


def test_drift_before_evidence_prevents_any_remove(fenced):
    root, _ = fenced
    calls = []

    def snapshot(_config, _remaining):
        raise cleanup.ResidualCleanupRefused("reader_residual_cleanup_resource_inventory")

    with pytest.raises(cleanup.ResidualCleanupRefused, match="resource_inventory"):
        cleanup._cleanup(root, snapshot=snapshot, remove=lambda *_: calls.append("remove"))
    assert calls == []
    assert not (root / cleanup.BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_partial_failure_retains_pending_and_step_without_retry(fenced):
    root, _ = fenced
    removed = []

    def remove(_config, image_id):
        if len(removed) == 1:
            raise RuntimeError("simulated Docker failure")
        removed.append(image_id)
        return b"removed"

    with pytest.raises(RuntimeError, match="simulated Docker failure"):
        cleanup._cleanup(
            root,
            snapshot=lambda *_: None,
            remove=remove,
            quiet_window=lambda _: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
        )
    assert removed == [cleanup.IDS[0]]
    with pytest.raises(cleanup.ResidualCleanupRefused, match="completion_files"):
        cleanup.completed_cleanup(root)
    evidence = next((root / cleanup.BASE).iterdir())
    assert {item.name for item in evidence.iterdir()} == {"pending.json", "removed-01.json"}
    with pytest.raises(cleanup.ResidualCleanupRefused, match="prior_consumption"):
        cleanup._cleanup(root, snapshot=lambda *_: None, remove=remove)
    assert removed == [cleanup.IDS[0]]


def test_completion_refuses_rewritten_step_after_apparent_success(fenced):
    root, _ = fenced
    cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        remove=lambda *_: b"removed",
        quiet_window=lambda _: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
    )
    evidence = next((root / cleanup.BASE).iterdir())
    step = evidence / "removed-01.json"
    value = json.loads(step.read_text())
    value["runtime_authority"] = True
    step.write_text(json.dumps(value))
    with pytest.raises(cleanup.ResidualCleanupRefused, match="completion_step"):
        cleanup.completed_cleanup(root)


def test_exact_docker_inventory_includes_all_images_and_unlabelled_dependents(
    tmp_path, monkeypatch
):
    config = tmp_path / "docker-config"
    config.mkdir()
    calls = []

    def command(_config, *args):
        calls.append(args)
        if args[:2] == ("image", "ls") and "label=org.zelo.media-reader.lease" in args:
            return (cleanup.IDS[0] + "\n").encode()
        if args[:2] == ("image", "ls"):
            return (cleanup.IDS[0] + "\n").encode()
        return b""

    monkeypatch.setattr(cleanup, "_command", command)
    monkeypatch.setattr(cleanup, "_verify", lambda *_: None)
    monkeypatch.setattr(cleanup, "_inspect", lambda *_: {})
    cleanup._snapshot(config, (cleanup.IDS[0],))
    assert any(args[:3] == ("image", "ls", "--all") for args in calls)
    assert any("ancestor=" + cleanup.IDS[0] in args for args in calls)
    assert all("--no-trunc" in args for args in calls)


def test_inventory_cli_never_enters_cleanup(fenced, monkeypatch, capsys):
    root, _ = fenced
    checks = []
    monkeypatch.setattr(cleanup, "_snapshot", lambda _config, ids: checks.append(ids))
    monkeypatch.setattr(cleanup, "cleanup", lambda: pytest.fail("cleanup invoked"))
    monkeypatch.setattr(
        sys, "argv", ["cleanup_media_native_reader_residual_images.py", "--inventory"]
    )
    assert cleanup.main() == 0
    assert checks == [cleanup.IDS]
    assert json.loads(capsys.readouterr().out)["image_count"] == 8
    assert not (root / cleanup.BASE).exists()
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""


def test_post_cleanup_ledger_requires_exact_durable_completion(fenced, monkeypatch):
    root, _ = fenced
    result = cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        remove=lambda *_: b"removed",
        quiet_window=lambda _: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
    )
    monkeypatch.setattr(cleanup.ledger, "_audit_held", lambda *_: {"status": "five-intact"})
    monkeypatch.setattr(post_cleanup, "RETAINED_ROOT", root)
    with pytest.raises(post_cleanup.PostCleanupRefused, match="completion"):
        post_cleanup.audit(root)
    monkeypatch.setattr(post_cleanup, "COMPLETION_SHA256", result["completion_sha256"])
    expected = {
        "status": "five-intact",
        "residual_cleanup_completion_sha256": result["completion_sha256"],
    }
    assert post_cleanup.audit(root) == expected
    with post_cleanup.fifth._locked(root):
        assert post_cleanup._audit_held(root) == expected
    evidence = next((root / cleanup.BASE).iterdir())
    (evidence / "removed-01.json").write_text("{}")
    with pytest.raises(cleanup.ResidualCleanupRefused, match="completion_step"):
        post_cleanup.audit(root)


def test_completion_chain_can_be_rechecked_under_an_exact_live_marker(fenced, monkeypatch):
    root, _ = fenced
    result = cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        remove=lambda *_: b"removed",
        quiet_window=lambda _: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
    )
    marker = root / "media-elf-metadata-admission/active.marker"
    payload = b'{"version": 1, "lease": "held", "policy": "digest"}'
    marker.write_bytes(payload)
    calls = []

    def audit(path, pins, *, expected_marker=None):
        assert path == root and pins == cleanup.ledger.FILE_PINS
        calls.append(expected_marker)
        assert marker.read_bytes() == expected_marker

    monkeypatch.setattr(cleanup.ledger, "_audit_held", audit)
    with post_cleanup.fifth._locked(root):
        assert (
            cleanup._completion_held(root, expected_marker=payload) == result["completion_sha256"]
        )
    assert calls == [payload]
    assert marker.read_bytes() == payload


def test_post_cleanup_live_audit_preserves_the_pre_marker_snapshot(fenced, monkeypatch):
    root, _ = fenced
    result = cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        remove=lambda *_: b"removed",
        quiet_window=lambda _: ("2026-10-25T00:00:00Z", "2026-10-25T00:00:02Z"),
    )
    monkeypatch.setattr(post_cleanup, "RETAINED_ROOT", root)
    monkeypatch.setattr(post_cleanup, "COMPLETION_SHA256", result["completion_sha256"])

    def historical(_root, _pins, *, expected_marker=None):
        return {"status": "five-intact", "marker_cleared": expected_marker is None}

    monkeypatch.setattr(cleanup.ledger, "_audit_held", historical)
    before = post_cleanup.audit(root)
    marker = root / "media-elf-metadata-admission/active.marker"
    lease = AdmissionController(marker.parent).acquire("b" * 64)
    payload = marker.read_bytes()
    entered, release = threading.Event(), threading.Event()

    def other_thread():
        with lease.retained():
            entered.set()
            assert release.wait(timeout=5)

    thread = threading.Thread(target=other_thread)
    thread.start()
    try:
        assert entered.wait(timeout=5)
        with pytest.raises(post_cleanup.PostCleanupRefused, match="binding"):
            post_cleanup._audit_held_live(root, lease, before)
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    with lease.retained():
        assert post_cleanup._audit_held_live(root, lease, before) == before
        moved = root.with_name("runtime-moved")
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
        try:
            with pytest.raises(post_cleanup.PostCleanupRefused, match="binding"):
                post_cleanup._audit_held_live(root, lease, before)
        finally:
            root.unlink()
            moved.rename(root)
        alternate = root / "unrelated.lock"
        alternate.touch(mode=0o600)
        wrong_lock = os.open(alternate, os.O_RDWR | os.O_CLOEXEC)
        original_fds = lease._fds
        try:
            lease._fds = (original_fds[0], wrong_lock, original_fds[2])
            with pytest.raises(post_cleanup.PostCleanupRefused, match="binding"):
                post_cleanup._audit_held_live(root, lease, before)
        finally:
            lease._fds = original_fds
            os.close(wrong_lock)
        altered = dict(before, attempts_used=6)
        with pytest.raises(post_cleanup.PostCleanupRefused, match="drift"):
            post_cleanup._audit_held_live(root, lease, altered)
        evidence = next((root / cleanup.BASE).iterdir())
        (evidence / "removed-01.json").write_text("{}")
        with pytest.raises(cleanup.ResidualCleanupRefused, match="completion_step"):
            post_cleanup._audit_held_live(root, lease, before)
    assert marker.read_bytes() == payload
    with pytest.raises(post_cleanup.PostCleanupRefused, match="binding"):
        post_cleanup._audit_held_live(root, lease, before)
    lease.finish(cleanup_confirmed=True)
