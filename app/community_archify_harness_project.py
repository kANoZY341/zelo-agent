"""Project-bound Harness proposal diagram, with no activation authority.

Consumes the independently checked Core planning snapshot and pinned Archify
schema. The graph is a *design* of four draft roles, never a runtime agent map.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.community_archify import (
    ArchitectureComponent,
    ArchitectureConnection,
    ArchitectureDraft,
    CommunityArchifyRefusal,
    export_archify_json,
    export_mermaid,
)
from app.community_harness_project import HarnessProjectReviewPlan, PlanReason


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("ascii")
    ).hexdigest()


class HarnessProjectArchitectureView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-archify-harness-project-design-v1"] = (
        "zelo-archify-harness-project-design-v1"
    )
    source_kind: Literal["harness_project_design_only"] = "harness_project_design_only"
    project_id: str
    project_status: Literal["active", "paused", "completed", "archived"]
    template: Literal[
        "engineering_quality", "business_research", "youtube_content", "trading_research"
    ]
    blueprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_reason: PlanReason
    eligible_for_design_review_only: bool
    role_count: Literal[4] = 4
    archify_json: str = Field(min_length=64, max_length=131072)
    mermaid_text: str = Field(min_length=10, max_length=32768)
    managing_director_approval_required: Literal[True] = True
    agent_instances_created: Literal[False] = False
    real_team_activated: Literal[False] = False
    plan_is_execution_authority: Literal[False] = False
    renderer_executed: Literal[False] = False
    tool_grants: tuple[()] = ()
    worker_grants: tuple[()] = ()
    space_grants: tuple[()] = ()
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_evidence(self):
        if self.evidence_sha256 != _sha(self.model_dump(mode="json", exclude={"evidence_sha256"})):
            raise ValueError("harness_project_architecture_digest_invalid")
        if self.eligible_for_design_review_only != (
            self.review_reason == "eligible_for_design_review_only"
        ):
            raise ValueError("harness_project_architecture_reason_invalid")
        return self


def harness_project_architecture(plan: HarnessProjectReviewPlan) -> HarnessProjectArchitectureView:
    """Render only an intact, current-call review plan. Output is never a grant."""

    if type(plan) is not HarnessProjectReviewPlan:
        raise CommunityArchifyRefusal("harness_architecture_plan_invalid")
    # A model_construct() or in-memory mutation must not bypass the plan's
    # integrity, hierarchy, false-authority, and strict-field validators.
    try:
        plan = HarnessProjectReviewPlan.model_validate_json(plan.model_dump_json(), strict=True)
    except (ValueError, TypeError):
        raise CommunityArchifyRefusal("harness_architecture_plan_invalid") from None

    tiers = ("vp", "director", "manager", "specialist")
    ids = ("draftVP", "draftDirector", "draftManager", "draftSpecialist")
    components = [
        ArchitectureComponent("projectDesign", "Project (design preview only)", "security")
    ]
    edges = [ArchitectureConnection("projectDesign", ids[0])]
    for index, role in enumerate(plan.roles):
        if role.organizational_level != tiers[index]:
            raise CommunityArchifyRefusal("harness_architecture_role_invalid")
        # No untrusted project name, Agent instructions, host paths, or
        # provider metadata are included in Archify JSON or Mermaid labels.
        components.append(
            ArchitectureComponent(
                ids[index], f"Draft {tiers[index].upper()}: {role.agent_id}", "backend"
            )
        )
        if index:
            edges.append(ArchitectureConnection(ids[index - 1], ids[index]))

    diagram = ArchitectureDraft(
        schema="zelo-archify-architecture-export-v1",
        title="Harness candidate team (design only; not deployed)",
        components=tuple(components),
        connections=tuple(edges),
    )
    payload = {
        "schema_version": "zelo-archify-harness-project-design-v1",
        "source_kind": "harness_project_design_only",
        "project_id": plan.project_id,
        "project_status": plan.project_status,
        "template": plan.template,
        "blueprint_sha256": plan.blueprint_sha256,
        "registry_version": plan.registry_version,
        "review_plan_sha256": plan.plan_sha256,
        "review_reason": plan.reason,
        "eligible_for_design_review_only": plan.eligible_for_separate_design_review,
        "role_count": 4,
        "archify_json": export_archify_json(diagram).decode("ascii"),
        "mermaid_text": export_mermaid(diagram),
        "managing_director_approval_required": True,
        "agent_instances_created": False,
        "real_team_activated": False,
        "plan_is_execution_authority": False,
        "renderer_executed": False,
        "tool_grants": (),
        "worker_grants": (),
        "space_grants": (),
    }
    return HarnessProjectArchitectureView(**payload, evidence_sha256=_sha(payload))
