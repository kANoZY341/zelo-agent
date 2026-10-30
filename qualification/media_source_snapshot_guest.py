"""Parameterize only the fixed input identity of the reviewed origin guest."""

import re

from qualification import media_origin as origin
from qualification import media_origin_guest as reviewed

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def bootstrap(size: int, sha256: str) -> str:
    if (
        type(size) is not int
        or not 0 < size <= origin.RELEASE_BYTES
        or type(sha256) is not str
        or not _SHA.fullmatch(sha256)
    ):
        raise ValueError("media_snapshot_guest_pin")
    old = "INPUTS = " + repr(reviewed.INPUTS) + "\n"
    if reviewed.GUEST_BOOTSTRAP.count(old) != 1:
        raise ValueError("media_snapshot_guest_source")
    record = (
        {
            "source": "/opt/zelo-origin.InRelease",
            "suite": "noble-updates",
            "bytes": size,
            "sha256": sha256,
            "mode": 0o644,
        },
    )
    return reviewed.GUEST_BOOTSTRAP.replace(old, "INPUTS = " + repr(record) + "\n", 1)
