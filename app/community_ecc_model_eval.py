"""Opt-in, no-Tool local Ollama ECC fixed-case probe.

The Zelo ModelProvider is the only model interface. Model responses are untrusted
JSON evidence, scored against synthetic Core-owned fixtures but never admitted
to Mind, Skill Registry, Task, approvals or worker/tool authority.
"""

import asyncio
import hashlib
import json
import re
import time
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.community_ecc_eval import (
    EccCaseAnswer,
    EccCaseSubmission,
    EccFixedEvaluation,
    EccFixtureRefusal,
    EccSkillId,
    ecc_fixed_suite,
    evaluate_ecc_fixed_cases,
)
from app.community_ecc_playbooks import EccPlaybookRefusal, ecc_manual_playbook
from app.community_sources import CommunitySourceRefusal
from app.gateway import ModelGatewayError, ModelProvider, ModelRequest, OllamaProvider

_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_LOOPBACK_OLLAMA = "http://127.0.0.1:11434"
_MIN_LOCAL_GGUF_BYTES = 128 * 1024 * 1024
_MAX_OUTPUT_BYTES = 512
_EXPECTED_FORMAT = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["pass", "fail", "unverified"]}},
    "required": ["verdict"],
    "additionalProperties": False,
}
_SYSTEM = (
    "You evaluate a fictional software evidence case. You are NOT a Zelo agent "
    "and have no tools, filesystem, approvals or action rights. Only the evidence "
    "provided in the current case exists; never infer missing defenses or tests. "
    "Return exactly one JSON object with the single key verdict: pass, fail, "
    "or unverified. 'pass' needs sufficient positive authorization/evidence; "
    "'fail' requires a demonstrated unsafe violation; 'unverified' means crucial "
    "evidence is missing. Do not include explanations or extra keys."
)


class EccLocalModelRefusal(ValueError):
    """Non-authoritative probe result is unavailable or identity-invalid."""


def _local_gguf_digest(models: object, model_id: str) -> str:
    """Admit only large locally materialized GGUF metadata, never cloud aliases.

    This is a trusted local Ollama *metadata check*, not an operating-system
    network isolation proof. Operator must control the local model daemon.
    """
    if type(models) is not list or len(models) > 256 or "cloud" in model_id.casefold():
        raise EccLocalModelRefusal("ecc_local_model_not_locally_qualified")
    matching = [item for item in models if type(item) is dict and item.get("name") == model_id]
    if len(matching) != 1:
        raise EccLocalModelRefusal("ecc_local_model_not_locally_qualified")
    candidate = matching[0]
    details = candidate.get("details")
    size = candidate.get("size")
    digest = candidate.get("digest")
    if (
        type(size) is not int
        or size < _MIN_LOCAL_GGUF_BYTES
        or type(digest) is not str
        or not _SHA256.fullmatch(digest)
        or type(details) is not dict
        or details.get("format") != "gguf"
        or not isinstance(details.get("family"), str)
        or not details["family"]
    ):
        raise EccLocalModelRefusal("ecc_local_model_not_locally_qualified")
    return digest


class LocallyAttestedModelProvider(ModelProvider, Protocol):
    async def attest_local_model(self, model_id: str) -> str: ...


class PinnedLocalOllamaProvider(OllamaProvider):
    """Loopback-only Ollama wrapper that verifies large local GGUF metadata."""

    def __init__(self):
        super().__init__(
            _LOOPBACK_OLLAMA,
            use_json_schema=True,
            structured_action_options={"num_predict": 64},
            trust_env=False,
        )

    async def attest_local_model(self, model_id: str) -> str:
        try:
            async with httpx.AsyncClient(
                base_url=_LOOPBACK_OLLAMA, trust_env=False, timeout=5
            ) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
                contents = response.json()
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise EccLocalModelRefusal("ecc_local_model_unavailable") from None
        if type(contents) is not dict:
            raise EccLocalModelRefusal("ecc_local_model_unavailable")
        return _local_gguf_digest(contents.get("models"), model_id)


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _candidate_label(model_id: str) -> str:
    """Bind Ollama model identity to the scorer's harmless bounded label."""
    return "ollama-" + hashlib.sha256(model_id.encode("ascii")).hexdigest()[:40]


def _unique_pairs(pairs):
    result = {}
    for key, val in pairs:
        if key in result:
            raise EccLocalModelRefusal("ecc_local_model_response_invalid")
        result[key] = val
    return result


