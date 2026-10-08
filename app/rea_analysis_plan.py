"""Read-only Core REA analysis-plan projection. A plan never authorizes execution.

Unlike a capability, lease or Task request, this immutable preview has no
consumer and cannot be transformed into authority. A revoked selection refuses
at read time, and real target bytes are never inspected by Core here.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_factory import default_agent_registry
from app.auth import OperatorPrincipal
from app.rea_candidate import rea_candidate_snapshot
from app.rea_operator_targets import ReaOperatorTargetStore
from app.rea_tool_inventory import (
    EXPECTED_CATALOG_SHA256,
    ReaCatalogRefusal,
    rea_tool_inventory,
)
from app.worker_authority import authority_policy_version

SCHEMA = "zelo-rea-analysis-plan-preview-v1"
SOURCE_MANIFEST_SHA256 = "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
_REFUSALS = (
    "target_not_verified_by_core",
    "rea_agent_not_active",
    "rea_worker_not_qualified",
    "rea_tool_ungranted",
    "execution_not_approved",
)


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    ).hexdigest()


class ReaAnalysisPlanPreview(BaseModel):
    """Deterministic refusal-bearing operator view, never an approval artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-rea-analysis-plan-preview-v1"] = SCHEMA
    snapshot_id: str = Field(pattern=r"^target-[0-9a-f]{32}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=1, le=128)
    total_bytes: int = Field(ge=0, le=4 * 1024 * 1024)
    review_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_commit: Literal["6fee42689ae6e95a0182e0d4d0f55644e73e72be"]
    source_manifest_sha256: Literal[
        "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
    ]
    catalog_sha256: Literal["82fb67571758c177d1eecac72efac152772cccc753442a77ef9418edc69be6a0"]
    tool_name: Literal["analyze_javascript_application"]
    tool_qualification: Literal["fixed_fixture_only_not_target_qualified"]
    target_validation: Literal["operator_asserted_unverified"]
    agent_id: Literal["reverse_engineering"]
    agent_lifecycle: Literal["draft"]
    candidate_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_agent_registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_authority_policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_authority: Literal[False] = False
    task_created: Literal[False] = False
    agent_activated: Literal[False] = False
    worker_registered: Literal[False] = False
    tool_grants: tuple[()] = ()
    may_execute: Literal[False] = False
    refusal_reasons: tuple[
        Literal[
            "target_not_verified_by_core",
            "rea_agent_not_active",
            "rea_worker_not_qualified",
            "rea_tool_ungranted",
            "execution_not_approved",
        ],
        ...,
    ] = (
        "target_not_verified_by_core",
        "rea_agent_not_active",
        "rea_worker_not_qualified",
        "rea_tool_ungranted",
        "execution_not_approved",
    )
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_inert_identity(self):
        if tuple(self.refusal_reasons) != _REFUSALS:
            raise ValueError("rea_analysis_plan_refusals_missing")
        if self.plan_fingerprint != _fingerprint(
            self.model_dump(mode="json", exclude={"plan_fingerprint"})
        ):
            raise ValueError("rea_analysis_plan_fingerprint_invalid")
        return self


def prepare_rea_analysis_plan(
    store: ReaOperatorTargetStore,
    snapshot_id: str,
    *,
    principal: OperatorPrincipal,
) -> ReaAnalysisPlanPreview:
    """Project current inert registry/source and chosen target, never reserve rights."""

    if type(principal) is not OperatorPrincipal or principal.subject != "ahmad":
        raise PermissionError("rea_operator_required")
    selected = store.get(snapshot_id, principal=principal)
    if selected is None:
        raise LookupError("rea_operator_target_not_selected")
    if not selected.currently_selected:
        raise ValueError("rea_operator_target_revoked")
    if (
        selected.origin != "operator_asserted_unverified"
        or selected.target_verified_by_core
        or selected.core_approval
        or selected.execution_authority
        or selected.worker_authority
        or selected.tool_grants
    ):
        raise ValueError("rea_operator_target_not_inert")

    candidate = rea_candidate_snapshot()
    live_agents = default_agent_registry()
    catalog = rea_tool_inventory()
    if (
        candidate.execution_enabled
        or candidate.tool_grants
        or candidate.agent.lifecycle != "draft"
        or candidate.agent.allowed_tools
        or candidate.agent.allowed_task_kinds
        or live_agents.get_agent("reverse_engineering") is not None
        or catalog.execution_enabled
        or catalog.worker_registered
        or catalog.grants
        or len([x for x in catalog.tools if x.name == "analyze_javascript_application"]) != 1
        or next(
            x for x in catalog.tools if x.name == "analyze_javascript_application"
        ).qualification
        != "manual_fixed_fixture_only"
    ):
        raise ReaCatalogRefusal("rea_analysis_plan_authority_changed")

    values: dict[str, object] = {
        "schema_version": SCHEMA,
        "snapshot_id": selected.snapshot_id,
        "receipt_sha256": selected.receipt_sha256,
        "content_sha256": selected.content_sha256,
        "file_count": selected.file_count,
        "total_bytes": selected.total_bytes,
        "review_fingerprint": selected.review_fingerprint,
        "source_commit": candidate.source_commit,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "catalog_sha256": EXPECTED_CATALOG_SHA256,
        "tool_name": "analyze_javascript_application",
        "tool_qualification": "fixed_fixture_only_not_target_qualified",
        "target_validation": "operator_asserted_unverified",
        "agent_id": candidate.agent.agent_id,
        "agent_lifecycle": candidate.agent.lifecycle,
        "candidate_spec_sha256": _fingerprint(candidate.agent.model_dump(mode="json")),
        "active_agent_registry_version": live_agents.version,
        "worker_authority_policy_version": authority_policy_version(),
        "approval_authority": False,
        "task_created": False,
        "agent_activated": False,
        "worker_registered": False,
        "tool_grants": (),
        "may_execute": False,
        "refusal_reasons": _REFUSALS,
    }
    return ReaAnalysisPlanPreview(**values, plan_fingerprint=_fingerprint(values))
