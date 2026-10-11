"""Operator-only synthetic Open Code Review calibration; no reviewer execution."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.auth import require_operator
from app.community_review_fixed_eval import (
    OcrCalibrationRefusal,
    OcrCalibrationResult,
    OcrCalibrationSubmission,
    OcrFixedSuite,
    evaluate_ocr_fixed_cases,
    ocr_fixed_suite,
)
from app.community_sources import CommunitySourceRefusal

MAX_OCR_CALIBRATION_BYTES = 32768

router = APIRouter(
    prefix="/integration-candidates/community/open-code-review/calibration",
    dependencies=[Depends(require_operator)],
)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ocr_calibration_duplicate_json_key")
        result[key] = value
    return result


@router.get("/cases", response_model=OcrFixedSuite)
async def read_ocr_calibration_cases() -> OcrFixedSuite:
    """Show hypothetical examples and unchanged source identities only."""

    try:
        return await run_in_threadpool(ocr_fixed_suite)
    except CommunitySourceRefusal:
        raise HTTPException(status_code=503, detail="ocr_calibration_source_unavailable") from None


@router.post("/evaluate", response_model=OcrCalibrationResult)
async def submit_ocr_calibration(request: Request) -> OcrCalibrationResult:
    """Grade supplied review text against fixed labels; never run a reviewer."""

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="ocr_calibration_json_required")
    size_header = request.headers.get("content-length")
    if size_header is not None:
        try:
            size = int(size_header)
        except ValueError:
            raise HTTPException(status_code=422, detail="ocr_calibration_invalid") from None
        if size < 0:
            raise HTTPException(status_code=422, detail="ocr_calibration_invalid")
        if size > MAX_OCR_CALIBRATION_BYTES:
            raise HTTPException(status_code=413, detail="ocr_calibration_size_denied")

    raw = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_OCR_CALIBRATION_BYTES - len(raw):
            raise HTTPException(status_code=413, detail="ocr_calibration_size_denied")
        raw.extend(chunk)
    if not raw:
        raise HTTPException(status_code=422, detail="ocr_calibration_invalid")

    try:
        json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
        submission = OcrCalibrationSubmission.model_validate_json(bytes(raw), strict=True)
    except (ValueError, ValidationError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(status_code=422, detail="ocr_calibration_invalid") from None
    try:
        return await run_in_threadpool(evaluate_ocr_fixed_cases, submission)
    except OcrCalibrationRefusal as error:
        if str(error) == "ocr_calibration_suite_stale":
            raise HTTPException(status_code=409, detail="ocr_calibration_suite_stale") from None
        raise HTTPException(status_code=422, detail="ocr_calibration_case_ids_invalid") from None
    except CommunitySourceRefusal:
        raise HTTPException(status_code=503, detail="ocr_calibration_source_unavailable") from None
