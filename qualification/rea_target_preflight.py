"""Offline, opt-in REA target review preview, never an execution admission.

A preflight binds an already-sealed offline target to the exact vendored
source and the single *fixture-proven* static tool identity. It is callable
only from an explicitly opted-in local operator utility; Core never imports it.
It provides no
approval, lease, Worker, MCP invocation, credential or Task admission.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rea_tool_inventory import (
    EXPECTED_CATALOG_SHA256,
    ReaCatalogRefusal,
    rea_tool_inventory,
)
from qualification.rea_source_vendor import (
    EXPECTED_COMMIT,
    EXPECTED_MANIFEST_SHA256,
    verify_vendored_rea_source,
)
from qualification.rea_target_admission import verify_target

SCHEMA = "zelo-rea-target-preflight-v1"
STATIC_TOOL = "analyze_javascript_application"
SnapshotId = Annotated[str, Field(min_length=39, max_length=39, pattern=r"^target-[0-9a-f]{32}$")]
Sha256 = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]


class ReaPreflightInput(BaseModel):
    """Explicit operator action; paths, tools and execution flags are not input."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    snapshot_id: SnapshotId
    receipt_sha256: Sha256


class ReaTargetPreflight(BaseModel):
    """Content-bound inert review artifact, NOT a revocable Core approval."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-rea-target-preflight-v1"] = SCHEMA
    snapshot_id: SnapshotId
    receipt_sha256: Sha256
    content_sha256: Sha256
    file_count: int = Field(ge=1, le=128)
    total_bytes: int = Field(ge=0, le=4 * 1024 * 1024)
    source_commit: Literal["6fee42689ae6e95a0182e0d4d0f55644e73e72be"] = EXPECTED_COMMIT
    source_manifest_sha256: Literal[
        "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
    ] = EXPECTED_MANIFEST_SHA256
    tool_catalog_sha256: Literal[
        "82fb67571758c177d1eecac72efac152772cccc753442a77ef9418edc69be6a0"
    ] = EXPECTED_CATALOG_SHA256
    tool_name: Literal["analyze_javascript_application"] = STATIC_TOOL
    tool_qualification: Literal["fixed_fixture_only_not_target_qualified"] = (
        "fixed_fixture_only_not_target_qualified"
    )
    manual_operator_opt_in: Literal[True] = True
    review_fingerprint: Sha256
    execution_enabled: Literal[False] = False
    core_approval: Literal[False] = False
    runtime_qualified: Literal[False] = False
    worker_registered: Literal[False] = False
    tool_grants: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_review_identity(self):
        if self.review_fingerprint != _fingerprint(
            self.model_dump(mode="json", exclude={"review_fingerprint"})
        ):
            raise ValueError("rea_target_preflight_fingerprint_invalid")
        return self


def _fingerprint(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def prepare_rea_target_preflight(
    request: ReaPreflightInput, *, operator_opt_in: bool = False
) -> ReaTargetPreflight:
    """Recheck bytes, source and catalog every time; never allocate authority.

    Verification is point-in-time. Later *separate* Core approval and a
    consumption-time recheck must both pass before any real target analysis.
    """

    if operator_opt_in is not True:
        raise PermissionError("rea_preflight_explicit_review_required")
    if type(request) is not ReaPreflightInput:
        raise TypeError("rea_preflight_input_invalid")
    request = ReaPreflightInput.model_validate(request.model_dump(mode="json"), strict=True)

    receipt = verify_target(request.snapshot_id, request.receipt_sha256)
    source = verify_vendored_rea_source()
    catalog = rea_tool_inventory()
    if (
        source.upstream_commit != EXPECTED_COMMIT
        or source.execution_enabled
        or catalog.execution_enabled
        or catalog.worker_registered
        or catalog.grants
        or [(item.name, item.qualification) for item in catalog.tools if item.name == STATIC_TOOL]
        != [(STATIC_TOOL, "manual_fixed_fixture_only")]
    ):
        raise ReaCatalogRefusal("rea_preflight_source_or_tool_not_qualified")

    values: dict[str, object] = {
        "schema_version": SCHEMA,
        "snapshot_id": receipt["snapshot_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "content_sha256": receipt["content_sha256"],
        "file_count": receipt["file_count"],
        "total_bytes": receipt["total_bytes"],
        "source_commit": EXPECTED_COMMIT,
        "source_manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "tool_catalog_sha256": EXPECTED_CATALOG_SHA256,
        "tool_name": STATIC_TOOL,
        "tool_qualification": "fixed_fixture_only_not_target_qualified",
        "manual_operator_opt_in": True,
        "execution_enabled": False,
        "core_approval": False,
        "runtime_qualified": False,
        "worker_registered": False,
        "tool_grants": (),
    }
    return ReaTargetPreflight(**values, review_fingerprint=_fingerprint(values))
