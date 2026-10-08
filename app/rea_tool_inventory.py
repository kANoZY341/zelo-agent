"""Pinned, operator-visible REA tool inventory; never a Worker or Tool grant.

Upstream MCP tool names are untrusted metadata. The only locally proved
operation is a manual, hardcoded two-file static fixture; it grants no Core
execution rights. All additional tools remain explicitly unqualified.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

CATALOG = Path(__file__).resolve().parents[1] / "integrations" / "rea" / "tool-catalog-v6.0.0.json"
EXPECTED_CATALOG_SHA256 = "82fb67571758c177d1eecac72efac152772cccc753442a77ef9418edc69be6a0"
EXPECTED_SOURCE_COMMIT = "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
CATALOG_MAX_BYTES = 32_768
_NAME = re.compile(r"[a-z][a-z0-9_]{0,95}\Z", re.ASCII)
_FIXED_FIXTURE_TOOL = "analyze_javascript_application"


class ReaCatalogRefusal(ValueError):
    """Catalog identity or bounded format cannot be independently verified."""


class ReaToolCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    qualification: Literal["manual_fixed_fixture_only", "unqualified"]


class ReaToolInventorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["zelo-rea-operator-tool-inventory-v1"] = (
        "zelo-rea-operator-tool-inventory-v1"
    )
    source_commit: Literal["6fee42689ae6e95a0182e0d4d0f55644e73e72be"] = (
        "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
    )
    tool_count: Literal[139] = 139
    execution_enabled: Literal[False] = False
    worker_registered: Literal[False] = False
    grants: tuple[str, ...] = ()
    tools: tuple[ReaToolCandidate, ...]


def _pinned_catalog_bytes(path: Path) -> bytes:
    """Bound file size before read; do not follow a swapped catalog symlink."""

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > CATALOG_MAX_BYTES:
                raise ReaCatalogRefusal("rea_catalog_file_invalid")
            data = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if len(data) != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ReaCatalogRefusal("rea_catalog_file_changed")
            return data
    except OSError:
        raise ReaCatalogRefusal("rea_catalog_unavailable") from None


def rea_tool_inventory(*, catalog_path: Path = CATALOG) -> ReaToolInventorySnapshot:
    """Report complete upstream names, never authorize any of the 139 tools."""

    raw = _pinned_catalog_bytes(catalog_path)
    if hashlib.sha256(raw).hexdigest() != EXPECTED_CATALOG_SHA256:
        raise ReaCatalogRefusal("rea_catalog_identity_changed")
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise ReaCatalogRefusal("rea_catalog_invalid") from None
    if (
        type(payload) is not dict
        or set(payload)
        != {
            "schema",
            "upstream_source_commit",
            "upstream_source_tag",
            "mcp_server",
            "mcp_protocol",
            "source_build",
            "observed_no_network",
            "tool_count",
            "tool_names",
            "execution_enabled",
            "zelo_tool_grants",
        }
        or payload["schema"] != "zelo-rea-mcp-tool-inventory-v1"
        or payload["upstream_source_commit"] != EXPECTED_SOURCE_COMMIT
        or payload["upstream_source_tag"] != "rea-agents-6.0.0"
        or payload["mcp_server"] != {"name": "rea", "version": "6.0.0"}
        or payload["mcp_protocol"] != "2025-11-25"
        or payload["source_build"] is not True
        or payload["observed_no_network"] is not True
        or payload["execution_enabled"] is not False
        or payload["zelo_tool_grants"] != []
        or type(payload["tool_count"]) is not int
        or payload["tool_count"] != 139
    ):
        raise ReaCatalogRefusal("rea_catalog_contract_invalid")
    names = payload["tool_names"]
    if (
        type(names) is not list
        or len(names) != 139
        or any(type(name) is not str or _NAME.fullmatch(name) is None for name in names)
        or names != sorted(set(names))
        or _FIXED_FIXTURE_TOOL not in names
    ):
        raise ReaCatalogRefusal("rea_catalog_names_invalid")
    return ReaToolInventorySnapshot(
        tools=tuple(
            ReaToolCandidate(
                name=name,
                qualification=(
                    "manual_fixed_fixture_only" if name == _FIXED_FIXTURE_TOOL else "unqualified"
                ),
            )
            for name in names
        )
    )
