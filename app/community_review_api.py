"""Operator-only, content-bounded preview of exact committed Open Code Review evidence.

This adapter consumes raw untrusted reviewer JSON and reads only a trusted
Core-selected repository's committed Git revisions. It NEVER publishes comments,
runs a reviewer, creates a Task, approves an action, or grants tools.
"""

import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.auth import require_operator
from app.community_review import MAX_BYTES, OcrEvidenceEnvelope, OcrEvidenceRefusal
from app.community_review_git_scope import (
    CommunityGitScopeRefusal,
    bind_ocr_review_from_git,
)
from app.community_sources import CommunitySourceRefusal
from app.config import ROOT
from app.developer_contracts import ReviewerReport

router = APIRouter(
    prefix="/integration-candidates/community/open-code-review",
    dependencies=[Depends(require_operator)],
)

# This HTTP preview does not use the TaskService capacity queue. Fence its
# own bounded Git subprocess work to one request per application process.
_PREVIEW_ADMISSION = threading.BoundedSemaphore(value=1)


class OcrPreviewCapacityDenied(Exception):
    """A prior trusted review preview is still deriving Git evidence."""


def _bound_git_preview(raw: bytes, base_sha: str, head_sha: str):
    if not _PREVIEW_ADMISSION.acquire(blocking=False):
        raise OcrPreviewCapacityDenied
    try:
        return bind_ocr_review_from_git(raw, ROOT, base_sha=base_sha, head_sha=head_sha)
    finally:
        _PREVIEW_ADMISSION.release()


class OcrOperatorEvidencePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-ocr-operator-preview-v1"] = "zelo-ocr-operator-preview-v1"
    report: ReviewerReport
    evidence: OcrEvidenceEnvelope
    changed_file_count: int = Field(ge=1, le=64)
    changed_line_count: int = Field(ge=1, le=768000)
    origin: Literal["trusted_core_committed_git"] = "trusted_core_committed_git"
    supplied_review_trusted: Literal[False] = False
    reviewer_worker_registered: Literal[False] = False
    review_approval_authority: Literal[False] = False
    git_write_authority: Literal[False] = False
    tool_authority: Literal[False] = False
    result_admitted_to_mind: Literal[False] = False


@router.post(
    "/evidence/{base_sha}/{head_sha}",
    response_model=OcrOperatorEvidencePreview,
)
async def preview_ocr_evidence(
    base_sha: str, head_sha: str, request: Request
) -> OcrOperatorEvidencePreview:
    """Validate every submitted finding against exact current HEAD changed lines.

    Only the operator submits this request. Source root comes from trusted Core
    configuration, never URL/body fields. The response excludes original patch
    bytes and cannot be consumed as approval or execution authorization.
    """
    if request.headers.get("content-type", "").lower().strip() != "application/json":
        raise HTTPException(status_code=415, detail="ocr_review_json_required")
    size_header = request.headers.get("content-length")
    if size_header is not None:
        try:
            reported = int(size_header)
        except ValueError:
            raise HTTPException(status_code=422, detail="ocr_review_invalid_size") from None
        if reported < 1 or reported > MAX_BYTES:
            raise HTTPException(status_code=413, detail="ocr_review_size_denied")
    content = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_BYTES - len(content):
            raise HTTPException(status_code=413, detail="ocr_review_size_denied")
        content.extend(chunk)
    if not content:
        raise HTTPException(status_code=422, detail="ocr_review_invalid_or_untrusted")
    try:
        # Bounded Git subprocesses can be slow; they must never monopolize
        # the shared ASGI event loop or block unrelated Core authority paths.
        report, evidence, scope = await run_in_threadpool(
            _bound_git_preview, bytes(content), base_sha, head_sha
        )
    except CommunityGitScopeRefusal as error:
        if str(error) == "ocr_git_review_head_stale":
            raise HTTPException(status_code=409, detail="ocr_review_head_stale") from None
        raise HTTPException(status_code=422, detail="ocr_review_git_scope_denied") from None
    except OcrEvidenceRefusal:
        raise HTTPException(status_code=422, detail="ocr_review_invalid_or_untrusted") from None
    except CommunitySourceRefusal:
        raise HTTPException(status_code=503, detail="ocr_review_source_unavailable") from None
    except OcrPreviewCapacityDenied:
        raise HTTPException(status_code=429, detail="ocr_review_capacity_exhausted") from None

    changed_line_count = sum(len(lines) for _, lines in scope.changed_lines)
    return OcrOperatorEvidencePreview(
        report=report,
        evidence=evidence,
        changed_file_count=len(scope.changed_lines),
        changed_line_count=changed_line_count,
    )
