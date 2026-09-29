"""Authenticated read-only performance and reported-cost evidence routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.performance import PerformanceEvidenceService
from app.schemas import PerformanceCostEvidence

router = APIRouter(prefix="/performance", dependencies=[Depends(require_operator)])


def service(request: Request) -> PerformanceEvidenceService:
    return request.app.state.performance_evidence


Performance = Annotated[PerformanceEvidenceService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=500)]


@router.get("/evidence", response_model=PerformanceCostEvidence)
def performance_evidence(
    performance: Performance,
    limit: Limit = 200,
) -> PerformanceCostEvidence:
    return performance.evidence(limit=limit)
