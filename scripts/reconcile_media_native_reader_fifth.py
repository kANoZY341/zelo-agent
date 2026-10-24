#!/usr/bin/env python3
"""Explicit fifth-refusal admission reconciliation; never runs a reader or grants attempt six."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from scripts import reconcile_media_native_reader as historical

RECON_BASE = "media-native-reader-reconciliation-fifth"
DOCKER = historical.DOCKER
LABEL = historical.LABEL


def _refuse(code: str) -> None:
    raise fifth.FifthReconciliationRefused("reader_fifth_reconcile_" + code)


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _mutable_lock(root: Path) -> Iterator[tuple[int, int]]:
    admission = root / "media-elf-metadata-admission"
    fifth._children(admission, {"admission.lock", "active.marker"})
    directory = os.open(admission, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    lock = marker = None
    try:
        historical._private(os.fstat(directory), directory=True)
        lock = os.open("admission.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        historical._private(os.fstat(lock))
        if os.fstat(lock).st_size != 0:
            _refuse("lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _refuse("live_lock")
        marker = os.open(
            "active.marker",
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory,
        )
        historical._private(os.fstat(marker))
        yield directory, marker
    finally:
        if marker is not None:
            os.close(marker)
        if lock is not None:
            os.close(lock)
        os.close(directory)


def _discover(kind: str, config: Path) -> tuple[str, ...]:
    if kind not in ("container", "image"):
        _refuse("kind")
    prefix = ("container", "ls", "--all") if kind == "container" else ("image", "ls", "--all")
    raw = historical._command(
        (
            DOCKER,
            "--host=unix:///var/run/docker.sock",
            *prefix,
            "--no-trunc",
            "--filter",
            f"label={LABEL}={fifth.LEASE}",
            "--format",
            "{{.ID}}",
        ),
        config,
    )
    try:
        rows = raw.decode("ascii", "strict").splitlines()
    except UnicodeError:
        _refuse("discovery")
    pattern = r"[0-9a-f]{64}" if kind == "container" else r"sha256:[0-9a-f]{64}"
    if len(rows) > 4096 or any(re.fullmatch(pattern, row) is None for row in rows):
        _refuse("discovery")
    return tuple(rows)


def _image_exists(image_id: str, config: Path) -> bool:
    raw = historical._command(
        (
            DOCKER,
            "--host=unix:///var/run/docker.sock",
            "image",
            "ls",
            "--all",
            "--no-trunc",
            "--format",
            "{{.ID}}",
        ),
        config,
    )
    try:
        rows = raw.decode("ascii", "strict").splitlines()
    except UnicodeError:
        _refuse("image_inventory")
    if len(rows) > 4096 or any(re.fullmatch(r"sha256:[0-9a-f]{64}", row) is None for row in rows):
        _refuse("image_inventory")
    return image_id in rows


def _empty_resources(
    config: Path, image_id: str, discover: Callable, image_exists: Callable
) -> None:
    if discover("container", config) or discover("image", config) or image_exists(image_id, config):
        _refuse("resources_present")


def _evidence_directory(root: Path) -> Path:
    base = root / RECON_BASE
    try:
        base.mkdir(mode=0o700)
    except FileExistsError:
        pass
    fifth._children(base, set())
    _sync_dir(root)
    path = Path(tempfile.mkdtemp(prefix="reconcile-", dir=base))
    os.chmod(path, 0o700)
    _sync_dir(base)
    _sync_dir(root)
    return path


def _completed_cleanup(root: Path, pins: dict[str, tuple[str, int, int]], image_id: str) -> str:
    from scripts import cleanup_media_native_reader_fifth_image as cleanup

    try:
        return cleanup.completed_cleanup(root, pins, image_id)
    except OSError:
        _refuse("cleanup_receipt")


def _reconcile(
    root: Path,
    pins: dict[str, tuple[str, int, int]],
    *,
    discover: Callable = _discover,
    image_exists: Callable = _image_exists,
    quiet_window: Callable = historical._quiet_window,
    completed_cleanup: Callable = _completed_cleanup,
) -> dict[str, object]:
    root = scope._canonical_root(root)
    with _mutable_lock(root) as (admission_fd, marker_fd):
        fifth._audit_held(root, pins)
        marker_raw = historical._read_fd(marker_fd, 4096)
        if (
            hashlib.sha256(marker_raw).hexdigest()
            != pins["media-elf-metadata-admission/active.marker"][0]
        ):
            _refuse("marker_pin")
        if (root / RECON_BASE).exists():
            fifth._children(root / RECON_BASE, set())
        image_raw = fifth._read_pinned(
            root / f"media-native-reader/{fifth.FIFTH}/image-id",
            *pins[f"media-native-reader/{fifth.FIFTH}/image-id"],
        )
        try:
            image_id = image_raw.decode("ascii", "strict").strip()
        except UnicodeError:
            _refuse("image_identity")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            _refuse("image_identity")
        completion_sha = completed_cleanup(root, pins, image_id)
        with tempfile.TemporaryDirectory(
            prefix="reader-fifth-reconcile-docker-", dir=root
        ) as raw_config:
            config = Path(raw_config)
            os.chmod(config, 0o700)
            _empty_resources(config, image_id, discover, image_exists)
            quiet_start, quiet_end = quiet_window(config)
            _empty_resources(config, image_id, discover, image_exists)
            if list(os.scandir(config)):
                _refuse("fresh_config_dirty")
            fifth._audit_held(root, pins)
            evidence_dir = _evidence_directory(root)
            pending = {
                "schema": "zelo-media-native-reader-fifth-reconciliation-v1",
                "status": "pending-marker-clear",
                "failed_attempt": fifth.FIFTH,
                "failed_source_head": fifth.SOURCE_HEAD,
                "failed_source_tree": fifth.SOURCE_TREE,
                "failed_lease": fifth.LEASE,
                "runtime_root": str(root),
                "policy_sha256": pins[f"media-native-reader/{fifth.FIFTH}/policy.json"][0],
                "proof_sha256": pins[f"media-native-reader/{fifth.FIFTH}/cases.json"][0],
                "marker_sha256": pins["media-elf-metadata-admission/active.marker"][0],
                "grant_sha256": pins["media-native-reader-extra-attempt-v3/grant.json"][0],
                "approval_receipt_sha256": pins["media-native-reader-run-approval-v3/receipt.json"][
                    0
                ],
                "attempt_recheck_sha256": pins[
                    "media-native-reader-run-approval-v3/attempt-recheck.json"
                ][0],
                "image_id_sha256": pins[f"media-native-reader/{fifth.FIFTH}/image-id"][0],
                "docker_container_count_before": 0,
                "docker_image_count_before": 0,
                "docker_container_count_after": 0,
                "docker_image_count_after": 0,
                "original_image_id_present": False,
                "cleanup_completion_sha256": completion_sha,
                "quiet_start": quiet_start,
                "quiet_end": quiet_end,
                "quiet_seconds": historical.QUIET_SECONDS,
                "attempts_used": 5,
                "attempt_limit": 5,
                "attempt_reset": False,
                "proof_rewritten": False,
                "proof_deleted": False,
                "another_attempt_authorized": False,
                "runtime_authority": False,
            }
            pending_raw, evidence_path = historical._write_pending(evidence_dir, pending)
            _sync_dir(root / RECON_BASE)
            _sync_dir(root)
            _empty_resources(config, image_id, discover, image_exists)
        _sync_dir(root)
        fifth._audit_held(root, pins)
        if historical._read_fd(marker_fd, 4096) != marker_raw:
            _refuse("marker_drift")
        if completed_cleanup(root, pins, image_id) != completion_sha:
            _refuse("cleanup_completion_drift")
        # Pending evidence and every ancestor entry must be durable before this mutation.
        os.fsync(admission_fd)
        os.ftruncate(marker_fd, 0)
        os.fsync(marker_fd)
        os.fsync(admission_fd)
    return {
        "status": "reconciled-fifth-refusal-admission",
        "evidence": str(evidence_path),
        "evidence_sha256": hashlib.sha256(pending_raw).hexdigest(),
        "attempts_used": 5,
        "attempt_limit": 5,
        "admission_marker_cleared": True,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def reconcile() -> dict[str, object]:
    """Fixed-root operator action; must not run without a separate fence decision."""
    return _reconcile(fifth.RETAINED_ROOT, fifth.FILE_PINS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        result = reconcile()
    except Exception:
        print("reader_fifth_reconcile_refused", file=os.sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
