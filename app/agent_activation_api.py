"""Operator-visible Agent Factory activation evaluation evidence."""

from fastapi import APIRouter, Depends, Request

from app.agent_activation import AgentActivationReport
from app.auth import require_operator

router = APIRouter(
    prefix="/agent-factory",
    dependencies=[Depends(require_operator)],
)


@router.get("/activation", response_model=AgentActivationReport)
def get_activation_report(request: Request) -> AgentActivationReport:
    return request.app.state.agent_activation_report
