"""Harness-inspired, immutable Agent Factory team proposals. Never activation."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_factory import AgentSpec
from app.community_sources import verify_community_sources

BLUEPRINT_SCHEMA = "zelo-community-team-blueprint-v1"
_HARNESS = "revfactory/harness"
TeamTemplate = Literal[
    "engineering_quality", "business_research", "youtube_content", "trading_research"
]


class TeamBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-community-team-blueprint-v1"] = BLUEPRINT_SCHEMA
    template: TeamTemplate
    source_repository: Literal["revfactory/harness"] = _HARNESS
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    roles: tuple[AgentSpec, ...] = Field(min_length=4, max_length=4)
    parent_ids: tuple[str | None, ...] = Field(min_length=4, max_length=4)
    managing_director_approval_required: Literal[True] = True
    ready_to_activate: Literal[False] = False
    tasks_created: Literal[False] = False
    worker_grants: tuple[()] = ()
    tool_grants: tuple[()] = ()
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self):
        tiers = ("vp", "director", "manager", "specialist")
        if tuple(x.organizational_level for x in self.roles) != tiers:
            raise ValueError("harness_reporting_levels_invalid")
        ids = tuple(x.agent_id for x in self.roles)
        if self.parent_ids != (None, *ids[:3]) or len(set(ids)) != 4:
            raise ValueError("harness_reporting_graph_invalid")
        if any(
            x.lifecycle != "draft"
            or x.allowed_tools
            or x.allowed_task_kinds
            or x.permissions
            or x.space_permissions
            or x.computer is not None
            or x.skill_learning != "disabled"
            or x.allow_subagents
            for x in self.roles
        ):
            raise ValueError("harness_blueprint_execution_forbidden")
        if self.fingerprint != _hash(self.model_dump(mode="json", exclude={"fingerprint"})):
            raise ValueError("harness_blueprint_fingerprint_invalid")
        return self


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def propose_team(template: TeamTemplate) -> TeamBlueprint:
    """No live organization changes, only checked draft AgentSpec records."""

    if template not in (
        "engineering_quality",
        "business_research",
        "youtube_content",
        "trading_research",
    ):
        raise ValueError("harness_template_unknown")
    source = next(x for x in verify_community_sources() if x.name == "harness")
    if template == "engineering_quality":
        names = ("engineering", "engineering_qa", "engineering_review", "code_auditor")
        objectives = (
            "Coordinate bounded engineering quality planning",
            "Design review and test strategies under Core authority",
            "Manage review scope, evaluation and escalation",
            "Examine code evidence and propose findings for independent review",
        )
        capabilities = ("code_planning", "code_planning", "code_review", "code_review")
    elif template == "business_research":
        names = ("business", "business_intelligence", "business_research", "market_analyst")
        objectives = (
            "Coordinate bounded business research strategy",
            "Develop research scope and evidence standards",
            "Manage specialist investigations and source quality",
            "Summarize market evidence without placing orders or commitments",
        )
        capabilities = (
            "code_planning",
            "evidence_synthesis",
            "evidence_synthesis",
            "evidence_synthesis",
        )
    elif template == "youtube_content":
        names = ("youtube", "youtube_content", "youtube_research", "youtube_script_writer")
        objectives = (
            "Coordinate research and content plans without channel publishing authority",
            "Design YouTube content workflow proposals under Core approval boundaries",
            "Manage source-grounded topics, editorial review and originality checks",
            "Draft a source-grounded script proposal without external uploads",
        )
        capabilities = (
            "code_planning",
            "evidence_synthesis",
            "evidence_synthesis",
            "evidence_synthesis",
        )
    else:  # trading_research only; no portfolio or order execution rights
        names = ("trading", "trading_intelligence", "trading_risk", "market_researcher")
        objectives = (
            "Coordinate financial market research without orders or account access",
            "Analyze public market evidence and document data uncertainty",
            "Manage risk-analysis questions and human-only escalation boundaries",
            "Prepare sourced research notes without investing or trading actions",
        )
        capabilities = (
            "code_planning",
            "evidence_synthesis",
            "evidence_synthesis",
            "evidence_synthesis",
        )
    roles = tuple(
        AgentSpec(
            agent_id=name,
            role=name,
            organizational_level=tier,
            objective=objective,
            capabilities=(capability,),
            memory_namespace=f"agent/{name}",
            review_required=True,
            lifecycle="draft",
        )
        for name, tier, objective, capability in zip(
            names,
            ("vp", "director", "manager", "specialist"),
            objectives,
            capabilities,
            strict=True,
        )
    )
    values = {
        "schema_version": BLUEPRINT_SCHEMA,
        "template": template,
        "source_repository": _HARNESS,
        "source_commit": source.commit,
        "roles": tuple(role.model_dump(mode="json") for role in roles),
        "parent_ids": (None, *names[:3]),
        "managing_director_approval_required": True,
        "ready_to_activate": False,
        "tasks_created": False,
        "worker_grants": (),
        "tool_grants": (),
    }
    return TeamBlueprint(**values, fingerprint=_hash(values))
