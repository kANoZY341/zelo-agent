"""Bind the native-reader attempt ledger to one explicit persistent runtime scope."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

from qualification import media_reader_reconcile as reconcile

SCOPE_SCHEMA = "zelo-media-native-reader-runtime-scope-v1"
SCOPE_FILENAME = "media-native-reader-scope.json"
PROOF_BASE = "media-native-reader"
RECON_BASE = "media-native-reader-reconciliation"
RECONCILIATION_DIRECTORY = "reconcile-1fudorw3"
ADMISSION_BASE = "media-elf-metadata-admission"
MAX_BYTES = 2_097_152
RECONCILIATION_SHA256 = "04fc0c4607922f24c3aea63fa5bd72a27334ae652174d4b51f12e039d155dad1"


class ScopeRefused(ValueError):
    """Missing, replayed, or mismatched persistent attempt scope."""


def _refuse(code: str) -> None:
    raise ScopeRefused("reader_native_scope_" + code)


def _directory(path: Path, *, private: bool) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        _refuse("missing")
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or (private and mode != 0o700)
        or (not private and mode & 0o022)
    ):
        _refuse("directory")
    return info


def _read(path: Path, *, maximum: int = MAX_BYTES) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        _refuse("file")
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or not 0 < info.st_size <= maximum
        ):
            _refuse("file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _refuse("size")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if len(raw) != info.st_size:
            _refuse("changed")
        return raw
    finally:
        os.close(fd)


def _pairs(rows: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in rows:
        if type(key) is not str or key in value:
            _refuse("json")
        value[key] = item
    return value


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        _refuse("json")
    if type(value) is not dict:
        _refuse("json")
    return value


def _canonical_root(runtime_root: Path) -> Path:
    path = Path(runtime_root)
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        _refuse("path")
    try:
        normalized = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
    except OSError:
        _refuse("path")
    if normalized != resolved:
        _refuse("path")
    info = _directory(resolved, private=False)
    if info.st_gid != os.getegid():
        _refuse("path")
    return resolved


def _check_unheld_admission(runtime_root: Path) -> None:
    directory = runtime_root / ADMISSION_BASE
    _directory(directory, private=True)
    try:
        fd = os.open(
            directory / "admission.lock",
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        )
    except OSError:
        _refuse("lock")
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != 0
        ):
            _refuse("lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _refuse("live_lock")
        marker_path = directory / "active.marker"
        marker_fd = os.open(marker_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            marker_info = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker_info.st_mode)
                or marker_info.st_uid != os.geteuid()
                or marker_info.st_gid != os.getegid()
                or stat.S_IMODE(marker_info.st_mode) != 0o600
                or marker_info.st_nlink != 1
                or marker_info.st_size != 0
            ):
                _refuse("marker")
        finally:
            os.close(marker_fd)
    except OSError:
        _refuse("lock")
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _pending_evidence(runtime_root: Path) -> tuple[Path, bytes, dict]:
    base = runtime_root / RECON_BASE
    _directory(base, private=True)
    entries = sorted(base.iterdir(), key=lambda item: item.name)
    if len(entries) != 1 or entries[0].name != RECONCILIATION_DIRECTORY:
        _refuse("reconciliation_inventory")
    evidence_dir = entries[0]
    _directory(evidence_dir, private=True)
    if sorted(item.name for item in evidence_dir.iterdir()) != ["pending.json"]:
        _refuse("reconciliation_inventory")
    evidence = evidence_dir / "pending.json"
    raw = _read(evidence)
    if hashlib.sha256(raw).hexdigest() != RECONCILIATION_SHA256:
        _refuse("reconciliation_digest")
    value = _json(raw)
    expected = {
        "schema": "zelo-media-native-reader-reconciliation-v1",
        "status": "pending-marker-clear",
        "failed_source_head": reconcile.REFUSAL_HEAD,
        "failed_source_tree": reconcile.REFUSAL_TREE,
        "failed_attempt": reconcile.ATTEMPT_NAME,
        "failed_lease": reconcile.LEASE_IDENTITY,
        "policy_sha256": reconcile.POLICY_SHA256,
        "proof_sha256": reconcile.CASES_SHA256,
        "marker_sha256": reconcile.MARKER_SHA256,
        "docker_container_count_before": 0,
        "docker_image_count_before": 0,
        "docker_container_count_after": 0,
        "docker_image_count_after": 0,
        "quiet_seconds": 2,
        "attempts_used": 1,
        "attempt_limit": 2,
        "proof_rewritten": False,
        "proof_deleted": False,
        "attempt_reset": False,
        "runtime_authority": False,
    }
    if any(
        type(value.get(key)) is not type(expected_value) or value[key] != expected_value
        for key, expected_value in expected.items()
    ):
        _refuse("reconciliation")
    if set(value) != set(expected) | {"quiet_start", "quiet_end"}:
        _refuse("reconciliation")
    try:
        quiet_start = datetime.strptime(value["quiet_start"], "%Y-%m-%dT%H:%M:%SZ")
        quiet_end = datetime.strptime(value["quiet_end"], "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        _refuse("reconciliation")
    if quiet_end - quiet_start != timedelta(seconds=2):
        _refuse("reconciliation")
    return evidence, raw, value


def _attempt1(runtime_root: Path) -> tuple[Path, bytes, bytes]:
    base = runtime_root / PROOF_BASE
    _directory(base, private=True)
    entries = sorted(base.iterdir(), key=lambda item: item.name)
    if len(entries) != 1:
        _refuse("attempt_count")
    attempt = entries[0]
    if attempt.name != reconcile.ATTEMPT_NAME:
        _refuse("attempt_identity")
    _directory(attempt, private=True)
    if sorted(item.name for item in attempt.iterdir()) != [
        "cases.json",
        "docker-config",
        "policy.json",
    ]:
        _refuse("attempt_inventory")
    config = attempt / "docker-config"
    _directory(config, private=True)
    if list(config.iterdir()):
        _refuse("attempt_config")
    policy = _read(attempt / "policy.json")
    proof = _read(attempt / "cases.json")
    if (
        hashlib.sha256(policy).hexdigest() != reconcile.POLICY_SHA256
        or hashlib.sha256(proof).hexdigest() != reconcile.CASES_SHA256
    ):
        _refuse("attempt_digest")
    return attempt, policy, proof


def _record(runtime_root: Path) -> dict:
    root = _canonical_root(runtime_root)
    _directory(root / PROOF_BASE, private=True)
    _, policy, proof = _attempt1(root)
    evidence_path, evidence_raw, evidence = _pending_evidence(root)
    _check_unheld_admission(root)
    return {
        "schema": SCOPE_SCHEMA,
        "runtime_root": str(root),
        "failed_attempt": reconcile.ATTEMPT_NAME,
        "failed_source_head": reconcile.REFUSAL_HEAD,
        "failed_source_tree": reconcile.REFUSAL_TREE,
        "failed_lease": reconcile.LEASE_IDENTITY,
        "policy_sha256": hashlib.sha256(policy).hexdigest(),
        "proof_sha256": hashlib.sha256(proof).hexdigest(),
        "reconciliation_path": str(evidence_path.relative_to(root)),
        "reconciliation_sha256": hashlib.sha256(evidence_raw).hexdigest(),
        "attempts_used": evidence["attempts_used"],
        "attempt_limit": evidence["attempt_limit"],
        "runtime_authority": False,
    }


def bind_runtime_scope(runtime_root: Path) -> dict:
    """Create one path-bound manifest; never copy, repair, or overwrite evidence."""
    record = _record(runtime_root)
    path = Path(record["runtime_root"]) / SCOPE_FILENAME
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
    except FileExistsError:
        _refuse("already_bound")
    except OSError:
        _refuse("write")
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                _refuse("write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(
        record["runtime_root"],
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return record


def validate_runtime_scope(runtime_root: Path) -> dict:
    """Require the immutable binding and unchanged attempt-1/reconciliation state."""
    expected = _record(runtime_root)
    raw = _read(Path(expected["runtime_root"]) / SCOPE_FILENAME)
    if _json(raw) != expected:
        _refuse("binding_mismatch")
    return expected
