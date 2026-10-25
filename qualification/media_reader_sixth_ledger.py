"""Read-only five-refusal ledger for a future, separately authorized Reader path.

This module never touches Docker, writes a grant, clears admission, or permits
another proof. Public inspection is fixed to the one reconciled retained root.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from qualification import media_reader_extra_attempt as v1
from qualification import media_reader_extra_attempt_v2 as v2
from qualification import media_reader_extra_attempt_v3 as v3
from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_reconcile as first
from qualification import media_reader_scope as scope
from scripts import cleanup_media_native_reader_fifth_image as cleanup
from scripts import reconcile_media_native_reader_fifth as reconciliation

RETAINED_ROOT = fifth.RETAINED_ROOT
CLEANUP_NAME = "cleanup-xjejyg4y"
RECONCILIATION_NAME = "reconcile-5zbl2qgk"
CLEANUP_SHA256 = "943d2625b5efe522739554e5000860e66e3f1a56f788b83504407604e16b5434"
RECONCILIATION_SHA256 = "bce6a0881a7c1086dd74d87e5dabbcc7a5ac4e33b4a0c4952c022f08185fa1fd"
CLEANUP_FILE = f"{cleanup.BASE}/{CLEANUP_NAME}/completed.json"
RECONCILIATION_FILE = f"{reconciliation.RECON_BASE}/{RECONCILIATION_NAME}/pending.json"
FILE_PINS = {
    relative: pin
    for relative, pin in fifth.FILE_PINS.items()
    if relative != "media-elf-metadata-admission/active.marker"
}
FILE_PINS[CLEANUP_FILE] = (CLEANUP_SHA256, 0o600, 65536)
FILE_PINS[RECONCILIATION_FILE] = (RECONCILIATION_SHA256, 0o600, 65536)


class SixthLedgerRefused(ValueError):
    """The exact five-refusal history or reconciled fence has changed."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SixthLedgerRefused("reader_sixth_ledger_" + reason)