class _ModelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    verdict: Literal["pass", "fail", "unverified"]


class EccModelCaseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    case_id: str
    answer: Literal["pass", "fail", "unverified"]
    raw_output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    measured_duration_ns: int = Field(ge=0, le=120_000_000_000)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class EccLocalModelProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["zelo-ecc-local-model-probe-v1"] = "zelo-ecc-local-model-probe-v1"
    model_id: str
    model_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["ollama"] = "ollama"
    skill_id: EccSkillId
    source_commit: str
    suite_sha256: str
    playbook_sha256: str
    case_observations: tuple[EccModelCaseObservation, ...] = Field(min_length=3, max_length=3)
    evaluation: EccFixedEvaluation
    local_model_responses_observed: Literal[True] = True
    actual_agent_task_or_permission_evaluated: Literal[False] = False
    imported_ecc_source_injected: Literal[False] = False
    runtime_authority: Literal[False] = False
    skill_promotion_authority: Literal[False] = False
    tool_grants: tuple[()] = ()
    result_admitted_to_mind: Literal[False] = False
    probe_sha256: str

    @model_validator(mode="after")
    def _check_digest(self):
        material = self.model_dump(mode="json", exclude={"probe_sha256"})
        if self.probe_sha256 != _sha(material):
            raise ValueError("ecc_local_model_probe_digest_invalid")
        if (
            self.skill_id != self.evaluation.skill_id
            or _candidate_label(self.model_id) != self.evaluation.candidate_label
            or self.suite_sha256 != self.evaluation.suite_sha256
            or self.playbook_sha256 != self.evaluation.playbook_sha256
            or tuple(x.case_id for x in self.case_observations)
            != tuple(x.case_id for x in self.evaluation.cases)
            or tuple(x.answer for x in self.case_observations)
            != tuple(x.submitted for x in self.evaluation.cases)
        ):
            raise ValueError("ecc_local_model_probe_identity_mismatch")
        return self


