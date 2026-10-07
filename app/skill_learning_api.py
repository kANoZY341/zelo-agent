"""Operator-authenticated proposal reading and immutable review decisions."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import OperatorPrincipal, require_operator
from app.schemas import SkillLearningCandidateResponse
from app.skill_learning import (
    SkillLearningPromotionLink,
    SkillLearningPromotionLinkRequest,
    SkillLearningReviewRequest,
)
from app.skill_learning_candidate import SkillLearningCandidateAdapter
from app.skill_learning_store import StoredSkillLearningProposal

router = APIRouter(prefix="/skill-learning")


@router.get("/proposals")
def list_proposals(
    request: Request,
    _principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> tuple[StoredSkillLearningProposal, ...]:
    return request.app.state.skill_learning_store.list()


@router.get("/proposals/{proposal_id}")
def get_proposal(
    proposal_id: str,
    request: Request,
    _principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> StoredSkillLearningProposal:
    proposal = request.app.state.skill_learning_store.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="skill_learning_proposal_not_found")
    return proposal


@router.post("/proposals/{proposal_id}/review")
def review_proposal(
    proposal_id: str,
    body: SkillLearningReviewRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> StoredSkillLearningProposal:
    try:
        result, _created = request.app.state.skill_learning_store.record_review(
            proposal_id, body, principal
        )
        return result
    except LookupError:
        raise HTTPException(status_code=404, detail="skill_learning_proposal_not_found") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="skill_learning_reviewer_forbidden") from None
    except ValueError as error:
        code = str(error)
        status = (
            409
            if code
            in {
                "skill_learning_proposal_stale",
                "skill_learning_proposal_already_reviewed",
            }
            else 422
        )
        raise HTTPException(status_code=status, detail=code) from None


@router.post(
    "/proposals/{proposal_id}/candidate",
    response_model=SkillLearningCandidateResponse,
)
def materialize_candidate(
    proposal_id: str,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillLearningCandidateResponse:
    root = request.app.state.settings.skill_learning_candidate_root
    adapter = getattr(request.app.state, "skill_learning_candidate_adapter", None)
    if root is None or not isinstance(adapter, SkillLearningCandidateAdapter):
        raise HTTPException(status_code=409, detail="skill_learning_candidate_staging_unconfigured")
    try:
        artifact = adapter.materialize(
            proposal_id,
            staging_root=root,
            principal=principal,
        )
    except OSError:
        raise HTTPException(status_code=409, detail="skill_learning_candidate_io_refused") from None
    except ValueError as error:
        code = str(error)
        if not code.startswith("skill_learning_candidate_"):
            code = "skill_learning_candidate_refused"
        raise HTTPException(status_code=409, detail=code) from None
    return SkillLearningCandidateResponse(
        proposal_id=artifact.proposal_id,
        review_fingerprint=artifact.review_fingerprint,
        relative_path=artifact.relative_path,
        content_sha256=artifact.content_sha256,
        artifact_sha256=artifact.artifact_sha256,
        created=artifact.created,
    )


@router.get(
    "/proposals/{proposal_id}/promotion-link",
    response_model=SkillLearningPromotionLink,
)
def get_promotion_link(
    proposal_id: str,
    request: Request,
    _principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillLearningPromotionLink:
    link = request.app.state.skill_learning_store.get_promotion_link(proposal_id)
    if link is None:
        raise HTTPException(status_code=404, detail="skill_learning_promotion_link_not_found")
    return link


@router.post(
    "/proposals/{proposal_id}/promotion-link",
    response_model=SkillLearningPromotionLink,
)
def record_promotion_link(
    proposal_id: str,
    body: SkillLearningPromotionLinkRequest,
    request: Request,
    principal: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> SkillLearningPromotionLink:
    try:
        link, _created = request.app.state.skill_learning_store.record_promotion_link(
            proposal_id,
            body.merge_task_id,
            principal,
        )
        return link
    except LookupError:
        raise HTTPException(status_code=404, detail="skill_learning_proposal_not_found") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="skill_learning_reviewer_forbidden") from None
    except ValueError as error:
        code = str(error)
        if not code.startswith("skill_learning_") or not code.replace("_", "").isalnum():
            code = "skill_learning_promotion_link_refused"
        conflict = {
            "skill_learning_candidate_not_accepted_or_stale",
            "skill_learning_proposal_stale",
            "skill_learning_promotion_already_linked",
            "skill_learning_promotion_merge_reused",
            "skill_learning_promoted_head_advanced",
            "skill_learning_promotion_predecessor_missing",
            "skill_learning_promotion_repository_unavailable",
        }
        raise HTTPException(status_code=409 if code in conflict else 422, detail=code) from None
