"""Inert one-process Media approval receipt capture; no grant or fifth proof.

This class has no operator CLI and is not connected to the native qualifier.
A future reviewed path must add resource preflight, grant and run handling under
this same admission lock. No readable receipt is a run permit.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty, Queue

from qualification import media_reader_extra_attempt_v3 as v3
from qualification import media_reader_resource_preflight_v3 as preflight
from qualification import media_reader_run_approval_v3 as approval
from qualification.admission import AdmissionController, Lease

BASE = "media-native-reader-run-approval-v3"
RECEIPT = "receipt.json"
RECHECK = "recheck.json"
COMMENT_WAIT_SECONDS = 300


class CaptureRefused(ValueError):
    """The receipt is absent, uncertain, already used, or unbound."""


def _refuse(reason: str) -> None:
    raise CaptureRefused("reader_native_v3_capture_" + reason) from None


def _trusted_directory(fd: int, *, private: bool) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or (
            stat.S_IMODE(info.st_mode) != 0o700
            if private
            else bool(stat.S_IMODE(info.st_mode) & 0o022)
        )
    ):
        _refuse("directory")


def _persist(root: Path, snapshot: dict) -> None:
    """Leave every uncertain entry fenced; never clean or retry in this process."""
    record = {"schema": "zelo-media-native-reader-v3-approval-receipt-v1", "snapshot": snapshot}
    raw = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > 16384:
        _refuse("receipt_bound")
    root_fd = base_fd = file_fd = -1
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        _trusted_directory(root_fd, private=False)
        os.mkdir(BASE, 0o700, dir_fd=root_fd)
        # A failure here can make the entry disappear after a crash. The live
        # challenge is never replayed; no grant or run is attempted on failure.
        os.fsync(root_fd)
        base_fd = os.open(
            BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd
        )
        _trusted_directory(base_fd, private=True)
        file_fd = os.open(
            RECEIPT,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=base_fd,
        )
        view = memoryview(raw)
        while view:
            count = os.write(file_fd, view)
            if count <= 0:
                _refuse("write")
            view = view[count:]
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = -1
        os.fsync(base_fd)
        verify_fd = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=base_fd)
        try:
            observed = os.read(verify_fd, len(raw) + 1)
            if observed != raw:
                _refuse("readback")
        finally:
            os.close(verify_fd)
    except OSError:
        _refuse("storage")
    finally:
        for fd in (file_fd, base_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def _persist_recheck(root: Path, snapshot: dict) -> None:
    """Persist the fresh decision before returning an admission lease."""
    record = {"schema": "zelo-media-native-reader-v3-approval-recheck-v1", "snapshot": snapshot}
    raw = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > 16384:
        _refuse("recheck_bound")
    root_fd = base_fd = file_fd = -1
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        _trusted_directory(root_fd, private=False)
        base_fd = os.open(
            BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd
        )
        _trusted_directory(base_fd, private=True)
        file_fd = os.open(
            RECHECK,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=base_fd,
        )
        view = memoryview(raw)
        while view:
            count = os.write(file_fd, view)
            if count <= 0:
                _refuse("recheck_write")
            view = view[count:]
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = -1
        os.fsync(base_fd)
        verify_fd = os.open(RECHECK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=base_fd)
        try:
            if os.read(verify_fd, len(raw) + 1) != raw:
                _refuse("recheck_readback")
        finally:
            os.close(verify_fd)
    except OSError:
        _refuse("storage")
    finally:
        for fd in (file_fd, base_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def _bounded_comment_id(
    request: Callable[[str, list[dict]], int], challenge: str, history: list[dict]
) -> int:
    """A late callback result cannot be used after the held-lock wait expires.

    The future operator process must exit after timeout; the daemon callback
    has no grant or attempt authority and must never be reused as a permit.
    """
    reply: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            reply.put((True, request(challenge, copy.deepcopy(history))))
        except BaseException as exc:
            reply.put((False, exc))

    threading.Thread(target=invoke, daemon=True, name="media-v3-comment-input").start()
    try:
        success, value = reply.get(timeout=COMMENT_WAIT_SECONDS)
    except Empty:
        _refuse("comment_timeout")
    if not success:
        _refuse("comment_input")
    return value


class ApprovalCapture:
    """Consume one in-memory challenge while holding one admission lock."""

    def __init__(self, root: Path, *, expected_source: tuple[str, str]) -> None:
        self._root = root
        self._source = expected_source
        self._used = False
        self._snapshot: dict | None = None

    def acquire(
        self,
        policy_digest: str,
        request_comment_id: Callable[[str, list[dict]], int],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> tuple[Lease, dict]:
        """Capture evidence under lock; the returned lease has no native run authority."""
        if self._used:
            _refuse("already_used")
        self._used = True

        def before_marker() -> None:
            root, record = v3._history(self._root)
            if (
                type(self._source) is not tuple
                or len(self._source) != 2
                or v3._source_identity() != self._source
                or (root / BASE).exists()
            ):
                _refuse("preflight")
            challenge = secrets.token_hex(32)
            issued = clock()
            if (
                type(issued) is not datetime
                or issued.tzinfo is None
                or issued.utcoffset() != timedelta(0)
            ):
                _refuse("clock")
            history = record["proof_history"]
            comment_id = _bounded_comment_id(request_comment_id, challenge, history)
            snapshot = approval.fetch_validated_snapshot(
                get=approval.gh_api_get,
                expected_comment_id=comment_id,
                expected_challenge=challenge,
                challenge_issued_at=issued,
                md_account_id=approval.MD_ACCOUNT_ID,
                expected_root=root,
                source_head=self._source[0],
                source_tree=self._source[1],
                proof_history=history,
                clock=clock,
            )
            refreshed_root, refreshed = v3._history(root)
            if (
                refreshed_root != root
                or refreshed["proof_history"] != history
                or v3._source_identity() != self._source
                or (root / BASE).exists()
            ):
                _refuse("drift")
            preflight.observe(root)
            observed_root, observed = v3._history(root)
            if (
                observed_root != root
                or observed["proof_history"] != history
                or v3._source_identity() != self._source
                or (root / BASE).exists()
            ):
                _refuse("resource_preflight_drift")
            _persist(root, snapshot)
            fresh = approval.recheck_snapshot(
                get=approval.gh_api_get,
                original=snapshot,
                expected_comment_id=comment_id,
                expected_challenge=challenge,
                challenge_issued_at=issued,
                md_account_id=approval.MD_ACCOUNT_ID,
                expected_root=root,
                source_head=self._source[0],
                source_tree=self._source[1],
                proof_history=history,
                clock=clock,
            )
            _persist_recheck(root, fresh)
            self._snapshot = fresh

        lease = AdmissionController(self._root / "media-elf-metadata-admission").acquire_after(
            policy_digest, before_marker
        )
        if self._snapshot is None:
            _refuse("snapshot_missing")
        return lease, self._snapshot
