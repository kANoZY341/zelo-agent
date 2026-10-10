"""Open Code Review -> Zelo ReviewerReport adapter; evidence only, not approval.

Adapt Alibaba's Apache-2.0 LlmComment category/severity/line contract.
The imported Go source is pinned reference DATA, never executed.
No OCR CLI, external model, network, auto-fix, GitHub comments or merges.
"""

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.community_sources import CommunitySourceRefusal, verify_community_sources
from app.developer_contracts import ReviewerReport, ReviewFinding

MAX_BYTES = 131072
SOURCE = "fabbdb296b0d97e2140ada8d54ca7d6d6d1d7ad4"
SEVERITY = ("critical", "high", "medium", "low")
CATEGORY = (
    "bug",
    "security",
    "performance",
    "maintainability",
    "test",
    "style",
    "documentation",
    "other",
)
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OcrEvidenceRefusal(ValueError):
    """Raw model or upstream reviewer data did not match trusted diff anchors."""


class OcrReviewComment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=1980)
    start_line: int = Field(ge=1, le=1_000_000)
    end_line: int = Field(ge=1, le=1_000_000)
    severity: Literal["critical", "high", "medium", "low"]
    category: Literal[
        "bug",
        "security",
        "performance",
        "maintainability",
        "test",
        "style",
        "documentation",
        "other",
    ]

    @model_validator(mode="after")
    def valid_anchor(self):
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or path.as_posix() != self.path
            or "\\" in self.path
            or any(part in {"", ".", "..", ".git"} for part in self.path.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in self.path)
        ):
            raise ValueError("ocr_unsafe_path")
        if self.end_line < self.start_line or self.end_line - self.start_line > 19:
            raise ValueError("ocr_line_span_invalid")
        if any(ord(char) == 0 or (ord(char) < 32 and char not in "\n\t") for char in self.content):
            raise ValueError("ocr_content_control_character")
        return self


class OcrEvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-ocr-line-review-evidence-v1"] = "zelo-ocr-line-review-evidence-v1"
    base_sha: Sha
    head_sha: Sha
    diff_sha256: Sha256
    reviewer_source_commit: Literal["fabbdb296b0d97e2140ada8d54ca7d6d6d1d7ad4"] = SOURCE
    raw_review_sha256: Sha256
    comments: tuple[OcrReviewComment, ...] = Field(max_length=64)
    source_only: Literal[True] = True
    git_write_authority: Literal[False] = False
    approval_authority: Literal[False] = False
    comment_publication_authority: Literal[False] = False
    review_evidence_sha256: Sha256

    @model_validator(mode="after")
    def validate_fingerprint(self):
        material = self.model_dump(mode="json", exclude={"review_evidence_sha256"})
        if self.review_evidence_sha256 != _digest(material):
            raise ValueError("ocr_evidence_digest_invalid")
        return self


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


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise OcrEvidenceRefusal("ocr_duplicate_json_key")
        result[name] = value
    return result


def bind_ocr_review(
    raw_review: bytes,
    *,
    approved_changed_lines: Mapping[str, frozenset[int]],
    base_sha: str,
    head_sha: str,
    diff_sha256: str,
) -> tuple[ReviewerReport, OcrEvidenceEnvelope]:
    """Normalize external findings only when *trusted Core* supplies exact diff lines.

    Model-supplied file/line anchors NEVER authorize their own inclusion.
    ReviewerReport is a review input, not an approval or CI success assertion.
    """

    if type(raw_review) is not bytes or not 1 <= len(raw_review) <= MAX_BYTES:
        raise OcrEvidenceRefusal("ocr_report_size_invalid")
    try:
        payload = json.loads(raw_review.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
        if type(payload) is not dict or set(payload) != {"schema_version", "summary", "comments"}:
            raise ValueError("ocr_report_shape_invalid")
        if payload["schema_version"] != "zelo-ocr-line-review-input-v1":
            raise ValueError("ocr_report_schema_invalid")
        if (
            type(payload["summary"]) is not str
            or not 1 <= len(payload["summary"]) <= 4096
            or type(payload["comments"]) is not list
            or len(payload["comments"]) > 64
        ):
            raise ValueError("ocr_report_bounds_invalid")
        if any(
            ord(char) == 0 or (ord(char) < 32 and char not in "\n\t") for char in payload["summary"]
        ):
            raise ValueError("ocr_report_control_character")
        items = tuple(
            OcrReviewComment.model_validate(item, strict=True) for item in payload["comments"]
        )
        if (
            type(base_sha) is not str
            or len(base_sha) != 40
            or any(c not in "0123456789abcdef" for c in base_sha)
            or type(head_sha) is not str
            or len(head_sha) != 40
            or any(c not in "0123456789abcdef" for c in head_sha)
            or type(diff_sha256) is not str
            or len(diff_sha256) != 64
            or any(c not in "0123456789abcdef" for c in diff_sha256)
        ):
            raise ValueError("ocr_repository_identity_invalid")
        seen = set()
        for comment in items:
            allowed = approved_changed_lines.get(comment.path)
            if (
                not isinstance(allowed, frozenset)
                or any(type(line) is not int or line < 1 for line in allowed)
                or not set(range(comment.start_line, comment.end_line + 1)).issubset(allowed)
            ):
                raise ValueError("ocr_finding_not_on_reviewed_diff")
            identity = (comment.path, comment.start_line, comment.end_line, comment.content)
            if identity in seen:
                raise ValueError("ocr_duplicate_finding")
            seen.add(identity)
        if items != tuple(
            sorted(items, key=lambda x: (x.path, x.start_line, x.end_line, x.content))
        ):
            raise ValueError("ocr_findings_not_canonical")
        verify_community_sources()
        evidence_values = {
            "schema_version": "zelo-ocr-line-review-evidence-v1",
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff_sha256": diff_sha256,
            "reviewer_source_commit": SOURCE,
            "raw_review_sha256": hashlib.sha256(raw_review).hexdigest(),
            "comments": tuple(item.model_dump(mode="json") for item in items),
            "source_only": True,
            "git_write_authority": False,
            "approval_authority": False,
            "comment_publication_authority": False,
        }
        evidence = OcrEvidenceEnvelope(
            **evidence_values, review_evidence_sha256=_digest(evidence_values)
        )
        report = ReviewerReport(
            summary=payload["summary"],
            findings=tuple(
                ReviewFinding(
                    severity=item.severity,
                    path=item.path,
                    message=(
                        f"[{item.category}, lines {item.start_line}-{item.end_line}] {item.content}"
                    ),
                )
                for item in items
            ),
        )
        return report, evidence
    except CommunitySourceRefusal:
        # A trusted reference-source-integrity failure is availability, not
        # evidence that the supplied review comment schema was invalid.
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise OcrEvidenceRefusal("ocr_report_invalid_or_untrusted") from None
