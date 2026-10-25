"""Read-only, receipt-bound five-refusal ledger after exact residual cleanup.

No Docker operation, grant, admission change, or sixth proof is performed here.
The shared admission lock covers both the historical ledger and the one-use
cleanup receipt chain; a later run path must separately check live Docker.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from qualification import media_reader_sixth_ledger as historical
from qualification.admission import Lease
from scripts import cleanup_media_native_reader_residual_images as cleanup

RETAINED_ROOT = historical.RETAINED_ROOT
COMPLETION_SHA256 = "8795cf71ffa8b117e5c5902ba7f5d6470c0b6bfd2c696a7b59015efa2b8b382c"


class PostCleanupRefused(ValueError):
    """The retained history or exact residual cleanup is absent or changed."""


def _audit_held(root: Path) -> dict[str, object]:
    """Caller owns the shared admission lock throughout this file-only audit."""
    history = historical._audit_held(root, historical.FILE_PINS)
    completion = cleanup._completion_held(root)
    if completion != COMPLETION_SHA256:
        raise PostCleanupRefused("reader_post_cleanup_completion")
    # The second historical audit catches drift across the receipt read.
    if historical._audit_held(root, historical.FILE_PINS) != history:
        raise PostCleanupRefused("reader_post_cleanup_ledger_drift")
    return {**history, "residual_cleanup_completion_sha256": completion}


def _audit_held_live(root: Path, lease: Lease, before: dict) -> dict:
    """Recheck pinned history under an exact live marker while the caller holds its lock.

    `before` must be the empty-marker snapshot captured in this same admission
    invocation. This verifies the marker and both retained receipt chains; it
    does not fetch GitHub, create a grant, or authorize another attempt.
    """
    if (
        root != RETAINED_ROOT
        or type(lease) is not Lease
        or lease._fds is None
        or not lease._lifecycle.locked()
        or lease._retained_thread != threading.get_ident()
        or type(before) is not dict
        or before.get("marker_cleared") is not True
    ):
        raise PostCleanupRefused("reader_post_cleanup_live_binding")
    try:
        if scope._canonical_root(root) != root:
            raise PostCleanupRefused("reader_post_cleanup_live_binding")
    except scope.ScopeRefused:
        raise PostCleanupRefused("reader_post_cleanup_live_binding") from None
    directory, lock_fd, marker_fd = lease._fds
    marker_path = root / "media-elf-metadata-admission/active.marker"
    held_directory, path_directory = os.fstat(directory), marker_path.parent.lstat()
    held_lock, path_lock = os.fstat(lock_fd), (marker_path.parent / "admission.lock").lstat()
    held_marker, path_marker = os.fstat(marker_fd), marker_path.lstat()
    if (
        (held_directory.st_dev, held_directory.st_ino)
        != (
            path_directory.st_dev,
            path_directory.st_ino,
        )
        or (held_lock.st_dev, held_lock.st_ino)
        != (
            path_lock.st_dev,
            path_lock.st_ino,
        )
        or (held_marker.st_dev, held_marker.st_ino) != (path_marker.st_dev, path_marker.st_ino)
    ):
        raise PostCleanupRefused("reader_post_cleanup_live_binding")
    expected_marker = json.dumps(
        {"version": 1, "lease": lease.identity, "policy": lease.policy_digest}
    ).encode("ascii")
    history = historical._audit_held(root, historical.FILE_PINS, expected_marker=expected_marker)
    completion = cleanup._completion_held(root, expected_marker=expected_marker)
    if completion != COMPLETION_SHA256 or history.get("marker_cleared") is not False:
        raise PostCleanupRefused("reader_post_cleanup_live_completion")
    if (
        historical._audit_held(root, historical.FILE_PINS, expected_marker=expected_marker)
        != history
    ):
        raise PostCleanupRefused("reader_post_cleanup_live_ledger_drift")
    if before != {
        **history,
        "marker_cleared": True,
        "residual_cleanup_completion_sha256": completion,
    }:
        raise PostCleanupRefused("reader_post_cleanup_live_drift")
    return before


def audit(runtime_root: Path) -> dict[str, object]:
    """Verify both receipt chains under one lock; report no Docker authority."""
    if Path(runtime_root) != RETAINED_ROOT:
        raise PostCleanupRefused("reader_post_cleanup_root")
    root = scope._canonical_root(RETAINED_ROOT)
    with fifth._locked(root):
        return _audit_held(root)
