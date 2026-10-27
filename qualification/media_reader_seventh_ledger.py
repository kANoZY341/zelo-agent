"""Read-only six-refusal evidence for a possible, separately authorized path.

This verifies retained files under the shared lock. It does not inspect Docker,
create a grant, clear admission, or permit a seventh native proof.
"""

from __future__ import annotations

from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_run_approval_v4 as prior_approval
from qualification import media_reader_scope as scope
from qualification import media_reader_sixth_ledger as historical
from scripts import cleanup_media_native_reader_sixth_image as cleanup

RETAINED_ROOT = historical.RETAINED_ROOT
COMPLETION_SHA256 = "6aebba764d8ff5c9dfa373feabd74be01165df08d886c93719171bf2805d078b"


class SeventhLedgerRefused(ValueError):
    """The six-refusal history, cleanup receipt, or empty marker changed."""


def _refuse(reason: str) -> None:
    raise SeventhLedgerRefused("reader_seventh_ledger_" + reason) from None


def _history() -> tuple[dict[str, str], ...]:
    return (
        *prior_approval._history(),
        {
            "attempt": cleanup.ATTEMPT,
            "policy_sha256": cleanup.PINS[f"media-native-reader/{cleanup.ATTEMPT}/policy.json"],
            "proof_sha256": cleanup.PINS[f"media-native-reader/{cleanup.ATTEMPT}/cases.json"],
            "source_head": cleanup.SOURCE_HEAD,
            "source_tree": cleanup.SOURCE_TREE,
        },
    )


def _empty_marker(root: Path) -> None:
    try:
        historical._empty_marker(root / "media-elf-metadata-admission/active.marker")
    except (historical.SixthLedgerRefused, OSError):
        _refuse("marker")


def _audit_held(root: Path) -> dict[str, object]:
    """Caller holds the admission lock for both pinned receipt reads."""
    _empty_marker(root)
    completion = cleanup._completion_held(root)
    if completion != COMPLETION_SHA256:
        _refuse("completion")
    if cleanup._completion_held(root) != completion:
        _refuse("completion_drift")
    _empty_marker(root)
    return {
        "status": "verified-six-refusals-and-exact-image-cleanup",
        "proof_history": _history(),
        "attempts_used": 6,
        "attempt_limit": 6,
        "cleanup_completion_sha256": completion,
        "marker_cleared": True,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _audit(root: Path) -> dict[str, object]:
    with fifth._locked(root):
        return _audit_held(root)


def audit(runtime_root: Path) -> dict[str, object]:
    """Inspect only the exact retained root; never creates proof authority."""
    if Path(runtime_root) != RETAINED_ROOT:
        _refuse("root")
    if scope._canonical_root(RETAINED_ROOT) != RETAINED_ROOT:
        _refuse("root")
    return _audit(RETAINED_ROOT)
