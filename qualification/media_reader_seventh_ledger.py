"""Read-only six-refusal evidence for a possible, separately authorized path.

This verifies retained files under the shared lock. It does not inspect Docker,
create a grant, clear admission, or permit a seventh native proof.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_run_approval_v4 as prior_approval
from qualification import media_reader_scope as scope
from qualification import media_reader_sixth_ledger as historical
from qualification.admission import Lease
from scripts import cleanup_media_native_reader_sixth_image as cleanup

RETAINED_ROOT = historical.RETAINED_ROOT
COMPLETION_SHA256 = "6aebba764d8ff5c9dfa373feabd74be01165df08d886c93719171bf2805d078b"


class SeventhLedgerRefused(ValueError):
    """The six-refusal history, cleanup receipt, or empty marker changed."""


def _refuse(reason: str) -> None:
    raise SeventhLedgerRefused("reader_seventh_ledger_" + reason) from None


def _history() -> tuple[dict[str, str], ...]:
    return (
        *prior_approval._history(),
        {
            "attempt": cleanup.ATTEMPT,
            "policy_sha256": cleanup.PINS[f"media-native-reader/{cleanup.ATTEMPT}/policy.json"],
            "proof_sha256": cleanup.PINS[f"media-native-reader/{cleanup.ATTEMPT}/cases.json"],
            "source_head": cleanup.SOURCE_HEAD,
            "source_tree": cleanup.SOURCE_TREE,
        },
    )


def _marker(root: Path, expected: bytes) -> None:
    try:
        historical._marker(root / "media-elf-metadata-admission/active.marker", expected)
    except (historical.SixthLedgerRefused, OSError):
        _refuse("marker")


def _audit_held(root: Path, *, expected_marker: bytes | None = None) -> dict[str, object]:
    """Caller holds the admission lock for both pinned receipt reads."""
    if expected_marker is not None and (type(expected_marker) is not bytes or not expected_marker):
        _refuse("marker_expected")
    marker = b"" if expected_marker is None else expected_marker
    _marker(root, marker)
    completion = (
        cleanup._completion_held(root)
        if expected_marker is None
        else cleanup._completion_held(root, expected_marker=marker)
    )
    if completion != COMPLETION_SHA256:
        _refuse("completion")
    if (
        cleanup._completion_held(root)
        if expected_marker is None
        else cleanup._completion_held(root, expected_marker=marker)
    ) != completion:
        _refuse("completion_drift")
    _marker(root, marker)
    return {
        "status": "verified-six-refusals-and-exact-image-cleanup",
        "proof_history": _history(),
        "attempts_used": 6,
        "attempt_limit": 6,
        "cleanup_completion_sha256": completion,
        "marker_cleared": expected_marker is None,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _audit_held_live(root: Path, lease: Lease, before: dict) -> dict[str, object]:
    """Recheck the six pins under the exact occupied marker and retained lock."""
    if (
        root != RETAINED_ROOT
        or type(lease) is not Lease
        or lease._fds is None
        or not lease._lifecycle.locked()
        or lease._retained_thread != threading.get_ident()
        or type(before) is not dict
        or before.get("marker_cleared") is not True
    ):
        _refuse("live_binding")
    try:
        if scope._canonical_root(root) != root:
            _refuse("live_binding")
    except scope.ScopeRefused:
        _refuse("live_binding")
    directory, lock_fd, marker_fd = lease._fds
    marker_path = root / "media-elf-metadata-admission/active.marker"
    try:
        for fd, path in (
            (directory, marker_path.parent),
            (lock_fd, marker_path.parent / "admission.lock"),
            (marker_fd, marker_path),
        ):
            held, named = os.fstat(fd), path.lstat()
            if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
                _refuse("live_binding")
    except OSError:
        _refuse("live_binding")
    expected_marker = json.dumps(
        {"version": 1, "lease": lease.identity, "policy": lease.policy_digest}
    ).encode("ascii")
    live = _audit_held(root, expected_marker=expected_marker)
    if live != {**before, "marker_cleared": False}:
        _refuse("live_drift")
    if _audit_held(root, expected_marker=expected_marker) != live:
        _refuse("live_drift")
    return before


def _audit(root: Path) -> dict[str, object]:
    with fifth._locked(root):
        return _audit_held(root)


def audit(runtime_root: Path) -> dict[str, object]:
    """Inspect only the exact retained root; never creates proof authority."""
    if Path(runtime_root) != RETAINED_ROOT:
        _refuse("root")
    if scope._canonical_root(RETAINED_ROOT) != RETAINED_ROOT:
        _refuse("root")
    return _audit(RETAINED_ROOT)
