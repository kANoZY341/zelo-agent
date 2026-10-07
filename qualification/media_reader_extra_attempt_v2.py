"""Versioned one-use continuation after three immutable native-reader refusals.

The validator and grant writer are offline-testable. Importing this module has no
runtime effect; grant_once is never called by qualification and must be invoked
separately after a bounded, read-only resource observation.
"""

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

from qualification import media_reader_extra_attempt as v1
from qualification import media_reader_reconcile as first
from qualification import media_reader_scope as scope

ROOT = Path(__file__).resolve().parents[1]
BASE = "media-native-reader-extra-attempt-v2"
GRANT = "grant.json"
V1_BASE = "media-native-reader-extra-attempt"
THIRD = "native-reader-proof-x7s79c20"
THIRD_POLICY_SHA256 = "318b9685933696b31cffe7ff3c421eacfebc2bbaf9f9f64a4d5d7fc2ea90c1e3"
THIRD_PROOF_SHA256 = "d67229530e5038e615bc094d4febe269afef33fca78773300f69411f966b624f"
V1_GRANT_SHA256 = "2449adcd63644198c18a0fec9e6f117fab8a12b9b0bde3ea6e337e004a163018"
SCOPE_SHA256 = "87eee881609de31e0fcbeac3ab5c0470b956aa3a336fbbadbc5eda162fe9a87b"
RECONCILIATION_SHA256 = "04fc0c4607922f24c3aea63fa5bd72a27334ae652174d4b51f12e039d155dad1"
SECOND_SOURCE_HEAD = "9200eb298a31b9438da21165174cf9bb15bd473f"
SECOND_SOURCE_TREE = "260e081ebe36389a384e7fcd86cc354a3a518819"
THIRD_SOURCE_HEAD = "a33f2869012263073516e9ae392fff122712e582"
THIRD_SOURCE_TREE = "437e20eade2aac7506e61e1c3386f1b010817c2d"
EXTRA_LIMIT = 4


class RecoveryRefused(ValueError):
    """The historical ledger or one-additional-attempt grant is not exact."""


def _refuse(code: str) -> None:
    raise RecoveryRefused("reader_native_extra_v2_" + code)


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
    return parts[0], parts[1]


def _proof_entry(
    root: Path, name: str, policy_sha: str, proof_sha: str, *, attempt_limit: int, proof: dict
) -> dict:
    path = root / scope.PROOF_BASE / name
    scope._directory(path, private=True)
    expected_files = (
        ["cases.json", "docker-config", "policy.json"]
        if name == first.ATTEMPT_NAME
        else ["cases.json", "policy.json"]
    )
    if sorted(item.name for item in path.iterdir()) != expected_files:
        _refuse("proof_inventory")
    if name == first.ATTEMPT_NAME:
        config = path / "docker-config"
        scope._directory(config, private=True)
        if list(config.iterdir()):
            _refuse("first_config")
    policy_raw = scope._read(path / "policy.json")
    proof_raw = scope._read(path / "cases.json")
    if _digest(policy_raw) != policy_sha or _digest(proof_raw) != proof_sha:
        _refuse("proof_pin")
    policy = scope._json(policy_raw)
    if (
        policy.get("schema") != "zelo-media-native-reader-synthetic-policy-v1"
        or type(policy.get("attempt_limit")) is not int
        or policy["attempt_limit"] != attempt_limit
        or policy.get("network") != "none"
        or policy.get("runtime_authority") is not False
        or scope._json(proof_raw) != proof
    ):
        _refuse("proof_refusal")
    return {
        "attempt": name,
        "policy_sha256": policy_sha,
        "proof_sha256": proof_sha,
    }


