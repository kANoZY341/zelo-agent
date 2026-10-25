"""One-process, held-lock sixth decision capture; no native attempt path.

An approval comment is captured only inside one live invocation. Durable
receipt/grant files consume a decision but are never themselves run permits.
There is no operator CLI or qualifier integration in this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from qualification import media_reader_approval_capture_v3 as prior_capture
from qualification import media_reader_extra_attempt_v3 as previous
from qualification import media_reader_post_cleanup_ledger as post_cleanup
from qualification import media_reader_resource_preflight_v4 as preflight
from qualification import media_reader_run_approval_v4 as approval
from qualification import media_reader_scope as scope
from qualification.admission import AdmissionController, Lease

RETAINED_ROOT = post_cleanup.RETAINED_ROOT
BASE = "media-native-reader-run-approval-v4"
GRANT_BASE = "media-native-reader-extra-attempt-v4"
MAX_RECORD = 65536


class CaptureRefused(ValueError):
    """A decision or durable one-use receipt is absent, uncertain, or consumed."""


def _refuse(reason: str) -> None:
    raise CaptureRefused("reader_native_v4_capture_" + reason) from None


def _source_identity() -> tuple[str, str]:
    return previous._source_identity()


def _raw(value: dict) -> bytes:
    result = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if not 0 < len(result) <= MAX_RECORD:
        _refuse("record_bound")
    return result


def _private(fd: int, *, directory: bool) -> None:
    info = os.fstat(fd)
    mode = stat.S_IMODE(info.st_mode)
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or mode != (0o700 if directory else 0o600)
        or (not directory and info.st_nlink != 1)
    ):
        _refuse("path")


def _write_once(root: Path, base: str, name: str, value: dict, *, create: bool) -> str:
    """Sync an exclusive file and directory; leave every uncertain entry fenced."""
    if base not in (BASE, GRANT_BASE) or name not in (
        "receipt.json",
        "recheck.json",
        "grant.json",
        "seal.json",
    ):
        _refuse("record_path")
    raw = _raw(value)
    root_fd = base_fd = file_fd = -1
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(root_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            _refuse("root")
        if create:
            os.mkdir(base, 0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        base_fd = os.open(
            base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd
        )
        _private(base_fd, directory=True)
        file_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=base_fd,
        )
        _private(file_fd, directory=False)
        view = memoryview(raw)
        while view:
            count = os.write(file_fd, view)
            if count <= 0:
                _refuse("record_write")
            view = view[count:]
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = -1
        os.fsync(base_fd)
        if scope._read(root / base / name, maximum=MAX_RECORD) != raw:
            _refuse("record_readback")
    except OSError:
        _refuse("record_storage")
    finally:
        for fd in (file_fd, base_fd, root_fd):
            if fd >= 0:
                os.close(fd)
    return hashlib.sha256(raw).hexdigest()


class ApprovalCapture:
    """Durably consume one fresh decision under one admission lock."""

    def __init__(self, root: Path, *, expected_source: tuple[str, str]) -> None:
        self._root = root
        self._source = expected_source
        self._used = False
        self._snapshot: dict | None = None
        self._expiry: datetime | None = None

    def acquire(
        self,
        policy_digest: str,
        request_comment_id: Callable[[str, list[dict]], int],
        *,
        get: Callable[[str], dict],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> tuple[Lease, dict]:
        """Capture and fence a proposed sixth decision; return no run permit."""
        if self._used:
            _refuse("already_used")
        self._used = True
        if self._root != RETAINED_ROOT or not callable(request_comment_id) or not callable(get):
            _refuse("root_or_callback")
        root = scope._canonical_root(self._root)

        def before_marker() -> None:
            if (root / BASE).exists() or (root / GRANT_BASE).exists():
                _refuse("prior_consumption")
            if _source_identity() != self._source:
                _refuse("source")
            before = post_cleanup._audit_held(root)
            history = list(before["proof_history"])
            preflight.observe_held(root)
            if post_cleanup._audit_held(root) != before:
                _refuse("preflight_drift")
            challenge = secrets.token_hex(32)
            issued = clock()
            if (
                type(issued) is not datetime
                or issued.tzinfo is None
                or issued.utcoffset() != UTC.utcoffset(issued)
            ):
                _refuse("clock")
            comment_id = prior_capture._bounded_comment_id(request_comment_id, challenge, history)
            binding = {
                "root": root,
                "get": get,
                "expected_comment_id": comment_id,
                "expected_challenge": challenge,
                "challenge_issued_at": issued,
                "source_head": self._source[0],
                "source_tree": self._source[1],
                "clock": clock,
            }
            snapshot = approval.fetch_validated_snapshot_held(**binding)
            preflight.observe_held(root)
            if post_cleanup._audit_held(root) != before or _source_identity() != self._source:
                _refuse("resource_drift")
            _write_once(
                root,
                BASE,
                "receipt.json",
                {"schema": "zelo-media-native-reader-v4-approval-receipt-v1", "snapshot": snapshot},
                create=True,
            )
            fresh = approval.recheck_snapshot_held(original=snapshot, **binding)
            recheck_sha = _write_once(
                root,
                BASE,
                "recheck.json",
                {"schema": "zelo-media-native-reader-v4-approval-recheck-v1", "snapshot": fresh},
                create=False,
            )
            preflight.observe_held(root)
            if post_cleanup._audit_held(root) != before or _source_identity() != self._source:
                _refuse("grant_drift")
            try:
                expiry = approval._time(json.loads(fresh["comment_body"])["expires_utc"])
            except (KeyError, TypeError, ValueError):
                _refuse("grant_binding")
            now = clock()
            if (
                type(now) is not datetime
                or now.tzinfo is None
                or now.utcoffset() != UTC.utcoffset(now)
                or now >= expiry
            ):
                _refuse("grant_expired")
            grant = {
                "schema": "zelo-media-native-reader-extra-attempt-v4",
                "status": "one-additional-attempt-granted",
                "runtime_root": str(root),
                "source_head": self._source[0],
                "source_tree": self._source[1],
                "approval_comment_id": comment_id,
                "approval_body_sha256": fresh["comment_body_sha256"],
                "approval_challenge_sha256": fresh["challenge_sha256"],
                "approval_recheck_sha256": recheck_sha,
                "residual_cleanup_completion_sha256": post_cleanup.COMPLETION_SHA256,
                "proof_history_sha256": fresh["proof_history_sha256"],
                "attempts_used_before": 5,
                "attempt_limit_after": 6,
                "additional_attempts": 1,
                "attempt_reset": False,
                "proof_rewritten": False,
                "proof_deleted": False,
                "network": "none",
                "runtime_authority": False,
                "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            grant_sha = _write_once(root, GRANT_BASE, "grant.json", grant, create=True)
            _write_once(
                root,
                GRANT_BASE,
                "seal.json",
                {
                    "schema": "zelo-media-native-reader-extra-attempt-v4-seal-v1",
                    "grant_sha256": grant_sha,
                    "source_head": self._source[0],
                    "source_tree": self._source[1],
                },
                create=False,
            )
            self._snapshot = fresh
            self._expiry = expiry

        lease = AdmissionController(root / "media-elf-metadata-admission").acquire_after(
            policy_digest, before_marker
        )
        try:
            source = _source_identity()
            now = clock()
        except BaseException:
            lease.finish(cleanup_confirmed=False)
            raise
        if self._snapshot is None or self._expiry is None or source != self._source:
            lease.finish(cleanup_confirmed=False)
            _refuse("post_marker_drift")
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != UTC.utcoffset(now)
            or now >= self._expiry
        ):
            lease.finish(cleanup_confirmed=False)
            _refuse("expired_after_marker")
        return lease, self._snapshot
