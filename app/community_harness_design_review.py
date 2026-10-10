"""Append-only operator decisions about *designs*, never Agent activation.

Every decision is checked against the current Project, Agent Registry, role
reservations, Harness blueprint and exact budget inside one serialized writer.
"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.community_harness import TeamTemplate
from app.community_harness_project import (
    HarnessProjectReviewService,
    TeamProjectPlanningRequest,
)
from app.models import HarnessTeamDesignReview, new_id, utcnow
from app.policy import DomainError
from app.storage import SessionStore

DesignDecision = Literal["accepted_for_design_review", "rejected_for_design_review"]


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    ).hexdigest()


class TeamDesignReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal: TeamProjectPlanningRequest
    expected_review_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    decision: DesignDecision


class TeamDesignReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-harness-team-design-review-v1"] = (
        "zelo-harness-team-design-review-v1"
    )
    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    project_id: str
    project_status: Literal["active", "paused", "completed", "archived"]
    template: TeamTemplate
    blueprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning_reason: Literal[
        "eligible_for_design_review_only",
        "project_inactive",
        "agent_name_already_registered",
        "agent_instance_identity_reserved",
        "draft_instance_budget_insufficient",
    ]
    decision: DesignDecision
    idempotency_key: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_subject: Literal["ahmad"] = "ahmad"
    created_at: datetime
    record_is_historical: Literal[True] = True
    current_plan_eligibility_claimed: Literal[False] = False
    real_team_activated: Literal[False] = False
    design_review_is_execution_authority: Literal[False] = False
    worker_or_tool_or_space_grants: tuple[()] = ()
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _integrity(self):
        if self.receipt_sha256 != _sha(self.model_dump(mode="json", exclude={"receipt_sha256"})):
            raise ValueError("harness_team_design_receipt_digest_invalid")
        if (
            self.decision == "accepted_for_design_review"
            and self.planning_reason != "eligible_for_design_review_only"
        ):
            raise ValueError("harness_team_design_acceptance_ineligible")
        return self


def _receipt(row: HarnessTeamDesignReview) -> TeamDesignReviewReceipt:
    try:
        return TeamDesignReviewReceipt(
            id=row.id,
            project_id=row.project_id,
            project_status=row.project_status,
            template=row.template,
            blueprint_sha256=row.blueprint_sha256,
            registry_version=row.registry_version,
            review_plan_sha256=row.review_plan_sha256,
            planning_reason=row.planning_reason,
            decision=row.decision,
            idempotency_key=row.idempotency_key,
            request_sha256=row.request_sha256,
            operator_subject=row.operator_subject,
            created_at=row.created_at,
            receipt_sha256=row.receipt_sha256,
        )
    except (TypeError, ValueError):
        raise DomainError("Team design review integrity check failed", 409) from None


class HarnessTeamDesignReviewService:
    """Serialized review event, never used to grant any operational authority."""

    def __init__(self, store: SessionStore, plans: HarnessProjectReviewService) -> None:
        self.store = store
        self.plans = plans

    def record(
        self,
        template: TeamTemplate,
        body: TeamDesignReviewRequest,
        *,
        operator_subject: Literal["ahmad"],
    ) -> TeamDesignReviewReceipt:
        if operator_subject != "ahmad":
            raise DomainError("Operator required", 403)
        request_sha256 = _sha({"template": template, "body": body.model_dump(mode="json")})
        with self.store.transaction() as session:
            # Crucially, the entire snapshot is checked while SQLite's BEGIN
            # IMMEDIATE lock is held. A stale Project/role cannot be reviewed.
            plan = self.plans.preview(template, body.proposal, policy_session=session)
            if plan.plan_sha256 != body.expected_review_plan_sha256:
                raise DomainError("Harness team design plan is stale", 409)
            if (
                body.decision == "accepted_for_design_review"
                and not plan.eligible_for_separate_design_review
            ):
                raise DomainError("Harness team design is not eligible for review", 409)

            existing = session.scalar(
                select(HarnessTeamDesignReview).where(
                    HarnessTeamDesignReview.idempotency_key == body.idempotency_key
                )
            )
            if existing is not None:
                if existing.request_sha256 != request_sha256 or existing.template != template:
                    raise DomainError("Harness team design review idempotency conflict", 409)
                return _receipt(existing)

            now = utcnow()
            receipt_id = new_id()
            material = {
                "schema_version": "zelo-harness-team-design-review-v1",
                "id": receipt_id,
                "project_id": plan.project_id,
                "project_status": plan.project_status,
                "template": template,
                "blueprint_sha256": plan.blueprint_sha256,
                "registry_version": plan.registry_version,
                "review_plan_sha256": plan.plan_sha256,
                "planning_reason": plan.reason,
                "decision": body.decision,
                "idempotency_key": body.idempotency_key,
                "request_sha256": request_sha256,
                "operator_subject": operator_subject,
                "created_at": now,
                "record_is_historical": True,
                "current_plan_eligibility_claimed": False,
                "real_team_activated": False,
                "design_review_is_execution_authority": False,
                "worker_or_tool_or_space_grants": (),
            }
            receipt = TeamDesignReviewReceipt.model_construct(**material, receipt_sha256="0" * 64)
            digest = _sha(receipt.model_dump(mode="json", exclude={"receipt_sha256"}))
            receipt = TeamDesignReviewReceipt(**material, receipt_sha256=digest)
            session.add(
                HarnessTeamDesignReview(
                    id=receipt_id,
                    project_id=plan.project_id,
                    project_status=plan.project_status,
                    template=template,
                    blueprint_sha256=plan.blueprint_sha256,
                    registry_version=plan.registry_version,
                    review_plan_sha256=plan.plan_sha256,
                    planning_reason=plan.reason,
                    decision=body.decision,
                    idempotency_key=body.idempotency_key,
                    request_sha256=request_sha256,
                    operator_subject=operator_subject,
                    created_at=now,
                    receipt_sha256=digest,
                )
            )
            session.flush()
            return receipt

    def get(self, review_id: str) -> TeamDesignReviewReceipt:
        with self.store.read() as session:
            row = session.get(HarnessTeamDesignReview, review_id)
            if row is None:
                raise DomainError("Team design review not found", 404)
            return _receipt(row)
