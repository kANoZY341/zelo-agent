"""Explicit, bounded local Ollama observation of synthetic OCR case quality.

Reuses the existing trusted Zelo Ollama ModelProvider and strict offline
calibrator. No imported instructions, model-supplied Git paths, Tools, Tasks,
Skill promotion, persistent transcripts or reviewer authority.
"""

import asyncio
import hashlib
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.community_ecc_model_eval import (
    LocallyAttestedModelProvider,
    PinnedLocalOllamaProvider,
)
from app.community_review_fixed_eval import (
    OcrCalibrationResult,
    OcrCalibrationSubmission,
    OcrCandidateAnswer,
    evaluate_ocr_fixed_cases,
    ocr_fixed_suite,
)
from app.community_sources import CommunitySourceRefusal
from app.gateway import ModelGatewayError, ModelRequest, ModelResult, OllamaProvider

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_MAX_RESPONSE_BYTES = 4096
_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": ["zelo-ocr-line-review-input-v1"],
        },
        "summary": {"type": "string"},
        "comments": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "bug",
                            "security",
                            "performance",
                            "maintainability",
                            "test",
                            "style",
                            "documentation",
                            "other",
                        ],
                    },
                },
                "required": [
                    "path",
                    "content",
                    "start_line",
                    "end_line",
                    "severity",
                    "category",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["schema_version", "summary", "comments"],
    "additionalProperties": False,
}
_SYSTEM = (
    "You are evaluating four FICTIONAL software code snippets. You have NO tools, "
    "filesystem, browsing, approvals, or ability to make changes. Only the "
    "supplied evidence exists; do not infer unprovided failures or defenses. "
    "Find concrete security/logic defects only when demonstrated. A verified "
    "safe example or missing context requires an empty comments array. "
    "Use exact changed_path and changed_lines from the case. Return exactly "
    "the requested OCR JSON object, with schema_version "
    "zelo-ocr-line-review-input-v1, summary, and comments."
)


class PinnedLocalOcrProvider(PinnedLocalOllamaProvider):
    """Reuse ECC's GGUF/local attestation with a bounded OCR response budget.

    ECC's 64-token verdict budget cannot hold a full six-field OCR finding.
    Only the output-generation cap differs; loopback/metadata gates are retained.
    """

    def __init__(self):
        OllamaProvider.__init__(
            self,
            "http://127.0.0.1:11434",
            use_json_schema=True,
            structured_action_options={"num_predict": 512},
            trust_env=False,
        )


class OcrLocalModelRefusal(ValueError):
    """Model execution or pinned identity could not be independently confirmed."""


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii")
    ).hexdigest()


def _candidate(model_id: str) -> str:
    return "ollama-" + hashlib.sha256(model_id.encode("ascii")).hexdigest()[:40]


class OcrLocalCaseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    case_id: str
    raw_output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    measured_duration_ns: int = Field(ge=0, le=120_000_000_000)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class OcrLocalModelProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["zelo-ocr-local-model-probe-v1"] = "zelo-ocr-local-model-probe-v1"
    model_id: str
    model_digest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: Literal["ollama"] = "ollama"
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    suite_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_observations: tuple[OcrLocalCaseObservation, ...] = Field(min_length=4, max_length=4)
    evaluation: OcrCalibrationResult
    local_model_responses_observed: Literal[True] = True
    actual_agent_task_qualified: Literal[False] = False
    real_git_diff_reviewed: Literal[False] = False
    independent_semantic_accuracy_proven: Literal[False] = False
    tool_or_space_grants: tuple[()] = ()
    reviewer_activation_authority: Literal[False] = False
    result_admitted_to_mind: Literal[False] = False
    probe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_seal(self):
        if self.probe_sha256 != _sha(self.model_dump(mode="json", exclude={"probe_sha256"})):
            raise ValueError("ocr_local_probe_digest_invalid")
        if (
            _candidate(self.model_id) != self.evaluation.candidate_label
            or self.source_commit != self.evaluation.source_commit
            or self.suite_sha256 != self.evaluation.suite_sha256
            or tuple(x.case_id for x in self.case_observations)
            != tuple(x.case_id for x in self.evaluation.cases)
        ):
            raise ValueError("ocr_local_probe_identity_invalid")
        return self


