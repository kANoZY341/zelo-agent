"""Authenticated Project and Goal ownership routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.projects import ProjectService
from app.schemas import (
    GoalCreate,
    GoalPage,
    GoalResponse,
    ProjectCreate,
    ProjectPage,
    ProjectResponse,
)
from app.youtube_company_attempt_budget import (
    YoutubeCompanyAttemptBudgetService,
    YoutubeCompanyAttemptBudgetView,
)

router = APIRouter(prefix="/projects", dependencies=[Depends(require_operator)])


def service(request: Request) -> ProjectService:
    return request.app.state.projects


Projects = Annotated[ProjectService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(body: ProjectCreate, projects: Projects) -> ProjectResponse:
    return projects.create(body)


@router.get("", response_model=ProjectPage)
def list_projects(
    projects: Projects,
    limit: Limit = 20,
    offset: Offset = 0,
) -> ProjectPage:
    items, total = projects.list_projects(limit, offset)
    return ProjectPage(items=items, limit=limit, offset=offset, total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, projects: Projects) -> ProjectResponse:
    return projects.get(project_id)


@router.get(
    "/{project_id}/youtube-company-attempt-budget",
    response_model=YoutubeCompanyAttemptBudgetView,
)
def read_youtube_company_attempt_budget(
    project_id: str, request: Request
) -> YoutubeCompanyAttemptBudgetView:
    """Owner-only, read-only measured Worker attempt evidence, no authority."""

    return YoutubeCompanyAttemptBudgetService(request.app.state.storage).preview(
        project_id,
        current_registry_version=request.app.state.agent_registry.version,
    )


@router.post("/{project_id}/goals", response_model=GoalResponse, status_code=201)
def create_goal(project_id: str, body: GoalCreate, projects: Projects) -> GoalResponse:
    return projects.create_goal(project_id, body)


@router.get("/{project_id}/goals", response_model=GoalPage)
def list_goals(
    project_id: str,
    projects: Projects,
    limit: Limit = 20,
    offset: Offset = 0,
) -> GoalPage:
    items, total = projects.list_goals(project_id, limit, offset)
    return GoalPage(items=items, limit=limit, offset=offset, total=total)
