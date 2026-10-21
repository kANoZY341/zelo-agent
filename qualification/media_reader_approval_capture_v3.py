"""One-process Media approval capture and inert grant; no fifth proof.

This class has no operator CLI and is not connected to the native qualifier.
A future reviewed path must add native run handling under the same lock.
No readable receipt or grant is a run permit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
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
from qualification.admission import AdmissionController, AdmissionDenied, Lease

BASE = "media-native-reader-run-approval-v3"
RECEIPT = "receipt.json"
RECHECK = "recheck.json"
ATTEMPT_RECHECK = "attempt-recheck.json"
COMMENT_WAIT_SECONDS = 300
_LIVE_ATTEMPT_GATES: dict[object, tuple[Path, Lease, Callable[[], None], int, int]] = {}
_GATE_LOCK = threading.Lock()


class CaptureRefused(ValueError):
    """The receipt is absent, uncertain, already used, or unbound."""


def _refuse(reason: str) -> None:
    raise CaptureRefused("reader_native_v3_capture_" + reason) from None


def _consume_attempt_gate(root: Path, gate: object) -> None:
    """Consume only a live in-process gate issued under the retained lease."""
    with _GATE_LOCK:
        try:
            state = _LIVE_ATTEMPT_GATES.pop(gate, None)
        except TypeError:
            state = None
    if state is None:
        raise ValueError("reader_native_attempt_limit")
    expected, lease, check, owner_pid, owner_thread = state
    if (
        root != expected
        or owner_pid != os.getpid()
        or owner_thread != threading.get_ident()
        or lease._fds is None
        or not lease._lifecycle.locked()
    ):
        raise ValueError("reader_native_attempt_limit")
    check()


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


def _persist_attempt_recheck(root: Path, snapshot: dict) -> None:
    """Durably record the last fetched decision before any future attempt path."""
    record = {"schema": "zelo-media-native-reader-v3-attempt-recheck-v1", "snapshot": snapshot}
    raw = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > 16384:
        _refuse("attempt_recheck_bound")
    root_fd = base_fd = file_fd = -1
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        _trusted_directory(root_fd, private=False)
        base_fd = os.open(
            BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd
        )
        _trusted_directory(base_fd, private=True)
        file_fd = os.open(
            ATTEMPT_RECHECK,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=base_fd,
        )
        view = memoryview(raw)
        while view:
            count = os.write(file_fd, view)
            if count <= 0:
                _refuse("attempt_recheck_write")
            view = view[count:]
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = -1
        os.fsync(base_fd)
        check = os.open(ATTEMPT_RECHECK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=base_fd)
        try:
            if os.read(check, len(raw) + 1) != raw:
                _refuse("attempt_recheck_readback")
        finally:
            os.close(check)
    except OSError:
        _refuse("attempt_recheck_storage")
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
        self._challenge: str | None = None
        self._issued: datetime | None = None
        self._comment_id: int | None = None
        self._history: list[dict] | None = None
        self._lease: Lease | None = None
        self._attempt_rechecked = False

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

        def persist_held_grant(root: Path, snapshot: dict) -> None:
            """The only writer is this one-shot capture's held-lock callback."""
            observed_root, record = v3._history(root)
            if observed_root != root or v3._source_identity() != self._source:
                _refuse("grant_drift")
            durable = v3.scope._json(v3.scope._read(root / BASE / RECHECK, maximum=16384))
            if durable != {
                "schema": "zelo-media-native-reader-v3-approval-recheck-v1",
                "snapshot": snapshot,
            }:
                _refuse("grant_recheck_drift")
            if (
                type(snapshot) is not dict
                or snapshot.get("runtime_root") != str(root)
                or (snapshot.get("source_head"), snapshot.get("source_tree")) != self._source
                or snapshot.get("proof_history_sha256")
                != approval._history_digest(record["proof_history"])
                or type(snapshot.get("comment_id")) is not int
                or type(snapshot.get("comment_body")) is not str
                or hashlib.sha256(snapshot["comment_body"].encode()).hexdigest()
                != snapshot.get("comment_body_sha256")
                or any(
                    type(snapshot.get(key)) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", snapshot[key]) is None
                    for key in ("comment_body_sha256", "challenge_sha256")
                )
            ):
                _refuse("grant_binding")
            try:
                body = json.loads(snapshot["comment_body"])
                expiry = approval._time(body["expires_utc"])
            except (KeyError, TypeError, ValueError):
                _refuse("grant_binding")
            root_fd = base_fd = -1
            try:
                root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                _trusted_directory(root_fd, private=False)
                # mkdir is the one-run consumption point. Sample expiry immediately before it.
                now = clock()
                if (
                    type(now) is not datetime
                    or now.tzinfo is None
                    or now.utcoffset() != timedelta(0)
                ):
                    _refuse("grant_clock")
                if now >= expiry:
                    _refuse("grant_expired")
                os.mkdir(v3.BASE, 0o700, dir_fd=root_fd)
                os.fsync(root_fd)
                record.update(
                    source_head=self._source[0],
                    source_tree=self._source[1],
                    created_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    approval_comment_id=snapshot["comment_id"],
                    approval_body_sha256=snapshot["comment_body_sha256"],
                    approval_challenge_sha256=snapshot["challenge_sha256"],
                )
                raw = (
                    json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
                ).encode()
                seal = {
                    "schema": "zelo-media-native-reader-extra-attempt-v3-seal-v1",
                    "grant_sha256": hashlib.sha256(raw).hexdigest(),
                    "source_head": self._source[0],
                    "source_tree": self._source[1],
                }
                seal_raw = (json.dumps(seal, sort_keys=True, allow_nan=False) + "\n").encode()
                base_fd = os.open(
                    v3.BASE,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=root_fd,
                )
                _trusted_directory(base_fd, private=True)
                for name, payload in ((v3.GRANT, raw), (v3.SEAL, seal_raw)):
                    fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                        dir_fd=base_fd,
                    )
                    try:
                        view = memoryview(payload)
                        while view:
                            count = os.write(fd, view)
                            if count <= 0:
                                _refuse("grant_write")
                            view = view[count:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.fsync(base_fd)
                if v3.validate(root, held_admission=True) != record:
                    _refuse("grant_readback")
            except OSError:
                _refuse("grant_storage")
            finally:
                for fd in (base_fd, root_fd):
                    if fd >= 0:
                        os.close(fd)

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
            persist_held_grant(root, fresh)
            self._snapshot = fresh
            self._challenge = challenge
            self._issued = issued
            self._comment_id = comment_id
            self._history = copy.deepcopy(history)

        lease = AdmissionController(self._root / "media-elf-metadata-admission").acquire_after(
            policy_digest, before_marker
        )
        if self._snapshot is None:
            _refuse("snapshot_missing")
        self._lease = lease
        return lease, self._snapshot

    def recheck_before_attempt(
        self,
        lease: Lease,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        allocate: Callable[[Path, object], object] | None = None,
    ) -> dict | tuple[dict, object]:
        """One-use decision recheck; optional trusted allocation stays under the lock."""
        if (
            lease is not self._lease
            or lease._fds is None
            or self._snapshot is None
            or self._challenge is None
            or self._issued is None
            or self._comment_id is None
            or self._history is None
        ):
            _refuse("attempt_lease")
        try:
            with lease.retained():
                if self._attempt_rechecked:
                    _refuse("attempt_recheck_used")
                self._attempt_rechecked = True
                root, record = v3._history(self._root)
                if (
                    v3._source_identity() != self._source
                    or record["proof_history"] != self._history
                    or v3.validate(root, held_admission=True)["approval_comment_id"]
                    != self._comment_id
                ):
                    _refuse("attempt_drift")
                preflight.observe(root)
                if v3._history(root)[1]["proof_history"] != self._history:
                    _refuse("attempt_resource_drift")
                fresh = approval.recheck_snapshot(
                    get=approval.gh_api_get,
                    original=self._snapshot,
                    expected_comment_id=self._comment_id,
                    expected_challenge=self._challenge,
                    challenge_issued_at=self._issued,
                    md_account_id=approval.MD_ACCOUNT_ID,
                    expected_root=root,
                    source_head=self._source[0],
                    source_tree=self._source[1],
                    proof_history=self._history,
                    clock=clock,
                )
                if (
                    lease._fds is None
                    or v3._source_identity() != self._source
                    or v3._history(root)[1]["proof_history"] != self._history
                    or v3.validate(root, held_admission=True)["approval_comment_id"]
                    != self._comment_id
                ):
                    _refuse("attempt_final_drift")
                _persist_attempt_recheck(root, fresh)
                if allocate is not None:
                    if not callable(allocate):
                        _refuse("attempt_allocator")
                    try:
                        expiry = approval._time(json.loads(fresh["comment_body"])["expires_utc"])
                    except (KeyError, TypeError, ValueError):
                        _refuse("attempt_expiry")

                    def check_before_mkdir() -> None:
                        now = clock()
                        if (
                            type(now) is not datetime
                            or now.tzinfo is None
                            or now.utcoffset() != timedelta(0)
                            or now >= expiry
                        ):
                            _refuse("attempt_expired")

                    check_before_mkdir()
                    gate = object()
                    with _GATE_LOCK:
                        _LIVE_ATTEMPT_GATES[gate] = (
                            root,
                            lease,
                            check_before_mkdir,
                            os.getpid(),
                            threading.get_ident(),
                        )
                    try:
                        return fresh, allocate(root, gate)
                    finally:
                        with _GATE_LOCK:
                            _LIVE_ATTEMPT_GATES.pop(gate, None)
                return fresh
        except AdmissionDenied:
            _refuse("attempt_lease")