def _history(runtime_root: Path) -> tuple[Path, dict]:
    root = scope._canonical_root(runtime_root)
    scope_raw = scope._read(root / scope.SCOPE_FILENAME)
    if _digest(scope_raw) != SCOPE_SHA256:
        _refuse("scope_pin")
    if scope._json(scope_raw).get("runtime_root") != str(root):
        _refuse("scope_path")
    pending_path, pending_raw, _ = scope._pending_evidence(root)
    if _digest(pending_raw) != RECONCILIATION_SHA256:
        _refuse("reconciliation_pin")

    base = root / scope.PROOF_BASE
    scope._directory(base, private=True)
    names = sorted(item.name for item in base.iterdir())
    expected_names = sorted((first.ATTEMPT_NAME, v1.SECOND, THIRD))
    if names != expected_names:
        _refuse("attempt_inventory")

    refused = {
        "runtime_authority": False,
        "status": "refused",
        "support_release_confirmed": False,
    }
    entries = [
        _proof_entry(
            root,
            first.ATTEMPT_NAME,
            first.POLICY_SHA256,
            first.CASES_SHA256,
            attempt_limit=2,
            proof={**refused, "stage": "lease-preflight"},
        ),
        _proof_entry(
            root,
            v1.SECOND,
            v1.SECOND_POLICY_SHA256,
            v1.SECOND_PROOF_SHA256,
            attempt_limit=2,
            proof={**refused, "stage": "build"},
        ),
        _proof_entry(
            root,
            THIRD,
            THIRD_POLICY_SHA256,
            THIRD_PROOF_SHA256,
            attempt_limit=3,
            proof={**refused, "stage": "build"},
        ),
    ]

    entries[0]["source_head"] = first.REFUSAL_HEAD
    entries[0]["source_tree"] = first.REFUSAL_TREE
    entries[1]["source_head"] = SECOND_SOURCE_HEAD
    entries[1]["source_tree"] = SECOND_SOURCE_TREE
    entries[2]["source_head"] = THIRD_SOURCE_HEAD
    entries[2]["source_tree"] = THIRD_SOURCE_TREE

    v1_dir = root / V1_BASE
    scope._directory(v1_dir, private=True)
    if sorted(item.name for item in v1_dir.iterdir()) != [v1.GRANT]:
        _refuse("v1_inventory")
    v1_raw = scope._read(v1_dir / v1.GRANT, maximum=65536)
    if _digest(v1_raw) != V1_GRANT_SHA256:
        _refuse("v1_pin")
    v1_record = scope._json(v1_raw)
    if (
        v1_record.get("schema") != "zelo-media-native-reader-extra-attempt-v1"
        or v1_record.get("attempts_used_before") != 2
        or v1_record.get("attempt_limit_after") != 3
        or v1_record.get("additional_attempts") != 1
        or v1_record.get("attempt_reset") is not False
        or v1_record.get("proof_rewritten") is not False
        or v1_record.get("proof_deleted") is not False
        or v1_record.get("runtime_authority") is not False
        or v1_record.get("source_head") != THIRD_SOURCE_HEAD
        or v1_record.get("source_tree") != THIRD_SOURCE_TREE
    ):
        _refuse("v1_binding")

    return root, {
        "schema": "zelo-media-native-reader-extra-attempt-v2",
        "status": "one-additional-attempt-granted",
        "runtime_root": str(root),
        "scope_sha256": SCOPE_SHA256,
        "reconciliation_path": str(pending_path.relative_to(root)),
        "reconciliation_sha256": RECONCILIATION_SHA256,
        "proof_history": entries,
        "existing_grant_path": f"{V1_BASE}/{v1.GRANT}",
        "existing_grant_sha256": V1_GRANT_SHA256,
        "existing_grant_source_head": THIRD_SOURCE_HEAD,
        "existing_grant_source_tree": THIRD_SOURCE_TREE,
        "attempts_used_before": 3,
        "attempt_limit_after": EXTRA_LIMIT,
        "additional_attempts": 1,
        "attempt_reset": False,
        "proof_rewritten": False,
        "proof_deleted": False,
        "runtime_authority": False,
        "network": "none",
        "pull": False,
        "whole_seconds": 240,
        "case_seconds": 20,
        "build_seconds": 90,
        "read_only_rootfs": True,
        "resource_scope": "all-org.zelo.media-reader.lease",
        "cleanup_scope": "exact-new-lease-labeled-resources-only",
        "cleanup_required_before_return": True,
        "cleanup_errors_permitted": False,
        "preflight_quiet_seconds": 2,
        "docker_container_count_before": 0,
        "docker_image_count_before": 0,
        "global_prune": False,
        "source_commit_required_clean": True,
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


def grant_once(
    runtime_root: Path,
    observe: Callable[[Path], None],
    *,
    expected_source: tuple[str, str],
) -> dict:
    if (
        type(expected_source) is not tuple
        or len(expected_source) != 2
        or any(
            type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in expected_source
        )
    ):
        _refuse("source_approval")
    root, record = _history(runtime_root)
    scope._check_unheld_admission(root)
    source_head, source_tree = _source_identity()
    if (source_head, source_tree) != expected_source:
        _refuse("source_approval")
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
