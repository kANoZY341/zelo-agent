"""Fail-closed v0.15 release interlock at the Task AND GitHub side-effect gates.

This is not a release audit or operator-approval generator. The default
configuration refuses every v0.15 patch/pre-release tag until an independently
audited exact main SHA is deliberately pinned by the host operator.
"""

import re

from app.config import Settings

_V015_TAG = re.compile(r"v0\.15\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?")


def require_v015_release_scope(tag: str, main_sha: str, settings: Settings) -> None:
    """Reject before release Task creation or ANY GitHub GET/POST.

    Even with an exact host-pinned SHA, the existing separate Task approval
    and exact-head GitHub checks remain mandatory. Never infer that the
    configuration value by itself proves issue #166 or authorizes tagging.
    """
    if not isinstance(tag, str) or _V015_TAG.fullmatch(tag) is None:
        return
    approved_sha = settings.v015_release_approved_main_sha
    if (
        not isinstance(approved_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", approved_sha) is None
        or not isinstance(main_sha, str)
        or approved_sha != main_sha
    ):
        raise ValueError("v015_release_scope_approval_required")
