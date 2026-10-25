"""One-process, held-lock sixth decision capture and one-use allocation gate.

An approval comment is captured only inside one live invocation. Durable
receipt/grant files consume a decision but are never themselves run permits.
The operator CLI and native qualifier use the live gate after a final recheck.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from qualification import media_reader_approval_capture_v3 as prior_capture
from qualification import media_reader_extra_attempt_v3 as previous
from qualification import media_reader_post_cleanup_ledger as post_cleanup
from qualification import media_reader_resource_preflight_v4 as preflight
from qualification import media_reader_run_approval_v4 as approval
from qualification import media_reader_scope as scope
from qualification.admission import AdmissionController, AdmissionDenied, Lease

RETAINED_ROOT = post_cleanup.RETAINED_ROOT
BASE = "media-native-reader-run-approval-v4"
GRANT_BASE = "media-native-reader-extra-attempt-v4"
MAX_RECORD = 65536
_GATES: dict[object, tuple[Path, Lease, Callable[[], None], int, int]] = {}
_GATE_LOCK = threading.Lock()


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
        "attempt-recheck.json",
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


def _check_records(root: Path, pins: dict[tuple[str, str], str]) -> None:
    """Pin the exact durable decision bytes via private directory descriptors."""
    root_fd = base_fd = file_fd = -1
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(root_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            _refuse("record_root_drift")
        for (base, name), expected in pins.items():
            if base not in (BASE, GRANT_BASE) or name not in (
                "receipt.json",
                "recheck.json",
                "attempt-recheck.json",
                "grant.json",
                "seal.json",
            ):
                _refuse("record_path")
            base_fd = os.open(
                base,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_fd,
            )
            _private(base_fd, directory=True)
            file_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=base_fd,
            )
            _private(file_fd, directory=False)
            before = os.fstat(file_fd)
            if not 0 < before.st_size <= MAX_RECORD:
                _refuse("record_bound")
            raw = os.read(file_fd, MAX_RECORD + 1)
            after = os.fstat(file_fd)
            if (
                len(raw) != before.st_size
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or hashlib.sha256(raw).hexdigest() != expected
            ):
                _refuse("record_drift")
            os.close(file_fd)
            file_fd = -1
            os.close(base_fd)
            base_fd = -1
    except OSError:
        _refuse("record_storage")
    finally:
        for fd in (file_fd, base_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def _consume_attempt_gate(root: Path, gate: object) -> None:
    """Only an in-process, one-use gate under its issuing live lease may pass."""
    with _GATE_LOCK:
        try:
            state = _GATES.pop(gate, None)
        except TypeError:
            state = None
    if state is None:
        _refuse("attempt_gate")
    expected, lease, check, owner_pid, owner_thread = state
    if (
        root != expected
        or owner_pid != os.getpid()
        or owner_thread != threading.get_ident()
        or lease._fds is None
        or lease._retained_thread != owner_thread
        or not lease._lifecycle.locked()
    ):
        _refuse("attempt_gate")
    check()


class ApprovalCapture:
    """Durably consume one fresh decision under one admission lock."""

    def __init__(self, root: Path, *, expected_source: tuple[str, str]) -> None:
        self._root = root
        self._source = expected_source
        self._used = False
        self._snapshot: dict | None = None
        self._expiry: datetime | None = None
        self._before: dict | None = None
        self._challenge: str | None = None
        self._issued: datetime | None = None
        self._comment_id: int | None = None
        self._grant_sha: str | None = None
        self._record_pins: dict[tuple[str, str], str] = {}
        self._lease: Lease | None = None
        self._attempt_rechecked = False

    def acquire(
        self,
        policy_digest: str,
        request_comment_id: Callable[[str, list[dict]], int],
        *,
        get: Callable[[str], dict],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> tuple[Lease, dict]:
        """Capture and fence a proposed sixth decision; return no saved run permit."""
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
            receipt_sha = _write_once(
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
            seal_sha = _write_once(
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
            self._before = before
            self._challenge = challenge
            self._issued = issued
            self._comment_id = comment_id
            self._grant_sha = grant_sha
            self._record_pins = {
                (BASE, "receipt.json"): receipt_sha,
                (BASE, "recheck.json"): recheck_sha,
                (GRANT_BASE, "grant.json"): grant_sha,
                (GRANT_BASE, "seal.json"): seal_sha,
            }

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
        self._lease = lease
        return lease, self._snapshot

    def recheck_before_attempt(
        self,
        lease: Lease,
        *,
        get: Callable[[str], dict],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        allocate: Callable[[Path, object], object] | None = None,
    ) -> dict | tuple[dict, object]:
        """Consume one final recheck; optional allocation remains under the lease.

        A caller must never infer permission from a saved grant. Only the live
        gate passed to its trusted allocator can allow one attempt directory.
        """
        if (
            lease is not self._lease
            or lease._fds is None
            or self._snapshot is None
            or self._before is None
            or self._challenge is None
            or self._issued is None
            or self._comment_id is None
            or self._grant_sha is None
            or len(self._record_pins) != 4
        ):
            _refuse("attempt_lease")
        try:
            with lease.retained():
                if self._attempt_rechecked:
                    _refuse("attempt_recheck_used")
                self._attempt_rechecked = True
                root = self._root
                if _source_identity() != self._source:
                    _refuse("attempt_source")
                post_cleanup._audit_held_live(root, lease, self._before)
                preflight.observe_held(root)
                fresh = approval.recheck_snapshot_lease(
                    original=self._snapshot,
                    root=root,
                    lease=lease,
                    before=self._before,
                    get=get,
                    expected_comment_id=self._comment_id,
                    expected_challenge=self._challenge,
                    challenge_issued_at=self._issued,
                    source_head=self._source[0],
                    source_tree=self._source[1],
                    clock=clock,
                )
                preflight.observe_held(root)
                post_cleanup._audit_held_live(root, lease, self._before)
                if _source_identity() != self._source:
                    _refuse("attempt_source_drift")
                _check_records(root, self._record_pins)
                attempt_sha = _write_once(
                    root,
                    BASE,
                    "attempt-recheck.json",
                    {
                        "schema": "zelo-media-native-reader-v4-attempt-recheck-v1",
                        "snapshot": fresh,
                    },
                    create=False,
                )
                pins = {**self._record_pins, (BASE, "attempt-recheck.json"): attempt_sha}
                expiry = approval._time(json.loads(fresh["comment_body"])["expires_utc"])

                def check() -> None:
                    post_cleanup._audit_held_live(root, lease, self._before)
                    preflight.observe_held(root)
                    _check_records(root, pins)
                    if _source_identity() != self._source:
                        _refuse("attempt_source_drift")
                    now = clock()
                    if (
                        type(now) is not datetime
                        or now.tzinfo is None
                        or now.utcoffset() != UTC.utcoffset(now)
                        or now >= expiry
                    ):
                        _refuse("attempt_expired")

                check()
                if allocate is None:
                    return fresh
                if not callable(allocate):
                    _refuse("attempt_allocator")
                gate = object()
                with _GATE_LOCK:
                    _GATES[gate] = (root, lease, check, os.getpid(), threading.get_ident())
                try:
                    return fresh, allocate(root, gate)
                finally:
                    with _GATE_LOCK:
                        _GATES.pop(gate, None)
        except AdmissionDenied:
            _refuse("attempt_lease")
