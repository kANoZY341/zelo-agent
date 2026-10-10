"""Project-scoped Harness team budget projection; no instance creation or grants.

An operator may request an exact, bounded resource proposal for one trusted
Harness blueprint and Core Project. The result is a read-only snapshot and
must NEVER be treated as authorization to activate Agents or Workers.
"""

import hashlib
import json
from contextlib import nullcontext
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_factory import AgentFactory
from app.community_harness import TeamTemplate, propose_team
from app.models import AgentInstance, Project
from app.policy import DomainError
from app.storage import SessionStore


class TeamProjectPlanningRefusal(ValueError):
    """Untrusted input did not bind exact approved-looking draft identity."""


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    ).hexdigest()


class TeamProposedBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    planned_instance_slots: int = Field(default=4, ge=1, le=4, strict=True)
    proposed_max_concurrent_workers: int = Field(default=1, ge=0, le=4, strict=True)
    proposed_model_tokens_per_task: int = Field(default=4096, ge=0, le=65536, strict=True)
    proposed_monthly_cost_cents: int = Field(default=0, ge=0, le=100000, strict=True)
    proposed_external_account_access: Literal[False] = False
    proposed_shell_access: Literal[False] = False
    proposed_automatic_publication: Literal[False] = False


class TeamProjectPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    project_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    expected_blueprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_budget: TeamProposedBudget


class TeamProjectRoleStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agent_id: str
    organizational_level: Literal["vp", "director", "manager", "specialist"]
    reports_to: str | None
    existing_instance_count: int = Field(ge=0)
    already_registered: bool
    eligible_for_separate_design_review: bool
    permissions_automatically_granted: Literal[False] = False


PlanReason = Literal[
    "eligible_for_design_review_only",
    "project_inactive",
    "agent_name_already_registered",
    "agent_instance_identity_reserved",
    "draft_instance_budget_insufficient",
]


class HarnessProjectReviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-harness-project-review-plan-v1"] = (
        "zelo-harness-project-review-plan-v1"
    )
    template: TeamTemplate
    project_id: str
    project_status: Literal["active", "paused", "completed", "archived"]
    project_updated_at: datetime
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    blueprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    proposed_budget: TeamProposedBudget
    roles: tuple[TeamProjectRoleStatus, ...] = Field(min_length=4, max_length=4)
    reason: PlanReason
    eligible_for_separate_design_review: bool
    managing_director_approval_required: Literal[True] = True
    automatic_instance_creation: Literal[False] = False
    review_plan_is_execution_authority: Literal[False] = False
    resource_budget_enforced_at_runtime: Literal[False] = False
    agent_registry_changed: Literal[False] = False
    active_agents_created: Literal[False] = False
    tasks_created: Literal[False] = False
    skill_activation_authority: Literal[False] = False
    tool_grants: tuple[()] = ()
    worker_grants: tuple[()] = ()
    space_grants: tuple[()] = ()
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _verify_plan(self):
        if self.plan_sha256 != _sha(self.model_dump(mode="json", exclude={"plan_sha256"})):
            raise ValueError("harness_project_review_plan_digest_invalid")
        levels = ("vp", "director", "manager", "specialist")
        if tuple(item.organizational_level for item in self.roles) != levels:
            raise ValueError("harness_project_role_order_invalid")
        role_ids = tuple(item.agent_id for item in self.roles)
        if (
            len(set(role_ids)) != 4
            or tuple(item.reports_to for item in self.roles) != (None, *role_ids[:3])
            or self.eligible_for_separate_design_review
            != (self.reason == "eligible_for_design_review_only")
        ):
            raise ValueError("harness_project_review_plan_structure_invalid")
        return self


class HarnessProjectReviewService:
    """Bind one operator proposal to current Project, registry and draft source."""

    def __init__(self, store: SessionStore, factory: AgentFactory):
        self.store = store
        self.factory = factory

    def preview(
        self,
        template: TeamTemplate,
        request: TeamProjectPlanningRequest,
        *,
        policy_session: Session | None = None,
    ) -> HarnessProjectReviewPlan:
        blueprint = propose_team(template)
        if blueprint.fingerprint != request.expected_blueprint_sha256:
            raise TeamProjectPlanningRefusal("harness_project_blueprint_stale")
        role_ids = tuple(item.agent_id for item in blueprint.roles)
        # Durable operator design reviews supply an already-serialized writer
        # session, so the exact proposal is rechecked under the same lock.
        with (
            self.store.read() if policy_session is None else nullcontext(policy_session)
        ) as session:
            project = session.get(Project, request.project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            # Read from one session, not separate inconsistent project/role calls.
            # Aggregate in SQLite instead of materializing unbounded historical
            # AgentInstance rows merely to report four operator-only counts.
            reservation_count = dict.fromkeys(role_ids, 0)
            for agent_id, count in session.execute(
                select(AgentInstance.agent_id, func.count())
                .where(
                    AgentInstance.agent_id.in_(role_ids),
                    AgentInstance.status != "retired",
                )
                .group_by(AgentInstance.agent_id)
            ):
                reservation_count[agent_id] = count
            registered = {
                agent_id: self.factory.registry.get_agent(agent_id) is not None
                for agent_id in role_ids
            }
            project_status = project.status
            updated_at = project.updated_at
            project_id = project.id

        if project_status != "active":
            reason: PlanReason = "project_inactive"
        elif any(registered.values()):
            reason = "agent_name_already_registered"
        elif any(reservation_count.values()):
            reason = "agent_instance_identity_reserved"
        elif request.proposed_budget.planned_instance_slots < len(role_ids):
            reason = "draft_instance_budget_insufficient"
        else:
            reason = "eligible_for_design_review_only"

        roles = tuple(
            TeamProjectRoleStatus(
                agent_id=role.agent_id,
                organizational_level=role.organizational_level,
                reports_to=parent,
                existing_instance_count=reservation_count[role.agent_id],
                already_registered=registered[role.agent_id],
                eligible_for_separate_design_review=(
                    project_status == "active"
                    and not registered[role.agent_id]
                    and reservation_count[role.agent_id] == 0
                    and request.proposed_budget.planned_instance_slots == 4
                ),
            )
            for role, parent in zip(blueprint.roles, blueprint.parent_ids, strict=True)
        )
        material = {
            "schema_version": "zelo-harness-project-review-plan-v1",
            "template": template,
            "project_id": project_id,
            "project_status": project_status,
            "project_updated_at": updated_at,
            "registry_version": self.factory.registry.version,
            "blueprint_sha256": blueprint.fingerprint,
            "source_commit": blueprint.source_commit,
            "proposed_budget": request.proposed_budget,
            "roles": roles,
            "reason": reason,
            "eligible_for_separate_design_review": reason == "eligible_for_design_review_only",
            "managing_director_approval_required": True,
            "automatic_instance_creation": False,
            "review_plan_is_execution_authority": False,
            "resource_budget_enforced_at_runtime": False,
            "agent_registry_changed": False,
            "active_agents_created": False,
            "tasks_created": False,
            "skill_activation_authority": False,
            "tool_grants": (),
            "worker_grants": (),
            "space_grants": (),
        }
        projection = HarnessProjectReviewPlan.model_construct(**material, plan_sha256="0" * 64)
        return HarnessProjectReviewPlan(
            **material,
            plan_sha256=_sha(projection.model_dump(mode="json", exclude={"plan_sha256"})),
        )
