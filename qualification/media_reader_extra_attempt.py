"""One-use, append-only native reader attempt-2 recovery contract."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from qualification import media_reader_reconcile as first
from qualification import media_reader_scope as scope

ROOT = Path(__file__).resolve().parents[1]
BASE = "media-native-reader-extra-attempt"
GRANT = "grant.json"
SECOND = "native-reader-proof-672ubxw7"
SCOPE_SHA256 = "87eee881609de31e0fcbeac3ab5c0470b956aa3a336fbbadbc5eda162fe9a87b"
SECOND_POLICY_SHA256 = "1f3866213f07ca314e800df192c239674b96bad52ac8c15c0a5d0d9669869f52"
SECOND_PROOF_SHA256 = "d67229530e5038e615bc094d4febe269afef33fca78773300f69411f966b624f"
CORRECTED_BASE_HEAD = "9200eb298a31b9438da21165174cf9bb15bd473f"
EXTRA_LIMIT = 3


class RecoveryRefused(ValueError):
    """The historical ledger or additional-attempt grant is not exact."""


def _refuse(code: str) -> None:
    raise RecoveryRefused("reader_native_extra_" + code)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_identity() -> tuple[str, str]:
    try:
        status = subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        raw = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD", "HEAD^{tree}"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        _refuse("source")
    parts = raw.decode("ascii", "strict").splitlines()
    if status or len(parts) != 2 or any(re.fullmatch(r"[0-9a-f]{40}", p) is None for p in parts):
        _refuse("source")
    try:
        ancestor = subprocess.check_output(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", CORRECTED_BASE_HEAD, parts[0]],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        _refuse("source_base")
    if ancestor != b"":
        _refuse("source_base")
    return parts[0], parts[1]


def _history(runtime_root: Path) -> tuple[Path, dict]:
    root = scope._canonical_root(runtime_root)
    raw_scope = scope._read(root / scope.SCOPE_FILENAME)
    if _digest(raw_scope) != SCOPE_SHA256:
        _refuse("scope_pin")
    if scope._json(raw_scope).get("runtime_root") != str(root):
        _refuse("scope_path")
    scope._pending_evidence(root)
    base = root / scope.PROOF_BASE
    scope._directory(base, private=True)
    if sorted(item.name for item in base.iterdir()) != sorted((first.ATTEMPT_NAME, SECOND)):
        _refuse("attempt_inventory")
    old = base / first.ATTEMPT_NAME
    scope._directory(old, private=True)
    if sorted(item.name for item in old.iterdir()) != [
        "cases.json",
        "docker-config",
        "policy.json",
    ]:
        _refuse("first_inventory")
    scope._directory(old / "docker-config", private=True)
    if list((old / "docker-config").iterdir()):
        _refuse("first_config")
    if (
        _digest(scope._read(old / "policy.json")) != first.POLICY_SHA256
        or _digest(scope._read(old / "cases.json")) != first.CASES_SHA256
    ):
        _refuse("first_pin")
    second = base / SECOND
    scope._directory(second, private=True)
    if sorted(item.name for item in second.iterdir()) != ["cases.json", "policy.json"]:
        _refuse("second_inventory")
    policy_raw = scope._read(second / "policy.json")
    proof_raw = scope._read(second / "cases.json")
    if _digest(policy_raw) != SECOND_POLICY_SHA256 or _digest(proof_raw) != SECOND_PROOF_SHA256:
        _refuse("second_pin")
    policy = scope._json(policy_raw)
    proof = scope._json(proof_raw)
    if (
        policy.get("schema") != "zelo-media-native-reader-synthetic-policy-v1"
        or type(policy.get("attempt_limit")) is not int
        or policy["attempt_limit"] != 2
        or policy.get("network") != "none"
        or policy.get("runtime_authority") is not False
        or proof
        != {
            "runtime_authority": False,
            "stage": "build",
            "status": "refused",
            "support_release_confirmed": False,
        }
    ):
        _refuse("second_refusal")
    return root, {
        "schema": "zelo-media-native-reader-extra-attempt-v1",
        "status": "one-additional-attempt-granted",
        "runtime_root": str(root),
        "scope_sha256": SCOPE_SHA256,
        "first_attempt": first.ATTEMPT_NAME,
        "first_policy_sha256": first.POLICY_SHA256,
        "first_proof_sha256": first.CASES_SHA256,
        "first_reconciliation_sha256": scope.RECONCILIATION_SHA256,
        "second_attempt": SECOND,
        "second_policy_sha256": SECOND_POLICY_SHA256,
        "second_proof_sha256": SECOND_PROOF_SHA256,
        "corrected_base_head": CORRECTED_BASE_HEAD,
        "diagnostic_source_head": "b6ee2fb0bdacb78092c8aba763e66f205a394791",
        "diagnostic_dockerfile_sha256": (
            "1badbad2e03f4130a1998263df6043f703aaddcd8ac0bc2b7e7d1003ba57a7e8"
        ),
        "diagnostic_result": "legacy-builder-copy-chmod-refused",
        "diagnostic_owned_cleanup_confirmed": True,
        "attempts_used_before": 2,
        "attempt_limit_after": EXTRA_LIMIT,
        "additional_attempts": 1,
        "attempt_reset": False,
        "proof_rewritten": False,
        "proof_deleted": False,
        "runtime_authority": False,
        "attempt2_lease_unrecorded": True,
        "resource_scope": "all-org.zelo.media-reader.lease",
        "docker_container_count_before": 0,
        "docker_image_count_before": 0,
        "docker_container_count_after": 0,
        "docker_image_count_after": 0,
        "quiet_seconds": 2,
        "daemon_events_observed": 0,
    }


def validate(runtime_root: Path, *, held_admission: bool = False) -> dict:
    root, expected = _history(runtime_root)
    if not held_admission:
        scope._check_unheld_admission(root)
    base = root / BASE
    if not base.exists():
        _refuse("grant_missing")
    scope._directory(base, private=True)
    if sorted(item.name for item in base.iterdir()) != [GRANT]:
        _refuse("grant_inventory")
    grant = scope._json(scope._read(base / GRANT, maximum=65536))
    source_head, source_tree = _source_identity()
    expected["source_head"] = source_head
    expected["source_tree"] = source_tree
    if set(grant) != set(expected) | {"created_utc"}:
        _refuse("grant_shape")
    if any(
        type(grant[key]) is not type(value) or grant[key] != value
        for key, value in expected.items()
    ):
        _refuse("grant_binding")
    stamp = grant["created_utc"]
    if type(stamp) is not str or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp) is None:
        _refuse("grant_time")
    try:
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _refuse("grant_time")
    return grant


def _require_empty_marker(admission: Path) -> None:
    marker_fd = os.open(admission / "active.marker", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(marker_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != 0
        ):
            _refuse("marker")
    finally:
        os.close(marker_fd)


def grant_once(runtime_root: Path, observe: Callable[[Path], None]) -> dict:
    root, record = _history(runtime_root)
    scope._check_unheld_admission(root)
    source_head, source_tree = _source_identity()
    if (root / BASE).exists():
        _refuse("already_granted")
    admission = root / scope.ADMISSION_BASE
    fd = os.open(admission / "admission.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != 0
            or info.st_nlink != 1
        ):
            _refuse("lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _refuse("live_lock")
        _history(root)
        _require_empty_marker(admission)
        observe(root)
        _history(root)
        _require_empty_marker(admission)
        if _source_identity() != (source_head, source_tree):
            _refuse("source_drift")
        if (root / BASE).exists():
            _refuse("already_granted")
        record["source_head"] = source_head
        record["source_tree"] = source_tree
        record["created_utc"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = root / BASE
        base.mkdir(mode=0o700)
        raw = (json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        target = base / GRANT
        out = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        try:
            view = memoryview(raw)
            while view:
                count = os.write(out, view)
                if count <= 0:
                    _refuse("write")
                view = view[count:]
            os.fsync(out)
        finally:
            os.close(out)
        directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        runtime_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(runtime_fd)
        finally:
            os.close(runtime_fd)
        validate(root, held_admission=True)
        return record
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