async def probe_local_ecc_model(
    provider: LocallyAttestedModelProvider,
    *,
    skill_id: EccSkillId,
    model_id: str,
    timeout_per_case: float = 20.0,
) -> EccLocalModelProbe:
    """Run exactly 3 reviewed synthetic cases; produce evidence only or refuse.

    The caller must construct a strictly loopback-only model provider. A
    complete and exact model/fixture identity is required before any score.
    """
    if (
        type(model_id) is not str
        or len(model_id) > 64
        or not _MODEL_ID.fullmatch(model_id)
        or "//" in model_id
        or type(timeout_per_case) not in (int, float)
        or not 2 <= timeout_per_case <= 45
    ):
        raise EccLocalModelRefusal("ecc_local_model_request_invalid")

    try:
        suite = ecc_fixed_suite(skill_id)
        playbook = ecc_manual_playbook(skill_id)
    except (EccPlaybookRefusal, EccFixtureRefusal, CommunitySourceRefusal):
        raise EccLocalModelRefusal("ecc_local_model_source_unavailable") from None
    if (
        suite.source_commit != playbook.source_commit
        or suite.playbook_sha256 != playbook.playbook_sha256
    ):
        raise EccLocalModelRefusal("ecc_local_model_source_changed")
    try:
        installed = await asyncio.wait_for(provider.models(), timeout=5.0)
    except (ModelGatewayError, TimeoutError, OSError, ValueError, TypeError):
        raise EccLocalModelRefusal("ecc_local_model_unavailable") from None
    if (
        type(installed) is not list
        or len(installed) > 256
        or any(type(item) is not str or len(item) > 128 for item in installed)
    ):
        raise EccLocalModelRefusal("ecc_local_model_unavailable")
    if model_id not in installed:
        raise EccLocalModelRefusal("ecc_local_model_not_installed")
    try:
        attested_digest = await asyncio.wait_for(provider.attest_local_model(model_id), timeout=5.0)
    except (AttributeError, ModelGatewayError, TimeoutError, OSError, ValueError, TypeError):
        raise EccLocalModelRefusal("ecc_local_model_not_locally_qualified") from None
    if type(attested_digest) is not str or not _SHA256.fullmatch(attested_digest):
        raise EccLocalModelRefusal("ecc_local_model_not_locally_qualified")

    observed: list[EccModelCaseObservation] = []
    for case in suite.cases:
        prompt = (
            f"Skill: {skill_id}\n"
            "Use this Zelo-authored manual evaluation checklist as rubric only:\n"
            + "\n".join(f"- {step}" for step in playbook.ordered_manual_steps)
            + f"\nEvidence case {case.case_id}:\n{case.evidence}"
        )
        request = ModelRequest(
            model_id=model_id,
            system_prompt=_SYSTEM,
            user_messages=[prompt],
            timeout_seconds=timeout_per_case,
            output_schema=_EXPECTED_FORMAT,
        )
        started = time.monotonic_ns()
        try:
            result = await asyncio.wait_for(provider.generate(request), timeout=timeout_per_case)
        except (ModelGatewayError, TimeoutError, OSError, ValueError, TypeError):
            raise EccLocalModelRefusal("ecc_local_model_generation_failed") from None
        elapsed = time.monotonic_ns() - started
        if result.model_id != model_id or result.provider != "ollama":
            raise EccLocalModelRefusal("ecc_local_model_identity_changed")
        try:
            if (
                type(result.text) is not str
                or not 1 <= len(result.text.encode("utf-8", "strict")) <= _MAX_OUTPUT_BYTES
            ):
                raise EccLocalModelRefusal("ecc_local_model_response_invalid")
        except UnicodeError:
            raise EccLocalModelRefusal("ecc_local_model_response_invalid") from None
        try:
            parsed = json.loads(result.text, object_pairs_hook=_unique_pairs)
            response = _ModelVerdict.model_validate(parsed, strict=True)
        except (ValueError, ValidationError, TypeError, RecursionError, UnicodeError):
            raise EccLocalModelRefusal("ecc_local_model_response_invalid") from None
        try:
            observed.append(
                EccModelCaseObservation(
                    case_id=case.case_id,
                    answer=response.verdict,
                    raw_output_sha256=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
                    measured_duration_ns=elapsed,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
            )
        except ValidationError:
            raise EccLocalModelRefusal("ecc_local_model_metrics_invalid") from None

    try:
        after_digest = await asyncio.wait_for(provider.attest_local_model(model_id), timeout=5.0)
    except (AttributeError, ModelGatewayError, TimeoutError, OSError, ValueError, TypeError):
        raise EccLocalModelRefusal("ecc_local_model_identity_changed") from None
    if after_digest != attested_digest:
        raise EccLocalModelRefusal("ecc_local_model_identity_changed")
    try:
        refreshed_suite = ecc_fixed_suite(skill_id)
    except (EccPlaybookRefusal, EccFixtureRefusal, CommunitySourceRefusal):
        raise EccLocalModelRefusal("ecc_local_model_source_changed") from None
    if refreshed_suite != suite:
        raise EccLocalModelRefusal("ecc_local_model_source_changed")
    submitted = EccCaseSubmission(
        candidate_label=_candidate_label(model_id),
        expected_suite_sha256=suite.suite_sha256,
        answers=tuple(
            EccCaseAnswer(case_id=item.case_id, verdict=item.answer) for item in observed
        ),
    )
    try:
        evaluation = evaluate_ecc_fixed_cases(skill_id, submitted)
    except EccFixtureRefusal:
        raise EccLocalModelRefusal("ecc_local_model_source_changed") from None
    material = {
        "schema_version": "zelo-ecc-local-model-probe-v1",
        "model_id": model_id,
        "model_digest_sha256": attested_digest,
        "provider": "ollama",
        "skill_id": skill_id,
        "source_commit": suite.source_commit,
        "suite_sha256": suite.suite_sha256,
        "playbook_sha256": suite.playbook_sha256,
        "case_observations": tuple(item.model_dump(mode="json") for item in observed),
        "evaluation": evaluation.model_dump(mode="json"),
        "local_model_responses_observed": True,
        "actual_agent_task_or_permission_evaluated": False,
        "imported_ecc_source_injected": False,
        "runtime_authority": False,
        "skill_promotion_authority": False,
        "tool_grants": (),
        "result_admitted_to_mind": False,
    }
    # Pydantic strict nested models need the typed receipt, not JSON arrays
    # for tuple-typed fields. Hash the canonical JSON projection separately.
    signature = _sha(material)
    material["evaluation"] = evaluation
    return EccLocalModelProbe(**material, probe_sha256=signature)