async def probe_local_ocr_model(
    provider: LocallyAttestedModelProvider,
    *,
    model_id: str,
    timeout_per_case: float = 30.0,
) -> OcrLocalModelProbe:
    """Four local-model generations; returned scores have zero Core authority."""

    if (
        type(model_id) is not str
        or _NAME.fullmatch(model_id) is None
        or "cloud" in model_id.casefold()
        or "//" in model_id
        or type(timeout_per_case) not in (float, int)
        or not 2 <= timeout_per_case <= 45
    ):
        raise OcrLocalModelRefusal("ocr_local_model_request_invalid")
    try:
        suite = ocr_fixed_suite()
        names = await asyncio.wait_for(provider.models(), timeout=5)
    except (
        CommunitySourceRefusal,
        ModelGatewayError,
        TimeoutError,
        OSError,
        ValueError,
        TypeError,
    ):
        raise OcrLocalModelRefusal("ocr_local_model_preflight_failed") from None
    if (
        type(names) is not list
        or len(names) > 256
        or any(type(name) is not str or len(name) > 128 for name in names)
        or model_id not in names
    ):
        raise OcrLocalModelRefusal("ocr_local_model_not_installed")
    try:
        digest = await asyncio.wait_for(provider.attest_local_model(model_id), timeout=5)
    except (ModelGatewayError, AttributeError, TimeoutError, OSError, ValueError, TypeError):
        raise OcrLocalModelRefusal("ocr_local_model_identity_invalid") from None
    if type(digest) is not str or _HASH.fullmatch(digest) is None:
        raise OcrLocalModelRefusal("ocr_local_model_identity_invalid")

    observed: list[OcrLocalCaseObservation] = []
    answers: list[OcrCandidateAnswer] = []
    for case in suite.cases:
        prompt = (
            f"Fictional case: {case.case_id}\n"
            f"Evidence: {case.evidence}\n"
            f"Exact changed_path: {case.changed_path}\n"
            f"Only changed_lines: {','.join(str(x) for x in case.changed_lines)}\n"
            "Return no findings if the evidence is safe or incomplete."
        )
        request = ModelRequest(
            model_id=model_id,
            system_prompt=_SYSTEM,
            user_messages=[prompt],
            timeout_seconds=timeout_per_case,
            output_schema=_SCHEMA,
        )
        start = time.monotonic_ns()
        try:
            response = await asyncio.wait_for(provider.generate(request), timeout=timeout_per_case)
        except (ModelGatewayError, TimeoutError, OSError, ValueError, TypeError):
            raise OcrLocalModelRefusal("ocr_local_model_generation_failed") from None
        elapsed = time.monotonic_ns() - start
        if type(response) is not ModelResult:
            raise OcrLocalModelRefusal("ocr_local_model_output_invalid")
        if response.provider != "ollama" or response.model_id != model_id:
            raise OcrLocalModelRefusal("ocr_local_model_identity_changed")
        try:
            if type(response.text) is not str:
                raise OcrLocalModelRefusal("ocr_local_model_output_invalid")
            raw = response.text.encode("utf-8", "strict")
            if not 1 <= len(raw) <= _MAX_RESPONSE_BYTES:
                raise OcrLocalModelRefusal("ocr_local_model_output_invalid")
            observation = OcrLocalCaseObservation(
                case_id=case.case_id,
                raw_output_sha256=hashlib.sha256(raw).hexdigest(),
                measured_duration_ns=elapsed,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            answer = OcrCandidateAnswer(case_id=case.case_id, raw_review_json=response.text)
        except (UnicodeError, ValueError, TypeError):
            raise OcrLocalModelRefusal("ocr_local_model_output_invalid") from None
        observed.append(observation)
        answers.append(answer)

    try:
        verified_after = await asyncio.wait_for(provider.attest_local_model(model_id), timeout=5)
        suite_after = ocr_fixed_suite()
    except (
        CommunitySourceRefusal,
        ModelGatewayError,
        TimeoutError,
        OSError,
        ValueError,
        TypeError,
    ):
        raise OcrLocalModelRefusal("ocr_local_model_identity_changed") from None
    if verified_after != digest or suite_after != suite:
        raise OcrLocalModelRefusal("ocr_local_model_identity_changed")

    submission = OcrCalibrationSubmission(
        candidate_label=_candidate(model_id),
        expected_suite_sha256=suite.suite_sha256,
        answers=tuple(answers),
    )
    try:
        evaluation = evaluate_ocr_fixed_cases(submission)
    except (CommunitySourceRefusal, ValueError):
        raise OcrLocalModelRefusal("ocr_local_model_score_unavailable") from None
    material = {
        "schema_version": "zelo-ocr-local-model-probe-v1",
        "model_id": model_id,
        "model_digest_sha256": digest,
        "provider": "ollama",
        "source_commit": suite.source_commit,
        "suite_sha256": suite.suite_sha256,
        "case_observations": tuple(x.model_dump(mode="json") for x in observed),
        "evaluation": evaluation.model_dump(mode="json"),
        "local_model_responses_observed": True,
        "actual_agent_task_qualified": False,
        "real_git_diff_reviewed": False,
        "independent_semantic_accuracy_proven": False,
        "tool_or_space_grants": (),
        "reviewer_activation_authority": False,
        "result_admitted_to_mind": False,
    }
    sealed = _sha(material)
    material["evaluation"] = evaluation
    return OcrLocalModelProbe(**material, probe_sha256=sealed)
