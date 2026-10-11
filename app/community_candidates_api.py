"""Owner-only view of pinned, inert Zelo community integration candidates."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.auth import OperatorPrincipal, require_operator
from app.community_archify import (
    CommunityArchifyRefusal,
    export_archify_json,
    export_mermaid,
    zelo_architecture_draft,
)
from app.community_archify_harness_project import (
    HarnessProjectArchitectureView,
    harness_project_architecture,
)
from app.community_archify_registry import RegistryArchitectureView, agent_registry_architecture
from app.community_ecc_playbooks import (
    EccManualPlaybook,
    EccPlaybookRefusal,
    ecc_manual_playbook,
)
from app.community_harness import TeamBlueprint, TeamTemplate, propose_team
from app.community_harness_design_review import (
    HarnessTeamDesignReviewService,
    TeamDesignReviewReceipt,
    TeamDesignReviewRequest,
)
from app.community_harness_project import (
    HarnessProjectReviewPlan,
    HarnessProjectReviewService,
    TeamProjectPlanningRefusal,
    TeamProjectPlanningRequest,
)
from app.community_skill_catalog import community_skill_candidates
from app.community_sources import CommunitySourceRefusal, verify_community_sources
from app.skills import SkillSpec

router = APIRouter(
    prefix="/integration-candidates/community",
    dependencies=[Depends(require_operator)],
)


class SourceReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    repository: str
    commit: str
    license: str
    source_files: int
    auto_activation: Literal[False] = False
    tool_grants: tuple[()] = ()


class CommunityReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["zelo-curated-community-readiness-v1"] = (
        "zelo-curated-community-readiness-v1"
    )
    sources: tuple[SourceReadiness, ...]
    context_mode_source_imported: Literal[False] = False
    worker_registered: Literal[False] = False
    license_review_required_before_activation: Literal[True] = True


class DraftSkillView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    skill: SkillSpec
    repository: str
    path: str
    commit: str
    reviewed_steps: tuple[str, ...]
    ready_for_promotion: Literal[False] = False
    tool_grants: tuple[()] = ()


@router.get("", response_model=CommunityReadiness)
def community_source_candidates() -> CommunityReadiness:
    verified = verify_community_sources()
    return CommunityReadiness(
        sources=tuple(
            SourceReadiness(
                name=item.name,
                repository=item.repository,
                commit=item.commit,
                license=item.license,
                source_files=item.file_count,
            )
            for item in verified
        )
    )


@router.get("/skills", response_model=tuple[DraftSkillView, ...])
def read_community_skill_drafts() -> tuple[DraftSkillView, ...]:
    return tuple(
        DraftSkillView(
            skill=candidate.skill,
            repository=candidate.upstream,
            path=candidate.upstream_path,
            commit=candidate.upstream_commit,
            reviewed_steps=candidate.reviewed_steps,
        )
        for candidate in community_skill_candidates()
    )


@router.get("/skills/{skill_id}/playbook", response_model=EccManualPlaybook)
def read_ecc_reviewed_manual_playbook(skill_id: str) -> EccManualPlaybook:
    """Return only Zelo-authored steps for a verified draft ECC skill."""

    try:
        return ecc_manual_playbook(skill_id)
    except (EccPlaybookRefusal, CommunitySourceRefusal) as exc:
        if isinstance(exc, EccPlaybookRefusal) and str(exc) == "ecc_playbook_unknown":
            raise HTTPException(status_code=404, detail="ecc_playbook_unknown") from None
        raise HTTPException(status_code=503, detail="ecc_playbook_unavailable") from None


@router.get("/teams/{template}", response_model=TeamBlueprint)
def get_community_team_draft(
    template: TeamTemplate,
) -> TeamBlueprint:
    return propose_team(template)


@router.post(
    "/teams/{template}/project-readiness",
    response_model=HarnessProjectReviewPlan,
)
def preview_harness_project_readiness(
    template: TeamTemplate,
    body: TeamProjectPlanningRequest,
    request: Request,
) -> HarnessProjectReviewPlan:
    """A project-bound, budgeted draft proposal, never an activation/approval."""

    service = HarnessProjectReviewService(
        request.app.state.storage,
        request.app.state.agent_factory,
    )
    try:
        return service.preview(template, body)
    except TeamProjectPlanningRefusal:
        raise HTTPException(status_code=409, detail="harness_project_blueprint_stale") from None
    except CommunitySourceRefusal:
        raise HTTPException(status_code=503, detail="harness_project_source_unavailable") from None


@router.post(
    "/teams/{template}/project-architecture",
    response_model=HarnessProjectArchitectureView,
)
def preview_harness_project_architecture(
    template: TeamTemplate,
    body: TeamProjectPlanningRequest,
    request: Request,
) -> HarnessProjectArchitectureView:
    """Project-bound draft team diagram; no team registration or permission changes."""

    service = HarnessProjectReviewService(
        request.app.state.storage,
        request.app.state.agent_factory,
    )
    try:
        return harness_project_architecture(service.preview(template, body))
    except TeamProjectPlanningRefusal:
        raise HTTPException(status_code=409, detail="harness_project_blueprint_stale") from None
    except (CommunityArchifyRefusal, CommunitySourceRefusal):
        raise HTTPException(
            status_code=503, detail="harness_project_architecture_unavailable"
        ) from None


@router.post(
    "/teams/{template}/design-reviews",
    response_model=TeamDesignReviewReceipt,
    status_code=201,
)
def record_harness_team_design_review(
    template: TeamTemplate,
    body: TeamDesignReviewRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> TeamDesignReviewReceipt:
    """Store one exact human design judgment; never activate Agents."""

    service = HarnessTeamDesignReviewService(
        request.app.state.storage,
        HarnessProjectReviewService(
            request.app.state.storage,
            request.app.state.agent_factory,
        ),
    )
    try:
        return service.record(template, body, operator_subject=principal.subject)
    except TeamProjectPlanningRefusal:
        raise HTTPException(status_code=409, detail="harness_project_blueprint_stale") from None
    except CommunitySourceRefusal:
        raise HTTPException(status_code=503, detail="harness_project_source_unavailable") from None


@router.get("/team-design-reviews/{review_id}", response_model=TeamDesignReviewReceipt)
def get_harness_team_design_review(
    review_id: str,
    request: Request,
) -> TeamDesignReviewReceipt:
    service = HarnessTeamDesignReviewService(
        request.app.state.storage,
        HarnessProjectReviewService(
            request.app.state.storage,
            request.app.state.agent_factory,
        ),
    )
    return service.get(review_id)


@router.get("/architecture")
def get_archify_architecture_candidate() -> Response:
    return Response(
        content=export_archify_json(zelo_architecture_draft()),
        media_type="application/json",
    )


@router.get("/architecture/agent-registry", response_model=RegistryArchitectureView)
def get_agent_registry_architecture_candidate(request: Request) -> RegistryArchitectureView:
    """Inspect Core-owned registry templates, never live AgentInstances or grants."""

    try:
        return agent_registry_architecture(request.app.state.agent_registry)
    except (CommunityArchifyRefusal, CommunitySourceRefusal):
        raise HTTPException(status_code=503, detail="archify_registry_unavailable") from None


@router.get("/architecture/mermaid")
def get_archify_mermaid_preview() -> Response:
    return Response(
        content=export_mermaid(zelo_architecture_draft()),
        media_type="text/plain; charset=utf-8",
    )
