"""Read-only eight-refusal inventory for future, separately reviewed work.

This module reads fixed retained files under the admission lock. It does not
inspect Docker, issue a grant, clear a marker, or authorize a ninth proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from qualification import media_reader_eighth_ledger as previous
from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from qualification import media_reader_sixth_ledger as historical
from qualification.admission import Lease

RETAINED_ROOT = previous.RETAINED_ROOT
ATTEMPT = "native-reader-proof-plhsvu3q"
SOURCE_HEAD = "90afa73d7383ddba39553e7cfd40233c91375e77"
SOURCE_TREE = "206b4a93f3361fe2d30a66187fefb78af90e071d"

PINS: dict[str, tuple[str, int, int]] = {
    **previous.PINS,
    "media-native-reader-run-approval-v6/receipt.json": (
        "1466f129056902696dd132f0f6a39c3b2faeedf1e3ef29352930e275bd631127",
        0o600,
        65536,
    ),
    "media-native-reader-run-approval-v6/recheck.json": (
        "2208d99d43a82771f068890dd525b671b7d4e9a936a75210fe7a71e37b769a67",
        0o600,
        65536,
    ),
    "media-native-reader-run-approval-v6/attempt-recheck.json": (
        "0867e31a26ae7c78a6b2ae1052f4a685aa45ca7b6006fc6f618920e28429ba71",
        0o600,
        65536,
    ),
    "media-native-reader-extra-attempt-v6/grant.json": (
        "2ad0cf321ba71109db7bebcaa596eb93165ea7aa506a135ba23586bbe6555d85",
        0o600,
        65536,
    ),
    "media-native-reader-extra-attempt-v6/seal.json": (
        "ac0206b56654cecc28fc91a85ee0dc5371f78d02b74a0c16561c2f48dde04706",
        0o600,
        65536,
    ),
    f"media-native-reader/{ATTEMPT}/cases.json": (
        "9d11825abaa2b707e6d7d928155c958d39f5460a123694c5039a1f7fa69c0bc7",
        0o600,
        2097152,
    ),
    f"media-native-reader/{ATTEMPT}/policy.json": (
        "a28b4aa0eb252c09880de9d32971adf78d2aee86fbaa0ded1b029992d2f40423",
        0o600,
        2097152,
    ),
}


class NinthLedgerRefused(ValueError):
    """The retained eight-refusal record is missing or changed."""


def _refuse(reason: str) -> None:
    raise NinthLedgerRefused("reader_ninth_ledger_" + reason) from None


def _history() -> tuple[dict[str, str], ...]:
    return (
        *previous._history(),
        {
            "attempt": ATTEMPT,
            "policy_sha256": PINS[f"media-native-reader/{ATTEMPT}/policy.json"][0],
            "proof_sha256": PINS[f"media-native-reader/{ATTEMPT}/cases.json"][0],
            "source_head": SOURCE_HEAD,
            "source_tree": SOURCE_TREE,
        },
    )


_PROGRESS_STAGES = (
    frozenset(),
    frozenset({("media-native-reader-run-approval-v7", "receipt.json")}),
    frozenset(
        {
            ("media-native-reader-run-approval-v7", "receipt.json"),
            ("media-native-reader-run-approval-v7", "recheck.json"),
        }
    ),
    frozenset(
        {
            ("media-native-reader-run-approval-v7", "receipt.json"),
            ("media-native-reader-run-approval-v7", "recheck.json"),
            ("media-native-reader-extra-attempt-v7", "grant.json"),
            ("media-native-reader-extra-attempt-v7", "seal.json"),
        }
    ),
    frozenset(
        {
            ("media-native-reader-run-approval-v7", "receipt.json"),
            ("media-native-reader-run-approval-v7", "recheck.json"),
            ("media-native-reader-run-approval-v7", "attempt-recheck.json"),
            ("media-native-reader-extra-attempt-v7", "grant.json"),
            ("media-native-reader-extra-attempt-v7", "seal.json"),
        }
    ),
)


def _progress_pins(pins: dict[tuple[str, str], str]) -> dict[str, tuple[str, int, int]]:
    if type(pins) is not dict or frozenset(pins) not in _PROGRESS_STAGES:
        _refuse("progress_shape")
    result = {}
    for (base, name), digest in pins.items():
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            _refuse("progress_pin")
        result[f"{base}/{name}"] = (digest, 0o600, 65536)
    return result


def _directories(root: Path, progress: dict[str, tuple[str, int, int]]) -> None:
    """Check the fixed evidence tree, including absence of a ninth proof."""
    children: dict[Path, set[str]] = {}
    for relative in PINS:
        path = Path(relative)
        for parent in path.parents:
            if parent == Path("."):
                break
            child = path.relative_to(parent).parts[0]
            children.setdefault(parent, set()).add(child)
    # Earlier build records retain empty Docker config directories.
    first = historical.first.ATTEMPT_NAME
    fifth_name = fifth.FIFTH
    children[Path("media-native-reader") / first].add("docker-config")
    children[Path("media-native-reader") / fifth_name].add("docker-config")
    children[Path("media-native-reader") / first / "docker-config"] = set()
    children[Path("media-native-reader") / fifth_name / "docker-config"] = set()
    for relative in progress:
        base, name = relative.split("/", 1)
        children.setdefault(Path(base), set()).add(name)
    for relative, expected in children.items():
        fifth._children(root / relative, expected)
    retained_bases = {Path(relative).parts[0] for relative in (*PINS, *progress)}
    for entry in root.iterdir():
        if (
            entry.name.startswith(
                ("media-native-reader-extra-attempt", "media-native-reader-run-approval")
            )
            and entry.name not in retained_bases
        ):
            _refuse("ninth_artifact")


def _audit_evidence(
    root: Path, *, expected_marker: bytes, progress_pins: dict[tuple[str, str], str]
) -> dict[str, object]:
    progress = _progress_pins(progress_pins)
    historical._marker(root / "media-elf-metadata-admission/active.marker", expected_marker)
    _directories(root, progress)
    values = {
        relative: fifth._read_pinned(root / relative, *pin)
        for relative, pin in {**PINS, **progress}.items()
    }
    case = scope._json(values[f"media-native-reader/{ATTEMPT}/cases.json"])
    policy = scope._json(values[f"media-native-reader/{ATTEMPT}/policy.json"])
    grant_raw = values["media-native-reader-extra-attempt-v6/grant.json"]
    grant = scope._json(grant_raw)
    seal = scope._json(values["media-native-reader-extra-attempt-v6/seal.json"])
    if case != {
        "status": "refused",
        "stage": "matrix",
        "matrix_failure": {
            "kind": "validation",
            "code": "reader_runtime_probe",
            "cause_code": "reader_container_limits",
            "limit_differences": [{"field": "OomKillDisable", "observed_kind": "null"}],
        },
        "support_release_confirmed": False,
        "runtime_authority": False,
    }:
        _refuse("eighth_refusal")
    if (
        type(policy.get("attempt_limit")) is not int
        or policy["attempt_limit"] != 8
        or policy.get("runtime_authority") is not False
        or grant.get("runtime_root") != str(root)
        or grant.get("source_head") != SOURCE_HEAD
        or grant.get("source_tree") != SOURCE_TREE
        or grant.get("approval_comment_id") != 6455367560
        or type(grant.get("attempts_used_before")) is not int
        or grant["attempts_used_before"] != 7
        or type(grant.get("attempt_limit_after")) is not int
        or grant["attempt_limit_after"] != 8
        or type(grant.get("additional_attempts")) is not int
        or grant["additional_attempts"] != 1
        or grant.get("attempt_reset") is not False
        or grant.get("proof_rewritten") is not False
        or grant.get("proof_deleted") is not False
        or grant.get("runtime_authority") is not False
        or seal.get("grant_sha256") != hashlib.sha256(grant_raw).hexdigest()
        or seal.get("source_head") != SOURCE_HEAD
        or seal.get("source_tree") != SOURCE_TREE
    ):
        _refuse("eighth_binding")
    historical._marker(root / "media-elf-metadata-admission/active.marker", expected_marker)
    return {
        "status": "verified-eight-terminal-refusals",
        "proof_history": _history(),
        "attempts_used": 8,
        "attempt_limit": 8,
        "marker_cleared": expected_marker == b"",
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _audit_held(root: Path, *, expected_marker: bytes = b"") -> dict[str, object]:
    """Strict initial audit: any v7 receipt remains an unexpected artifact."""
    return _audit_evidence(root, expected_marker=expected_marker, progress_pins={})


def _audit_held_progress(root: Path, pins: dict[tuple[str, str], str]) -> dict[str, object]:
    """Caller holds admission; check exact pinned v7 records for one phase."""
    try:
        return _audit_evidence(root, expected_marker=b"", progress_pins=pins)
    except (
        OSError,
        historical.SixthLedgerRefused,
        fifth.FifthReconciliationRefused,
        scope.ScopeRefused,
    ):
        _refuse("progress_evidence")


def _audit_held_live(root: Path, lease: Lease, before: dict) -> dict[str, object]:
    return _audit_held_live_progress(root, lease, before, {})


def _audit_held_live_progress(
    root: Path, lease: Lease, before: dict, pins: dict[tuple[str, str], str]
) -> dict[str, object]:
    """Recheck the same pins under the exact occupied and retained lease."""
    owner = threading.get_ident()
    if (
        root != RETAINED_ROOT
        or type(lease) is not Lease
        or lease._fds is None
        or not lease._lifecycle.locked()
        or lease._retained_thread != owner
        or type(before) is not dict
        or before.get("marker_cleared") is not True
    ):
        _refuse("live_binding")
    try:
        if scope._canonical_root(root) != root:
            _refuse("live_binding")
        directory, lock_fd, marker_fd = lease._fds
        marker_path = root / "media-elf-metadata-admission/active.marker"
        for fd, path in (
            (directory, marker_path.parent),
            (lock_fd, marker_path.parent / "admission.lock"),
            (marker_fd, marker_path),
        ):
            held, named = os.fstat(fd), path.lstat()
            if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
                _refuse("live_binding")
        expected_marker = json.dumps(
            {"version": 1, "lease": lease.identity, "policy": lease.policy_digest}
        ).encode("ascii")
        live = _audit_evidence(root, expected_marker=expected_marker, progress_pins=pins)
        if live != {**before, "marker_cleared": False}:
            _refuse("live_drift")
        if _audit_evidence(root, expected_marker=expected_marker, progress_pins=pins) != live:
            _refuse("live_drift")
    except (
        OSError,
        historical.SixthLedgerRefused,
        fifth.FifthReconciliationRefused,
        scope.ScopeRefused,
    ):
        _refuse("live_evidence")
    return before


def audit(runtime_root: Path) -> dict[str, object]:
    """Verify only the fixed retained root; never allocate proof authority."""
    if Path(runtime_root) != RETAINED_ROOT:
        _refuse("root")
    try:
        if scope._canonical_root(RETAINED_ROOT) != RETAINED_ROOT:
            _refuse("root")
        with fifth._locked(RETAINED_ROOT):
            before = _audit_held(RETAINED_ROOT)
            if _audit_held(RETAINED_ROOT) != before:
                _refuse("drift")
            return before
    except (
        OSError,
        historical.SixthLedgerRefused,
        fifth.FifthReconciliationRefused,
        scope.ScopeRefused,
    ):
        _refuse("retained_evidence")
