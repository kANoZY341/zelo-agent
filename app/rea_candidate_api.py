"""Operator-only view of REA's inert candidate definition."""

from fastapi import APIRouter, Depends

from app.auth import require_operator
from app.rea_candidate import ReaCandidateSnapshot, rea_candidate_snapshot

router = APIRouter(dependencies=[Depends(require_operator)])


@router.get("/integration-candidates/rea", response_model=ReaCandidateSnapshot)
def get_rea_candidate() -> ReaCandidateSnapshot:
    return rea_candidate_snapshot()
