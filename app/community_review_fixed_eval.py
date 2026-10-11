"""Offline fixed-case calibration for Open Code Review evidence, never review authority.

All labels are Zelo-authored synthetic cases. Candidate review text is
untrusted and passed through the existing pinned-source/line-binding adapter.
No Git checkout, agent, model, Worker, Task or external reviewer is invoked.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.community_review import SOURCE, OcrEvidenceRefusal, bind_ocr_review
from app.community_sources import CommunitySourceRefusal, verify_community_sources

CASE_SCHEMA = "zelo-ocr-fixed-calibration-v1"
MAX_CASE_REVIEW_CHARS = 4096


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _Fixture:
    case_id: str
    evidence: str
    path: str
    changed_lines: tuple[int, ...]
    expected: tuple[tuple[str, int, int, str, str], ...]


# Fixed synthetic regression situations. Expected labels are engineering
# hypotheses for these exact snippets, NOT independently validated real bugs.
_FIXED = (
    _Fixture(
        "unsafe_path_boundary",
        "src/handlers.py:5 (new): return read_file(request.query['path'])\n"
        "Context: this handler has no authentication, authorization, or path containment.",
        "src/handlers.py",
        (5,),
        (("src/handlers.py", 5, 5, "security", "high"),),
    ),
    _Fixture(
        "pagination_extra_item",
        "src/pages.py:4 (new): return items[:limit + 1]\n"
        "Context: the API contract requires returning at most `limit` items.",
        "src/pages.py",
        (4,),
        (("src/pages.py", 4, 4, "bug", "medium"),),
    ),
    _Fixture(
        "safe_constant_lookup",
        "src/handlers.py:5 (new): return read_file(ALLOWED_PUBLIC_FILES[key])\n"
        "Context: caller authentication, allowed-key validation and fixed public roots "
        "are independently verified; no arbitrary path can enter the lookup.",
        "src/handlers.py",
        (5,),
        (),
    ),
    _Fixture(
        "unknown_middleware_scope",
        "src/handlers.py:5 (new): return store.get(key)\n"
        "Context: the upstream authentication/middleware and input contract were not "
        "provided. Do not assert a definite vulnerability from this line alone.",
        "src/handlers.py",
        (5,),
        (),
    ),
)


class OcrFixedCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]{5,63}$")
    evidence: str = Field(min_length=50, max_length=512)
    changed_path: str = Field(min_length=1, max_length=240)
    changed_lines: tuple[int, ...] = Field(min_length=1, max_length=20)
    hidden_expected_findings: Literal[True] = True


class OcrFixedSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-ocr-fixed-calibration-v1"] = CASE_SCHEMA
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[OcrFixedCase, ...] = Field(min_length=4, max_length=4)
    synthetic_truth_only: Literal[True] = True
    genuine_git_revision_proof: Literal[False] = False
    actual_model_execution_observed: Literal[False] = False
    reviewer_worker_registered: Literal[False] = False
    authority_granted: Literal[False] = False


class OcrCandidateAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]{5,63}$")
    raw_review_json: str = Field(min_length=1, max_length=MAX_CASE_REVIEW_CHARS)


class OcrCalibrationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidate_label: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$"
    )
    expected_suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answers: tuple[OcrCandidateAnswer, ...] = Field(min_length=4, max_length=4)


class OcrCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str
    adapter_status: Literal["accepted", "invalid"]
    expected_findings: int = Field(ge=0, le=8)
    observed_findings: int = Field(ge=0, le=64)
    matched_findings: int = Field(ge=0, le=8)
    unexpected_findings: int = Field(ge=0, le=64)
    missed_findings: int = Field(ge=0, le=8)
    clean_negative: bool

    @model_validator(mode="after")
    def validate_counts(self):
        if (
            self.matched_findings + self.missed_findings != self.expected_findings
            or self.matched_findings + self.unexpected_findings != self.observed_findings
            or (self.adapter_status == "invalid" and self.observed_findings != 0)
            or self.clean_negative
            != (
                self.adapter_status == "accepted"
                and self.expected_findings == 0
                and self.observed_findings == 0
            )
        ):
            raise ValueError("ocr_calibration_case_counts_invalid")
        return self


class OcrCalibrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-ocr-calibration-result-v1"] = "zelo-ocr-calibration-result-v1"
    candidate_label: str
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    submitted_answers_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[OcrCaseResult, ...] = Field(min_length=4, max_length=4)
    matched_finding_count: int = Field(ge=0, le=32)
    unexpected_finding_count: int = Field(ge=0, le=256)
    missed_finding_count: int = Field(ge=0, le=32)
    invalid_review_count: int = Field(ge=0, le=4)
    clean_negative_count: int = Field(ge=0, le=4)
    fixture_only: Literal[True] = True
    genuine_git_revision_proof: Literal[False] = False
    independent_semantic_quality_proven: Literal[False] = False
    live_model_invoked: Literal[False] = False
    reviewer_worker_registered: Literal[False] = False
    approval_or_merge_authority: Literal[False] = False
    tool_or_space_grants: tuple[()] = ()
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_receipt(self):
        if self.receipt_sha256 != _sha(self.model_dump(mode="json", exclude={"receipt_sha256"})):
            raise ValueError("ocr_calibration_receipt_digest_invalid")
        if (
            self.matched_finding_count != sum(x.matched_findings for x in self.cases)
            or self.unexpected_finding_count != sum(x.unexpected_findings for x in self.cases)
            or self.missed_finding_count != sum(x.missed_findings for x in self.cases)
            or self.invalid_review_count != sum(x.adapter_status == "invalid" for x in self.cases)
            or self.clean_negative_count != sum(x.clean_negative for x in self.cases)
        ):
            raise ValueError("ocr_calibration_result_counts_invalid")
        return self


class OcrCalibrationRefusal(ValueError):
    """A caller attempted an unknown or changed synthetic fixture submission."""


def _material() -> dict:
    return {
        "schema_version": CASE_SCHEMA,
        "source_commit": SOURCE,
        "cases": [
            {
                "id": c.case_id,
                "evidence": c.evidence,
                "changed_path": c.path,
                "changed_lines": c.changed_lines,
                "expected": c.expected,
            }
            for c in _FIXED
        ],
    }


def ocr_fixed_suite() -> OcrFixedSuite:
    sources = verify_community_sources()
    if not any(
        item.name == "open_code_review"
        and item.repository == "alibaba/open-code-review"
        and item.commit == SOURCE
        for item in sources
    ):
        raise CommunitySourceRefusal("ocr_fixed_source_identity_invalid")
    return OcrFixedSuite(
        source_commit=SOURCE,
        suite_sha256=_sha(_material()),
        cases=tuple(
            OcrFixedCase(
                case_id=case.case_id,
                evidence=case.evidence,
                changed_path=case.path,
                changed_lines=case.changed_lines,
            )
            for case in _FIXED
        ),
    )


def _synthetic_identity(case: _Fixture) -> tuple[str, str, str]:
    """These are *fixture labels*, never Git commit or diff verification."""

    base = hashlib.sha256(("synthetic-base:" + case.case_id).encode("ascii")).hexdigest()[:40]
    head = hashlib.sha256(("synthetic-head:" + case.case_id).encode("ascii")).hexdigest()[:40]
    diff = hashlib.sha256(case.evidence.encode("utf-8")).hexdigest()
    return base, head, diff


def evaluate_ocr_fixed_cases(submission: OcrCalibrationSubmission) -> OcrCalibrationResult:
    suite = ocr_fixed_suite()
    if submission.expected_suite_sha256 != suite.suite_sha256:
        raise OcrCalibrationRefusal("ocr_calibration_suite_stale")
    if tuple(x.case_id for x in submission.answers) != tuple(x.case_id for x in _FIXED):
        raise OcrCalibrationRefusal("ocr_calibration_case_ids_invalid")
    results = []
    for fixed, answer in zip(_FIXED, submission.answers, strict=True):
        base, head, diff = _synthetic_identity(fixed)
        try:
            _, evidence = bind_ocr_review(
                answer.raw_review_json.encode("utf-8"),
                approved_changed_lines={fixed.path: frozenset(fixed.changed_lines)},
                base_sha=base,
                head_sha=head,
                diff_sha256=diff,
            )
            observed = {
                (x.path, x.start_line, x.end_line, x.category, x.severity)
                for x in evidence.comments
            }
            # Duplicate comments with different text are still separately
            # counted as unsupported extra findings, not silently deduped.
            actual_count = len(evidence.comments)
            match_count = sum(key in fixed.expected for key in observed)
            status: Literal["accepted", "invalid"] = "accepted"
            unexpected_count = actual_count - match_count
        except (OcrEvidenceRefusal, UnicodeError):
            status = "invalid"
            actual_count = match_count = unexpected_count = 0
        results.append(
            OcrCaseResult(
                case_id=fixed.case_id,
                adapter_status=status,
                expected_findings=len(fixed.expected),
                observed_findings=actual_count,
                matched_findings=match_count,
                unexpected_findings=unexpected_count,
                missed_findings=len(fixed.expected) - match_count,
                clean_negative=(status == "accepted" and not fixed.expected and actual_count == 0),
            )
        )
    payload = {
        "schema_version": "zelo-ocr-calibration-result-v1",
        "candidate_label": submission.candidate_label,
        "source_commit": suite.source_commit,
        "suite_sha256": suite.suite_sha256,
        "submitted_answers_sha256": _sha(submission.model_dump(mode="json")),
        "cases": tuple(x.model_dump(mode="json") for x in results),
        "matched_finding_count": sum(x.matched_findings for x in results),
        "unexpected_finding_count": sum(x.unexpected_findings for x in results),
        "missed_finding_count": sum(x.missed_findings for x in results),
        "invalid_review_count": sum(x.adapter_status == "invalid" for x in results),
        "clean_negative_count": sum(x.clean_negative for x in results),
        "fixture_only": True,
        "genuine_git_revision_proof": False,
        "independent_semantic_quality_proven": False,
        "live_model_invoked": False,
        "reviewer_worker_registered": False,
        "approval_or_merge_authority": False,
        "tool_or_space_grants": (),
    }
    return OcrCalibrationResult(**payload, receipt_sha256=_sha(payload))
