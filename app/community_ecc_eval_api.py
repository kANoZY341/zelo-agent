"""Authenticated, read-only ECC fixed-case evaluation preview.

This does not publish Skills, admit model prompts or create Zelo Tasks.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.auth import require_operator
from app.community_ecc_eval import (
    EccCaseSubmission,
    EccFixedEvaluation,
    EccFixedSuite,
    EccFixtureRefusal,
    ecc_fixed_suite,
    evaluate_ecc_fixed_cases,
)
from app.community_ecc_playbooks import EccPlaybookRefusal
from app.community_sources import CommunitySourceRefusal

MAX_ECC_EVAL_BYTES = 8192

router = APIRouter(
    prefix="/integration-candidates/community/ecc",
    dependencies=[Depends(require_operator)],
)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for name, value in pairs:
        if name in obj:
            raise ValueError("duplicate_ecc_evaluation_field")
        obj[name] = value
    return obj


@router.get("/{skill_id}/cases", response_model=EccFixedSuite)
def read_ecc_fixed_cases(skill_id: str) -> EccFixedSuite:
    """List Zelo-owned synthetic evidence, never the hidden expected label."""
    try:
        return ecc_fixed_suite(skill_id)
    except EccPlaybookRefusal as error:
        if str(error) == "ecc_playbook_unknown":
            raise HTTPException(status_code=404, detail="ecc_fixture_unknown_skill") from None
        raise HTTPException(status_code=503, detail="ecc_fixture_source_unavailable") from None
    except (EccFixtureRefusal, CommunitySourceRefusal):
        raise HTTPException(status_code=503, detail="ecc_fixture_source_unavailable") from None


@router.post("/{skill_id}/evaluate", response_model=EccFixedEvaluation)
async def submit_ecc_fixed_answers(skill_id: str, request: Request) -> EccFixedEvaluation:
    """Score exact operator-supplied answers with no I/O beyond source verification."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="ecc_evaluation_json_required")
    try:
        length = request.headers.get("content-length")
        if length is not None:
            content_length = int(length)
            if content_length < 0:
                raise HTTPException(status_code=422, detail="ecc_evaluation_invalid")
            if content_length > MAX_ECC_EVAL_BYTES:
                raise HTTPException(status_code=413, detail="ecc_evaluation_size_denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="ecc_evaluation_invalid") from None
    raw = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_ECC_EVAL_BYTES - len(raw):
            raise HTTPException(status_code=413, detail="ecc_evaluation_size_denied")
        raw.extend(chunk)
    if not raw:
        raise HTTPException(status_code=422, detail="ecc_evaluation_invalid")
    try:
        json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
        submission = EccCaseSubmission.model_validate_json(bytes(raw), strict=True)
    except (ValueError, ValidationError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(status_code=422, detail="ecc_evaluation_invalid") from None
    try:
        return await run_in_threadpool(evaluate_ecc_fixed_cases, skill_id, submission)
    except EccPlaybookRefusal as error:
        if str(error) == "ecc_playbook_unknown":
            raise HTTPException(status_code=404, detail="ecc_fixture_unknown_skill") from None
        raise HTTPException(status_code=503, detail="ecc_fixture_source_unavailable") from None
    except EccFixtureRefusal as error:
        if str(error) == "ecc_fixture_identity_stale":
            raise HTTPException(status_code=409, detail="ecc_fixture_identity_stale") from None
        raise HTTPException(status_code=422, detail="ecc_fixture_case_ids_invalid") from None
    except CommunitySourceRefusal:
        raise HTTPException(status_code=503, detail="ecc_fixture_source_unavailable") from None
