"""Read-only seven-refusal inventory for future, separately reviewed work.

This module reads fixed retained files under the admission lock. It does not
inspect Docker, issue a grant, clear a marker, or authorize an eighth proof.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from qualification import media_reader_seventh_ledger as previous
from qualification import media_reader_sixth_ledger as historical
from scripts import cleanup_media_native_reader_sixth_image as cleanup

RETAINED_ROOT = previous.RETAINED_ROOT
ATTEMPT = "native-reader-proof-v2sr2b2m"
SOURCE_HEAD = "e9893060f04208f8b788534fc1b92c41220f0a47"
SOURCE_TREE = "192fc40fb842c4ff2f725f36247e8d5e325838a3"

PINS: dict[str, tuple[str, int, int]] = {
    **historical.FILE_PINS,
    f"{historical.cleanup.BASE}/{historical.CLEANUP_NAME}/pending.json": (
        "3877a12b1157a87c4be4e6efb9d2754a1389d8095f198048528f4c89710e2f19",
        0o600,
        65536,
    ),
    **{
        relative: (digest, 0o600, 2097152 if relative.endswith("/cases.json") else 65536)
        for relative, digest in cleanup.PINS.items()
    },
    f"{cleanup.BASE}/cleanup-s9y8x100/pending.json": (
        "b4b6f3f2799b5b374156448d635de63c72af2301721146a002f0e9bcaae8fbbe",
        0o600,
        65536,
    ),
    f"{cleanup.BASE}/cleanup-s9y8x100/completed.json": (previous.COMPLETION_SHA256, 0o600, 65536),
    "media-native-reader-run-approval-v5/receipt.json": (
        "7a9f390a10b85dff16024d9b6241a82cd825502105911b9fbb31ca20d06eca1a",
        0o600,
        65536,
    ),
    "media-native-reader-run-approval-v5/recheck.json": (
        "c99ef52f6b32f473d5c23f87c95a5af64ac295a650c976ef969fbfe4d8888bdd",
        0o600,
        65536,
    ),
    "media-native-reader-run-approval-v5/attempt-recheck.json": (
        "66b17efb7559d5a3884858ef6f00c43707a1b7963b311bd424316ccc78adbd3f",
        0o600,
        65536,
    ),
    "media-native-reader-extra-attempt-v5/grant.json": (
        "c20f32bdaf6a6e7332ba8adbea9bddd59da6ccd9bd6ae6402ba553f171523361",
        0o600,
        65536,
    ),
    "media-native-reader-extra-attempt-v5/seal.json": (
        "747806a12ffa637ae46714ab8f896f444e79c7daa6067730f7a7631795278772",
        0o600,
        65536,
    ),
    f"media-native-reader/{ATTEMPT}/cases.json": (
        "4102226af8a74b47ec47db1ab1ad08b877f766d3641a5631c448d0ded26cf5c4",
        0o600,
        2097152,
    ),
    f"media-native-reader/{ATTEMPT}/policy.json": (
        "c2a21f5cea998012fef01e47653216c8b9e45b17c4d4668dd09e0afa38ddefd6",
        0o600,
        2097152,
    ),
}


class EighthLedgerRefused(ValueError):
    """The retained seven-refusal record is missing or changed."""


def _refuse(reason: str) -> None:
    raise EighthLedgerRefused("reader_eighth_ledger_" + reason) from None


def _directories(root: Path) -> None:
    """Check the fixed evidence tree, including absence of an eighth proof."""
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
    for relative, expected in children.items():
        fifth._children(root / relative, expected)
    approved_bases = {Path(relative).parts[0] for relative in PINS}
    for entry in root.iterdir():
        if (
            entry.name.startswith(
                ("media-native-reader-extra-attempt", "media-native-reader-run-approval")
            )
            and entry.name not in approved_bases
        ):
            _refuse("eighth_artifact")


def _audit_held(root: Path) -> dict[str, object]:
    historical._marker(root / "media-elf-metadata-admission/active.marker", b"")
    _directories(root)
    values = {relative: fifth._read_pinned(root / relative, *pin) for relative, pin in PINS.items()}
    case = scope._json(values[f"media-native-reader/{ATTEMPT}/cases.json"])
    policy = scope._json(values[f"media-native-reader/{ATTEMPT}/policy.json"])
    grant_raw = values["media-native-reader-extra-attempt-v5/grant.json"]
    grant = scope._json(grant_raw)
    seal = scope._json(values["media-native-reader-extra-attempt-v5/seal.json"])
    if case != {
        "status": "refused",
        "stage": "matrix",
        "matrix_failure": {
            "kind": "validation",
            "code": "reader_runtime_probe",
            "cause_code": "reader_container_configuration",
        },
        "support_release_confirmed": False,
        "runtime_authority": False,
    }:
        _refuse("seventh_refusal")
    if (
        type(policy.get("attempt_limit")) is not int
        or policy["attempt_limit"] != 7
        or policy.get("runtime_authority") is not False
        or grant.get("runtime_root") != str(root)
        or grant.get("source_head") != SOURCE_HEAD
        or grant.get("source_tree") != SOURCE_TREE
        or grant.get("approval_comment_id") != 6442334016
        or type(grant.get("attempts_used_before")) is not int
        or grant["attempts_used_before"] != 6
        or type(grant.get("attempt_limit_after")) is not int
        or grant["attempt_limit_after"] != 7
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
        _refuse("seventh_binding")
    historical._marker(root / "media-elf-metadata-admission/active.marker", b"")
    return {
        "status": "verified-seven-terminal-refusals",
        "proof_history": (
            *previous._history(),
            {
                "attempt": ATTEMPT,
                "policy_sha256": PINS[f"media-native-reader/{ATTEMPT}/policy.json"][0],
                "proof_sha256": PINS[f"media-native-reader/{ATTEMPT}/cases.json"][0],
                "source_head": SOURCE_HEAD,
                "source_tree": SOURCE_TREE,
            },
        ),
        "attempts_used": 7,
        "attempt_limit": 7,
        "marker_cleared": True,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


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
