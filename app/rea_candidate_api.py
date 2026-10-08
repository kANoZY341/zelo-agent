"""Operator-only view of REA's inert candidate definition."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_operator
from app.rea_candidate import ReaCandidateSnapshot, rea_candidate_snapshot
from app.rea_tool_inventory import (
    ReaCatalogRefusal,
    ReaToolInventorySnapshot,
    rea_tool_inventory,
)

router = APIRouter(dependencies=[Depends(require_operator)])


@router.get("/integration-candidates/rea", response_model=ReaCandidateSnapshot)
def get_rea_candidate() -> ReaCandidateSnapshot:
    return rea_candidate_snapshot()


@router.get("/integration-candidates/rea/tools", response_model=ReaToolInventorySnapshot)
def get_rea_tool_inventory() -> ReaToolInventorySnapshot:
    try:
        return rea_tool_inventory()
    except ReaCatalogRefusal:
        raise HTTPException(status_code=503, detail="rea_catalog_unavailable") from None
