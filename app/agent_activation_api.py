"""Operator-visible Agent Factory activation evaluation evidence."""

from fastapi import APIRouter, Depends, Request

from app.agent_activation import AgentActivationReport
from app.auth import require_operator
from app.youtube_research_specialist import (
    YoutubeResearchCandidateView,
    youtube_research_candidate_view,
)

router = APIRouter(
    prefix="/agent-factory",
    dependencies=[Depends(require_operator)],
)


@router.get("/activation", response_model=AgentActivationReport)
def get_activation_report(request: Request) -> AgentActivationReport:
    return request.app.state.agent_activation_report


@router.get("/optional-specialists/youtube-research", response_model=YoutubeResearchCandidateView)
def preview_youtube_research_candidate(request: Request) -> YoutubeResearchCandidateView:
    """Exact trusted opt-in digest, not a request to enable or create an agent."""

    return youtube_research_candidate_view(request.app.state.agent_registry)
