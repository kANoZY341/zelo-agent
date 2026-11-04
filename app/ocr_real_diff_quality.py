"""Offline OCR quality receipts for exact historical Zelo Git diffs.

Git and JSON binding prove source/anchor validity, never semantic correctness.
Comment labels here are explicit, hash-bound human claims and do not prove an
independent reviewer or grant any runtime, publication or merge authority.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.community_review import OcrEvidenceRefusal, bind_ocr_review
from app.community_review_git_scope import verified_git_review_scope


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


class OcrRealDiffRefusal(ValueError):
    """The submitted case or semantic adjudication cannot be trusted."""


class OcrRealDiffCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]{5,63}$")
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    known_defect_path: str | None = None
    known_defect_span: tuple[int, int] | None = None
    historical_case: bool

    @model_validator(mode="after")
    def check_target(self):
        if (self.known_defect_path is None) != (self.known_defect_span is None):
            raise ValueError("ocr_real_diff_target_incomplete")
        if self.known_defect_span is not None and not (
            1 <= self.known_defect_span[0] <= self.known_defect_span[1] <= 1_000_000
        ):
            raise ValueError("ocr_real_diff_target_span_invalid")
        return self


# These identities are historical data, not a model-supplied grading key.
# The migration's destructive downgrade was corrected after PR #242.
_CASES = {
    "migration_0049_data_loss": OcrRealDiffCase(
        case_id="migration_0049_data_loss",
        base_sha="d3ebcdadc2c9631eecbc9691158ee2f01e5aa711",
        head_sha="1b6d47be81cbe67d315192f90403ef67f29ae188",
        known_defect_path="migrations/versions/0049_skill_publication_promotion_receipt_link.py",
        known_defect_span=(165, 175),
        historical_case=True,
    ),
    "browser_297_clean": OcrRealDiffCase(
        case_id="browser_297_clean",
        base_sha="a417272318e7c1ba62fda337819a83b6f1ca8451",
        head_sha="02908456765063232dc9cac528aab92bc6c155fb",
        historical_case=True,
    ),
}


class OcrCommentAdjudication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    comment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["known_defect", "supported_other", "unsupported"]
    rationale: str = Field(min_length=20, max_length=2000)


class OcrRealDiffResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-ocr-real-diff-quality-v1"] = "zelo-ocr-real-diff-quality-v1"
    case_id: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    known_defect_path: str | None
    known_defect_span: tuple[int, int] | None
    diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_evidence_sha256: str | None = None
    adjudications_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment_sha256: tuple[str, ...]
    adapter_status: Literal["accepted", "invalid"]
    target_status: Literal[
        "not_applicable",
        "missed",
        "needs_adjudication",
        "detected_with_manual_adjudication",
        "invalid_review",
    ]
    clean_negative: bool
    supported_other_count: int = Field(ge=0, le=64)
    unsupported_count: int = Field(ge=0, le=64)
    unadjudicated_count: int = Field(ge=0, le=64)
    historical_case: bool
    independent_semantic_quality_proven: Literal[False] = False
    live_model_invoked: Literal[False] = False
    runtime_authority_granted: Literal[False] = False
    approval_or_merge_authority: Literal[False] = False
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_receipt(self):
        if self.receipt_sha256 != _digest(self.model_dump(mode="json", exclude={"receipt_sha256"})):
            raise ValueError("ocr_real_diff_receipt_digest_invalid")
        if self.adapter_status == "invalid" and (
            self.comment_sha256
            or self.review_evidence_sha256 is not None
            or self.target_status != "invalid_review"
            or self.clean_negative
        ):
            raise ValueError("ocr_real_diff_invalid_result_inconsistent")
        return self


def pinned_ocr_real_diff_cases() -> tuple[OcrRealDiffCase, ...]:
    """Expose exact historical identity pins without mutable checkout data."""

    return tuple(_CASES.values())


def _evaluate_case(
    raw_review: bytes,
    repo: Path,
    *,
    case: OcrRealDiffCase,
    adjudications: tuple[OcrCommentAdjudication, ...],
) -> OcrRealDiffResult:
    """Score one verified Git diff; used with fixture pins by contract tests."""

    if (
        type(raw_review) is not bytes
        or type(adjudications) is not tuple
        or any(type(item) is not OcrCommentAdjudication for item in adjudications)
    ):
        raise OcrRealDiffRefusal("ocr_real_diff_input_invalid")
    scope = verified_git_review_scope(repo, base_sha=case.base_sha, head_sha=case.head_sha)
    known_lines = scope.allowed_lines().get(case.known_defect_path, frozenset())
    if case.known_defect_span is not None and not any(
        case.known_defect_span[0] <= line <= case.known_defect_span[1] for line in known_lines
    ):
        raise OcrRealDiffRefusal("ocr_real_diff_known_defect_not_in_diff")
    raw_hash = hashlib.sha256(raw_review).hexdigest()
    try:
        _, evidence = bind_ocr_review(
            raw_review,
            approved_changed_lines=scope.allowed_lines(),
            base_sha=case.base_sha,
            head_sha=case.head_sha,
            diff_sha256=scope.diff_sha256,
        )
    except OcrEvidenceRefusal:
        if adjudications:
            raise OcrRealDiffRefusal("ocr_adjudication_on_invalid_review") from None
        comments = ()
        evidence_hash = None
        status: Literal["accepted", "invalid"] = "invalid"
    else:
        comments = evidence.comments
        evidence_hash = evidence.review_evidence_sha256
        status = "accepted"

    hashes = tuple(_digest(item.model_dump(mode="json")) for item in comments)
    labels = {item.comment_sha256: item for item in adjudications}
    if len(labels) != len(adjudications):
        raise OcrRealDiffRefusal("ocr_adjudication_duplicate_comment")
    if set(labels) - set(hashes):
        raise OcrRealDiffRefusal("ocr_adjudication_comment_unknown")
    known_count = 0
    for comment, comment_hash in zip(comments, hashes, strict=True):
        label = labels.get(comment_hash)
        if label is None or label.verdict != "known_defect":
            continue
        if (
            case.known_defect_path is None
            or comment.path != case.known_defect_path
            or comment.end_line < case.known_defect_span[0]
            or comment.start_line > case.known_defect_span[1]
        ):
            raise OcrRealDiffRefusal("ocr_adjudication_target_anchor_invalid")
        known_count += 1
    if known_count > 1:
        raise OcrRealDiffRefusal("ocr_adjudication_target_duplicated")

    if status == "invalid":
        target_status = "invalid_review"
    elif case.known_defect_path is None:
        target_status = "not_applicable"
    elif known_count:
        target_status = "detected_with_manual_adjudication"
    elif len(labels) < len(comments):
        target_status = "needs_adjudication"
    else:
        target_status = "missed"

    values = {
        "case_id": case.case_id,
        "base_sha": case.base_sha,
        "head_sha": case.head_sha,
        "known_defect_path": case.known_defect_path,
        "known_defect_span": case.known_defect_span,
        "diff_sha256": scope.diff_sha256,
        "raw_review_sha256": raw_hash,
        "review_evidence_sha256": evidence_hash,
        "adjudications_sha256": _digest([item.model_dump(mode="json") for item in adjudications]),
        "comment_sha256": hashes,
        "adapter_status": status,
        "target_status": target_status,
        "clean_negative": status == "accepted" and case.known_defect_path is None and not comments,
        "supported_other_count": sum(item.verdict == "supported_other" for item in adjudications),
        "unsupported_count": sum(item.verdict == "unsupported" for item in adjudications),
        "unadjudicated_count": len(comments) - len(adjudications),
        "historical_case": case.historical_case,
        "independent_semantic_quality_proven": False,
        "live_model_invoked": False,
        "runtime_authority_granted": False,
        "approval_or_merge_authority": False,
    }
    return OcrRealDiffResult(
        **values,
        receipt_sha256=_digest({"schema_version": "zelo-ocr-real-diff-quality-v1", **values}),
    )


def evaluate_pinned_ocr_case(
    raw_review: bytes,
    repo: Path,
    *,
    case_id: str,
    adjudications: tuple[OcrCommentAdjudication, ...] = (),
) -> OcrRealDiffResult:
    case = _CASES.get(case_id)
    if case is None:
        raise OcrRealDiffRefusal("ocr_real_diff_case_unknown")
    return _evaluate_case(raw_review, repo, case=case, adjudications=adjudications)
