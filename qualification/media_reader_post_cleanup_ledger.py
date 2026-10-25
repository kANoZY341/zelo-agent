"""Read-only, receipt-bound five-refusal ledger after exact residual cleanup.

No Docker operation, grant, admission change, or sixth proof is performed here.
The shared admission lock covers both the historical ledger and the one-use
cleanup receipt chain; a later run path must separately check live Docker.
"""

from __future__ import annotations

from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from qualification import media_reader_sixth_ledger as historical
from scripts import cleanup_media_native_reader_residual_images as cleanup

RETAINED_ROOT = historical.RETAINED_ROOT
COMPLETION_SHA256 = "8795cf71ffa8b117e5c5902ba7f5d6470c0b6bfd2c696a7b59015efa2b8b382c"


class PostCleanupRefused(ValueError):
    """The retained history or exact residual cleanup is absent or changed."""


def audit(runtime_root: Path) -> dict[str, object]:
    """Verify both receipt chains under one lock; report no Docker authority."""
    if Path(runtime_root) != RETAINED_ROOT:
        raise PostCleanupRefused("reader_post_cleanup_root")
    root = scope._canonical_root(RETAINED_ROOT)
    with fifth._locked(root):
        history = historical._audit_held(root, historical.FILE_PINS)
        completion = cleanup._completion_held(root)
        if completion != COMPLETION_SHA256:
            raise PostCleanupRefused("reader_post_cleanup_completion")
        # The second historical audit catches drift across the receipt read.
        if historical._audit_held(root, historical.FILE_PINS) != history:
            raise PostCleanupRefused("reader_post_cleanup_ledger_drift")
        return {**history, "residual_cleanup_completion_sha256": completion}
