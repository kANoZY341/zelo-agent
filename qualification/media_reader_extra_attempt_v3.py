"""Dormant, one-use continuation contract after four retained Media refusals.

No grant is created on import or by the native qualifier. A separate operator
step may append one exact grant only after source review and authorization.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from qualification import media_reader_extra_attempt as v1
from qualification import media_reader_extra_attempt_v2 as v2
from qualification import media_reader_reconcile as first
from qualification import media_reader_scope as scope

BASE = "media-native-reader-extra-attempt-v3"
GRANT = "grant.json"
SEAL = "seal.json"
FOURTH = "native-reader-proof-kvhyvgri"
FOURTH_POLICY_SHA256 = "51a8532bf63d2f603d6e671815850aa86e7984b7040918e0e86fe04569a364b6"
FOURTH_PROOF_SHA256 = "2c3bd1f7b130d3ce57bf317b5119971cdb30b68820b2e4f19503c1c0d14e1251"
FOURTH_SOURCE_HEAD = "337435398356dd84f57b38d77a43c6549d3c506b"
FOURTH_SOURCE_TREE = "402ce08912876f8e3d33507ac9fb1b284ca6b1a2"
V2_GRANT_SHA256 = "4c3dbda2ae77db193c296dbffb105b18f14d306541c6793675e04f0e600d1271"
SCOPE_SHA256 = v2.SCOPE_SHA256
RECONCILIATION_SHA256 = v2.RECONCILIATION_SHA256
EXTRA_LIMIT = 5


class RecoveryRefused(ValueError):
    """The four refusals, prior grants or new one-use grant are not exact."""


def _refuse(code: str) -> None:
    raise RecoveryRefused("reader_native_extra_v3_" + code)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_identity() -> tuple[str, str]:
    return v2._source_identity()


def _fourth_entry(root: Path) -> dict:
    path = root / scope.PROOF_BASE / FOURTH
    scope._directory(path, private=True)
    if sorted(item.name for item in path.iterdir()) != ["cases.json", "policy.json"]:
        _refuse("fourth_inventory")
    policy_raw = scope._read(path / "policy.json")
    proof_raw = scope._read(path / "cases.json")
    if _digest(policy_raw) != FOURTH_POLICY_SHA256 or _digest(proof_raw) != FOURTH_PROOF_SHA256:
        _refuse("fourth_pin")
    policy = scope._json(policy_raw)
    proof = scope._json(proof_raw)
    failure = proof.get("build_failure")
    if (
        policy.get("schema") != "zelo-media-native-reader-synthetic-policy-v1"
        or type(policy.get("attempt_limit")) is not int
        or policy["attempt_limit"] != 4
        or policy.get("network") != "none"
        or policy.get("runtime_authority") is not False
        or proof.get("status") != "refused"
        or proof.get("stage") != "build"
        or proof.get("support_release_confirmed") is not False
        or proof.get("runtime_authority") is not False
        or type(failure) is not dict
        or failure.get("operation") != "build"
        or failure.get("kind") != "docker_command_exit"
        or type(failure.get("exit_code")) is not int
        or failure["exit_code"] != 2
        or failure.get("overflowed_streams") != ["err"]
        or failure.get("output_category") != "unclassified"
    ):
        _refuse("fourth_refusal")
    return {
        "attempt": FOURTH,
        "policy_sha256": FOURTH_POLICY_SHA256,
        "proof_sha256": FOURTH_PROOF_SHA256,
        "source_head": FOURTH_SOURCE_HEAD,
        "source_tree": FOURTH_SOURCE_TREE,
    }


def _history(runtime_root: Path) -> tuple[Path, dict]:
    root = scope._canonical_root(runtime_root)
    scope_raw = scope._read(root / scope.SCOPE_FILENAME)
    if _digest(scope_raw) != SCOPE_SHA256 or scope._json(scope_raw).get("runtime_root") != str(
        root
    ):
        _refuse("scope_pin")
    pending_path, pending_raw, _ = scope._pending_evidence(root)
    if _digest(pending_raw) != RECONCILIATION_SHA256:
        _refuse("reconciliation_pin")
    base = root / scope.PROOF_BASE
    scope._directory(base, private=True)
    expected_names = sorted((first.ATTEMPT_NAME, v1.SECOND, v2.THIRD, FOURTH))
    if sorted(item.name for item in base.iterdir()) != expected_names:
        _refuse("attempt_inventory")
    refused = {"runtime_authority": False, "status": "refused", "support_release_confirmed": False}
    entries = [
        v2._proof_entry(
            root,
            first.ATTEMPT_NAME,
            first.POLICY_SHA256,
            first.CASES_SHA256,
            attempt_limit=2,
            proof={**refused, "stage": "lease-preflight"},
        ),
        v2._proof_entry(
            root,
            v1.SECOND,
            v1.SECOND_POLICY_SHA256,
            v1.SECOND_PROOF_SHA256,
            attempt_limit=2,
            proof={**refused, "stage": "build"},
        ),
        v2._proof_entry(
            root,
            v2.THIRD,
            v2.THIRD_POLICY_SHA256,
            v2.THIRD_PROOF_SHA256,
            attempt_limit=3,
            proof={**refused, "stage": "build"},
        ),
        _fourth_entry(root),
    ]
    for entry, head, tree in zip(
        entries[:3],
        (first.REFUSAL_HEAD, v2.SECOND_SOURCE_HEAD, v2.THIRD_SOURCE_HEAD),
        (first.REFUSAL_TREE, v2.SECOND_SOURCE_TREE, v2.THIRD_SOURCE_TREE),
        strict=True,
    ):
        entry["source_head"] = head
        entry["source_tree"] = tree

    v1_dir = root / v2.V1_BASE
    scope._directory(v1_dir, private=True)
    if sorted(item.name for item in v1_dir.iterdir()) != [v1.GRANT]:
        _refuse("v1_inventory")
    v1_raw = scope._read(v1_dir / v1.GRANT, maximum=65536)
    if _digest(v1_raw) != v2.V1_GRANT_SHA256:
        _refuse("v1_pin")
    old = scope._json(v1_raw)
    if (
        old.get("schema") != "zelo-media-native-reader-extra-attempt-v1"
        or old.get("attempt_limit_after") != 3
        or old.get("source_head") != v2.THIRD_SOURCE_HEAD
        or old.get("source_tree") != v2.THIRD_SOURCE_TREE
        or old.get("runtime_authority") is not False
    ):
        _refuse("v1_binding")

    v2_dir = root / v2.BASE
    scope._directory(v2_dir, private=True)
    if sorted(item.name for item in v2_dir.iterdir()) != [v2.GRANT]:
        _refuse("v2_inventory")
    v2_raw = scope._read(v2_dir / v2.GRANT, maximum=65536)
    if _digest(v2_raw) != V2_GRANT_SHA256:
        _refuse("v2_pin")
    prior = scope._json(v2_raw)
    if (
        prior.get("schema") != "zelo-media-native-reader-extra-attempt-v2"
        or prior.get("runtime_root") != str(root)
        or prior.get("attempts_used_before") != 3
        or prior.get("attempt_limit_after") != 4
        or prior.get("additional_attempts") != 1
        or prior.get("attempt_reset") is not False
        or prior.get("proof_rewritten") is not False
        or prior.get("proof_deleted") is not False
        or prior.get("runtime_authority") is not False
        or prior.get("source_head") != FOURTH_SOURCE_HEAD
        or prior.get("source_tree") != FOURTH_SOURCE_TREE
        or prior.get("existing_grant_sha256") != v2.V1_GRANT_SHA256
    ):
        _refuse("v2_binding")

    return root, {
        "schema": "zelo-media-native-reader-extra-attempt-v3",
        "status": "one-additional-attempt-granted",
        "runtime_root": str(root),
        "scope_sha256": SCOPE_SHA256,
        "reconciliation_path": str(pending_path.relative_to(root)),
        "reconciliation_sha256": RECONCILIATION_SHA256,
        "proof_history": entries,
        "existing_grant_v1_path": f"{v2.V1_BASE}/{v1.GRANT}",
        "existing_grant_v1_sha256": v2.V1_GRANT_SHA256,
        "existing_grant_v2_path": f"{v2.BASE}/{v2.GRANT}",
        "existing_grant_v2_sha256": V2_GRANT_SHA256,
        "attempts_used_before": 4,
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


def validate(
    runtime_root: Path,
    *,
    held_admission: bool = False,
) -> dict:
    root, expected = _history(runtime_root)
    if not held_admission:
        scope._check_unheld_admission(root)
    base = root / BASE
    if not base.exists():
        _refuse("grant_missing")
    scope._directory(base, private=True)
    if sorted(item.name for item in base.iterdir()) != [GRANT, SEAL]:
        _refuse("grant_inventory")
    raw = scope._read(base / GRANT, maximum=65536)
    grant = scope._json(raw)
    seal = scope._json(scope._read(base / SEAL, maximum=4096))
    if seal != {
        "schema": "zelo-media-native-reader-extra-attempt-v3-seal-v1",
        "grant_sha256": _digest(raw),
        "source_head": grant.get("source_head"),
        "source_tree": grant.get("source_tree"),
    }:
        _refuse("seal_binding")
    expected["source_head"], expected["source_tree"] = _source_identity()
    if set(grant) != set(expected) | {"created_utc"}:
        _refuse("grant_shape")
    if any(type(grant[k]) is not type(v) or grant[k] != v for k, v in expected.items()):
        _refuse("grant_binding")
    stamp = grant["created_utc"]
    if type(stamp) is not str or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp) is None:
        _refuse("grant_time")
    try:
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _refuse("grant_time")
    return grant


def grant_once(
    runtime_root: Path, observe: Callable[[Path], None], *, expected_source: tuple[str, str]
) -> dict:
    if (
        type(expected_source) is not tuple
        or len(expected_source) != 2
        or any(
            type(s) is not str or re.fullmatch(r"[0-9a-f]{40}", s) is None for s in expected_source
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
        v2._require_empty_marker(admission)
        observe(root)
        _history(root)
        v2._require_empty_marker(admission)
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
        seal = {
            "schema": "zelo-media-native-reader-extra-attempt-v3-seal-v1",
            "grant_sha256": _digest(raw),
            "source_head": source_head,
            "source_tree": source_tree,
        }
        seal_raw = (json.dumps(seal, sort_keys=True, allow_nan=False) + "\n").encode()
        seal_created = False
        try:
            seal_fd = os.open(
                base / SEAL,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
            seal_created = True
            try:
                view = memoryview(seal_raw)
                while view:
                    count = os.write(seal_fd, view)
                    if count <= 0:
                        _refuse("seal_write")
                    view = view[count:]
                os.fsync(seal_fd)
            finally:
                os.close(seal_fd)
            directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            validate(root, held_admission=True)
        except BaseException:
            # A failed seal acknowledgement cannot become an accepted grant.
            # Retain the grant directory so the attempt cannot be retried as new.
            if seal_created:
                try:
                    os.unlink(base / SEAL)
                    directory = os.open(
                        base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                    )
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError:
                    pass  # Explicit run approval remains a separate gate.
            raise
        return record
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
