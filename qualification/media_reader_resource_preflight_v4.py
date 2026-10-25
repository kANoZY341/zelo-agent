"""Read-only live Docker check for a possible sixth native Reader admission.

The caller must hold the shared admission lock. This neither grants another
attempt nor checks a decision; it only refuses an occupied Docker namespace.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from qualification import media_reader_post_cleanup_ledger as post_cleanup
from qualification import media_reader_scope as scope
from scripts import cleanup_media_native_reader_residual_images as cleanup
from scripts import qualify_media_native_reader as reader

RETAINED_ROOT = post_cleanup.RETAINED_ROOT


class PreflightRefused(ValueError):
    """The prospective sixth admission is outside the one retained root."""


def observe_held(runtime_root: Path) -> None:
    """Inspect all labelled resources and old pinned images around readiness.

    This must run under the caller's admission lock before a grant and again
    immediately before allocation. A prior receipt is no substitute for this
    live Docker inventory. No image/container removal occurs here.
    """
    if not isinstance(runtime_root, Path) or runtime_root != RETAINED_ROOT:
        raise PreflightRefused("reader_native_v4_preflight_root")
    root = scope._canonical_root(runtime_root)
    with tempfile.TemporaryDirectory(prefix="native-reader-sixth-check-", dir=root) as raw:
        config = Path(raw)
        os.chmod(config, 0o700)
        cleanup._snapshot(config, ())
        reader._docker_readiness(root)
        cleanup._snapshot(config, ())
