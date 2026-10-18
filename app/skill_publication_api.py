"""Operator-only API for *inert* learned-Skill publication evidence.

Never grants GitHub merge, Skill loading, Worker, Tool or delegated authority.
Only the explicit owner can reserve or inspect a claim and record an immutable
review decision. Fresh external checks use GET only and fail closed.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.auth import OperatorPrincipal, require_operator
from app.developer_process import DeveloperError
from app.models import SkillPublicationOperatorDecisionRecord
from app.promotion_contracts import MergeRequest
from app.skill_learning import Sha256
from app.skill_publication_blob_evidence import (
    SkillPublicationBlobEvidence,
    SkillPublicationBlobEvidenceService,
)
from app.skill_publication_claim import (
    SkillPublicationClaim,
    SkillPublicationClaimService,
)
from app.skill_publication_dispatch_fence import SkillPublicationDispatchFenceService
from app.skill_publication_merge_preview import (
    SkillPublicationMergePreview,
    SkillPublicationMergePreviewService,
)
from app.skill_publication_operator_decision import (
    SkillPublicationOperatorDecision,
    SkillPublicationOperatorDecisionService,
)
from app.skill_publication_post_attempt_reconciliation import (
    POST_ATTEMPT_RECONCILIATION_CONFIRMATION,
    SkillPublicationPostAttemptReconciliation,
    SkillPublicationPostAttemptReconciliationService,
)
from app.skill_publication_remote_evidence import (
    SkillPublicationRemoteEvidence,
    SkillPublicationRemoteEvidenceService,
)

router = APIRouter(prefix="/skill-publication")


class SkillClaimReserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    expected_intent_fingerprint: Sha256


class SkillDecisionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    confirmation: Literal["APPROVE_EXACT_SKILL_PUBLICATION_REVIEW"]
    merge_request: MergeRequest


class SkillPostAttemptReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    confirmation: Literal[POST_ATTEMPT_RECONCILIATION_CONFIRMATION]
    merge_request: MergeRequest


def _claims(request: Request) -> SkillPublicationClaimService:
    return SkillPublicationClaimService(request.app.state.skill_learning_store)


def _remote(request: Request) -> SkillPublicationRemoteEvidenceService:
    configuration = request.app.state.settings
    if configuration.promotion_repository is None:
        raise HTTPException(
            status_code=409,
            detail="skill_publication_remote_repository_unconfigured",
        )
    return SkillPublicationRemoteEvidenceService(
        SkillPublicationMergePreviewService(_claims(request)), configuration
    )


def _post_attempt_reconciler(request: Request) -> SkillPublicationPostAttemptReconciliationService:
    remote = _remote(request)
    fences = SkillPublicationDispatchFenceService(SkillPublicationBlobEvidenceService(remote))
    return SkillPublicationPostAttemptReconciliationService(fences, transport=remote.transport)


def _refuse_code(error: ValueError | PermissionError, default: str) -> HTTPException:
    code = str(error)
    if (
        not code.startswith("skill_publication_")
        or not code.replace("_", "").isalnum()
        or len(code) > 100
    ):
        code = default
    return HTTPException(status_code=409, detail=code)


def _remote_error(error: DeveloperError) -> HTTPException:
    # Public error responses never include GitHub response bodies, tokens,
    # internal URLs, stderr or arbitrary exception representations.
    return HTTPException(
        status_code=503 if str(error) == "promotion_github_api_failed" else 409,
        detail="skill_publication_remote_evidence_unavailable",
    )


@router.post(
    "/proposals/{proposal_id}/claim",
    response_model=SkillPublicationClaim,
    status_code=201,
)
def reserve_operator_claim(
    proposal_id: str,
    body: SkillClaimReserveRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationClaim:
    try:
        result, _created = _claims(request).reserve(
            proposal_id, body.expected_intent_fingerprint, principal
        )
    except LookupError:
        raise HTTPException(
            status_code=404, detail="skill_publication_proposal_not_found"
        ) from None
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_claim_refused") from None
    return result


@router.get("/claims/{claim_id}", response_model=SkillPublicationClaim)
def read_operator_claim(
    claim_id: str,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationClaim:
    claim = _claims(request).get(claim_id, principal)
    if claim is None:
        raise HTTPException(status_code=404, detail="skill_publication_claim_not_found")
    return claim


@router.post("/claims/{claim_id}/preview", response_model=SkillPublicationMergePreview)
def preview_operator_review(
    claim_id: str,
    body: MergeRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationMergePreview:
    try:
        return SkillPublicationMergePreviewService(_claims(request)).preview(
            claim_id, body, principal
        )
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_preview_refused") from None


@router.post("/claims/{claim_id}/remote-evidence", response_model=SkillPublicationRemoteEvidence)
async def inspect_current_github_ci(
    claim_id: str,
    body: MergeRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationRemoteEvidence:
    try:
        return await _remote(request).verify(claim_id, body, principal)
    except DeveloperError as error:
        raise _remote_error(error) from None
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_remote_refused") from None


@router.post("/claims/{claim_id}/blob-evidence", response_model=SkillPublicationBlobEvidence)
async def inspect_exact_immutable_blob(
    claim_id: str,
    body: MergeRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationBlobEvidence:
    try:
        return await SkillPublicationBlobEvidenceService(_remote(request)).verify(
            claim_id, body, principal
        )
    except DeveloperError as error:
        raise _remote_error(error) from None
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_blob_refused") from None


@router.post(
    "/claims/{claim_id}/post-attempt-reconciliation",
    response_model=SkillPublicationPostAttemptReconciliation,
    status_code=201,
)
async def record_post_attempt_reconciliation(
    claim_id: str,
    body: SkillPostAttemptReconciliationRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationPostAttemptReconciliation:
    try:
        return await _post_attempt_reconciler(request).reconcile(
            claim_id, body.merge_request, body.confirmation, principal
        )
    except DeveloperError as error:
        raise _remote_error(error) from None
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_post_attempt_reconciliation_refused") from None


@router.get(
    "/claims/{claim_id}/post-attempt-reconciliations",
    response_model=list[SkillPublicationPostAttemptReconciliation],
)
def read_post_attempt_reconciliations(
    claim_id: str,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> list[SkillPublicationPostAttemptReconciliation]:
    try:
        return SkillPublicationPostAttemptReconciliationService.list_recent_for_store(
            request.app.state.skill_learning_store, claim_id, principal
        )
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_post_attempt_reconciliation_refused") from None


@router.post(
    "/claims/{claim_id}/decision",
    response_model=SkillPublicationOperatorDecision,
    status_code=201,
)
async def select_exact_candidate_for_review(
    claim_id: str,
    body: SkillDecisionReviewRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationOperatorDecision:
    try:
        chosen, _created = await SkillPublicationOperatorDecisionService(
            _remote(request)
        ).record_approval(claim_id, body.merge_request, body.confirmation, principal)
    except DeveloperError as error:
        raise _remote_error(error) from None
    except (PermissionError, ValueError) as error:
        raise _refuse_code(error, "skill_publication_decision_refused") from None
    return chosen


@router.get("/claims/{claim_id}/decision", response_model=SkillPublicationOperatorDecision)
def read_historical_review_selection(
    claim_id: str,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillPublicationOperatorDecision:
    with request.app.state.skill_learning_store.database.read() as session:
        row = session.scalar(
            select(SkillPublicationOperatorDecisionRecord).where(
                SkillPublicationOperatorDecisionRecord.claim_id == claim_id
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="skill_publication_decision_not_found")
        return SkillPublicationOperatorDecision.model_validate(row)