def _empty_marker(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        _require(
            stat.S_ISREG(before.st_mode)
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_uid == os.geteuid()
            and before.st_gid == os.getegid()
            and before.st_nlink == 1
            and before.st_size == 0
            and os.read(fd, 1) == b"",
            "marker",
        )
        after = os.fstat(fd)
        _require(
            identity
            == (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ),
            "marker_drift",
        )
    finally:
        os.close(fd)


def _directories(root: Path) -> None:
    proof_base = root / "media-native-reader"
    fifth._children(proof_base, {first.ATTEMPT_NAME, v1.SECOND, v2.THIRD, v3.FOURTH, fifth.FIFTH})
    for name in (first.ATTEMPT_NAME, v1.SECOND, v2.THIRD, v3.FOURTH):
        names = {"cases.json", "policy.json"}
        if name == first.ATTEMPT_NAME:
            names.add("docker-config")
        fifth._children(proof_base / name, names)
    fifth._children(proof_base / first.ATTEMPT_NAME / "docker-config", set())
    fifth_dir = proof_base / fifth.FIFTH
    fifth._children(fifth_dir, {"build", "cases.json", "docker-config", "image-id", "policy.json"})
    fifth._children(fifth_dir / "docker-config", set())
    fifth._children(
        fifth_dir / "build", {"Dockerfile"} | {f"{n:02}.binary.reference" for n in range(8)}
    )
    for base, names in (
        ("media-native-reader-extra-attempt", {"grant.json"}),
        ("media-native-reader-extra-attempt-v2", {"grant.json"}),
        ("media-native-reader-extra-attempt-v3", {"grant.json", "seal.json"}),
        (
            "media-native-reader-run-approval-v3",
            {"receipt.json", "recheck.json", "attempt-recheck.json"},
        ),
        ("media-native-reader-reconciliation", {"reconcile-1fudorw3"}),
        (cleanup.BASE, {CLEANUP_NAME}),
        (reconciliation.RECON_BASE, {RECONCILIATION_NAME}),
    ):
        fifth._children(root / base, names)
    fifth._children(
        root / "media-native-reader-reconciliation/reconcile-1fudorw3", {"pending.json"}
    )
    fifth._children(root / cleanup.BASE / CLEANUP_NAME, {"pending.json", "completed.json"})
    fifth._children(root / reconciliation.RECON_BASE / RECONCILIATION_NAME, {"pending.json"})


def _audit_held(root: Path, pins: dict[str, tuple[str, int, int]]) -> dict[str, object]:
    """Caller holds the shared admission lock for the whole inspection."""
    _directories(root)
    _empty_marker(root / "media-elf-metadata-admission/active.marker")
    values = {relative: fifth._read_pinned(root / relative, *pin) for relative, pin in pins.items()}
    fifth_policy = scope._json(values[f"media-native-reader/{fifth.FIFTH}/policy.json"])
    fifth_proof = scope._json(values[f"media-native-reader/{fifth.FIFTH}/cases.json"])
    _require(
        fifth_policy.get("attempt_limit") == 5
        and fifth_policy.get("runtime_authority") is False
        and fifth_proof
        == {
            "status": "refused",
            "stage": "build",
            "support_release_confirmed": False,
            "runtime_authority": False,
            "build_failure": {"kind": "validation", "code": "reader_container_image_configuration"},
        },
        "fifth_refusal",
    )
    grant = scope._json(values["media-native-reader-extra-attempt-v3/grant.json"])
    _require(
        grant.get("runtime_root") == str(root)
        and grant.get("source_head") == fifth.SOURCE_HEAD
        and grant.get("source_tree") == fifth.SOURCE_TREE
        and grant.get("attempt_limit_after") == 5
        and grant.get("additional_attempts") == 1
        and grant.get("runtime_authority") is False,
        "fifth_grant",
    )
    completion_sha = cleanup.completed_cleanup(root, fifth.FILE_PINS, cleanup.IMAGE_ID)
    _require(completion_sha == pins[CLEANUP_FILE][0], "cleanup_completion")
    receipt = scope._json(values[RECONCILIATION_FILE])
    expected = {
        "schema": "zelo-media-native-reader-fifth-reconciliation-v1",
        "status": "pending-marker-clear",
        "runtime_root": str(root),
        "failed_attempt": fifth.FIFTH,
        "failed_lease": fifth.LEASE,
        "failed_source_head": fifth.SOURCE_HEAD,
        "failed_source_tree": fifth.SOURCE_TREE,
        "proof_sha256": pins[f"media-native-reader/{fifth.FIFTH}/cases.json"][0],
        "policy_sha256": pins[f"media-native-reader/{fifth.FIFTH}/policy.json"][0],
        "marker_sha256": fifth.MARKER_SHA256,
        "cleanup_completion_sha256": completion_sha,
        "grant_sha256": pins["media-native-reader-extra-attempt-v3/grant.json"][0],
        "image_id_sha256": pins[f"media-native-reader/{fifth.FIFTH}/image-id"][0],
        "attempts_used": 5,
        "attempt_limit": 5,
        "attempt_reset": False,
        "proof_rewritten": False,
        "proof_deleted": False,
        "original_image_id_present": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    _require(
        all(
            type(receipt.get(key)) is type(value) and receipt[key] == value
            for key, value in expected.items()
        )
        and all(
            type(receipt.get(key)) is int and receipt[key] == 0
            for key in (
                "docker_container_count_before",
                "docker_container_count_after",
                "docker_image_count_before",
                "docker_image_count_after",
            )
        ),
        "reconciliation_binding",
    )
    history = tuple(
        {
            "attempt": name,
            "policy_sha256": pins[f"media-native-reader/{name}/policy.json"][0],
            "proof_sha256": pins[f"media-native-reader/{name}/cases.json"][0],
            "source_head": head,
            "source_tree": tree,
        }
        for name, head, tree in (
            (first.ATTEMPT_NAME, first.REFUSAL_HEAD, first.REFUSAL_TREE),
            (v1.SECOND, v2.SECOND_SOURCE_HEAD, v2.SECOND_SOURCE_TREE),
            (v2.THIRD, v2.THIRD_SOURCE_HEAD, v2.THIRD_SOURCE_TREE),
            (v3.FOURTH, v3.FOURTH_SOURCE_HEAD, v3.FOURTH_SOURCE_TREE),
            (fifth.FIFTH, fifth.SOURCE_HEAD, fifth.SOURCE_TREE),
        )
    )
    return {
        "status": "verified-five-refusals-and-reconciled-fence",
        "proof_history": history,
        "attempts_used": 5,
        "attempt_limit": 5,
        "marker_cleared": True,
        "cleanup_completion_sha256": completion_sha,
        "reconciliation_sha256": pins[RECONCILIATION_FILE][0],
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _audit(root: Path, pins: dict[str, tuple[str, int, int]]) -> dict[str, object]:
    root = scope._canonical_root(root)
    with fifth._locked(root):
        return _audit_held(root, pins)


def audit(runtime_root: Path) -> dict[str, object]:
    """Inspect only the retained root; no Docker or write operation."""
    _require(Path(runtime_root) == RETAINED_ROOT, "root")
    return _audit(RETAINED_ROOT, FILE_PINS)
