"""Operator-only durable REA target selection metadata; never analyzer execution."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.auth import OperatorPrincipal, require_operator
from app.rea_operator_targets import (
    ReaOperatorTargetSelectionInput,
    ReaOperatorTargetSnapshot,
    ReaOperatorTargetStore,
)

router = APIRouter(dependencies=[Depends(require_operator)])


class ReaRevocationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    revoked: bool


def _target_store(request: Request) -> ReaOperatorTargetStore:
    """Use only Core's trusted local metadata store; never access target bytes."""

    return ReaOperatorTargetStore(request.app.state.database)


@router.post(
    "/integration-candidates/rea/operator-targets", response_model=ReaOperatorTargetSnapshot
)
def select_rea_operator_target(
    selection: ReaOperatorTargetSelectionInput,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> ReaOperatorTargetSnapshot:
    """Record an operator-asserted review claim, not permission to run REA."""

    try:
        snapshot, _created = _target_store(request).select(selection.review, principal=principal)
        return snapshot
    except ValueError as error:
        if str(error) in {"rea_operator_target_selection_conflict", "rea_operator_target_revoked"}:
            raise HTTPException(status_code=409, detail="rea_operator_target_conflict") from None
        raise HTTPException(status_code=503, detail="rea_operator_target_unavailable") from None


@router.get(
    "/integration-candidates/rea/operator-targets/{snapshot_id}",
    response_model=ReaOperatorTargetSnapshot,
)
def get_rea_operator_target(
    snapshot_id: str,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> ReaOperatorTargetSnapshot:
    try:
        record = _target_store(request).get(snapshot_id, principal=principal)
        if record is None:
            raise HTTPException(status_code=404, detail="rea_operator_target_not_found")
        return record
    except ValueError:
        raise HTTPException(status_code=404, detail="rea_operator_target_not_found") from None


@router.post(
    "/integration-candidates/rea/operator-targets/{snapshot_id}/revoke",
    response_model=ReaRevocationResult,
)
def revoke_rea_operator_target(
    snapshot_id: str,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> ReaRevocationResult:
    """Irreversible operator refusal; no mutation to a selection or tool grant."""

    try:
        return ReaRevocationResult(
            revoked=_target_store(request).revoke(snapshot_id, principal=principal)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="rea_operator_target_not_found") from None
    except ValueError as error:
        if str(error) == "rea_target_identity_invalid":
            raise HTTPException(status_code=404, detail="rea_operator_target_not_found") from None
        raise HTTPException(status_code=503, detail="rea_operator_target_unavailable") from None
