"""Local OCR probe uses ModelProvider with zero Zelo task or tool grants."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.community_review_local_model import (
    OcrLocalModelProbe,
    OcrLocalModelRefusal,
    PinnedLocalOcrProvider,
    probe_local_ocr_model,
)
from app.community_sources import CommunitySourceRefusal
from app.gateway import ModelRequest, ModelResult
from scripts import evaluate_ocr_draft_local as cli
from tests.test_community_review_fixed_eval import PAGINATION, SECURITY, _raw

_MODEL = "qwen3:4b-instruct"
_DIGEST = "d" * 64


class FakeLocalProvider:
    def __init__(self, *, outputs=None, after_digest=None, wrong_provider=False):
        self.calls = []
        self.attest_count = 0
        self.outputs = outputs or (
            _raw((SECURITY,)),
            _raw((PAGINATION,)),
            _raw(),
            _raw(),
        )
        self.after_digest = after_digest
        self.wrong_provider = wrong_provider

    async def models(self):
        return [_MODEL]

    async def attest_local_model(self, model_id):
        self.attest_count += 1
        assert model_id == _MODEL
        if self.attest_count == 2 and self.after_digest is not None:
            return self.after_digest
        return _DIGEST

    async def generate(self, request):
        self.calls.append(request)
        idx = len(self.calls) - 1
        return ModelResult(
            text=self.outputs[idx],
            provider="ollama" if not self.wrong_provider else "remote",
            model_id=request.model_id,
            prompt_tokens=88 + idx,
            completion_tokens=32 + idx,
        )


def run(provider, model_id=_MODEL, timeout=20):
    return asyncio.run(probe_local_ocr_model(provider, model_id=model_id, timeout_per_case=timeout))


def test_fake_model_receipt_is_integrity_checked_and_no_authority():
    provider = FakeLocalProvider()
    result = run(provider)
    assert result.model_id == _MODEL
    assert result.model_digest_sha256 == _DIGEST
    assert result.provider == "ollama"
    assert len(result.case_observations) == 4
    assert len(provider.calls) == 4
    assert provider.attest_count == 2
    assert result.evaluation.matched_finding_count == 2
    assert result.evaluation.unexpected_finding_count == 0
    assert result.evaluation.missed_finding_count == 0
    assert result.evaluation.invalid_review_count == 0
    assert result.local_model_responses_observed
    assert not result.actual_agent_task_qualified
    assert not result.real_git_diff_reviewed
    assert not result.independent_semantic_accuracy_proven
    assert not result.reviewer_activation_authority
    assert not result.result_admitted_to_mind
    assert result.tool_or_space_grants == ()
    assert OcrLocalModelProbe.model_validate_json(result.model_dump_json(), strict=True) == result
    for request in provider.calls:
        assert request.output_schema is not None
        assert request.timeout_seconds == 20
        assert request.model_id == _MODEL
        assert len(request.user_messages) == 1
        assert "fictional" in request.system_prompt.lower()
        assert "http" not in request.user_messages[0]
    assert "raw_review_json" not in result.model_dump_json()
    assert SECURITY["content"] not in result.model_dump_json()


def test_wrong_model_alias_and_timeout_refused_before_provider_call():
    for name in ("model-cloud", "../remote", "https://host", "local//model", ""):
        provider = FakeLocalProvider()
        with pytest.raises(OcrLocalModelRefusal, match="request_invalid"):
            run(provider, name)
        assert not provider.calls
    for timeout in (-1, 1, 46, float("nan")):
        with pytest.raises(OcrLocalModelRefusal, match="request_invalid"):
            run(FakeLocalProvider(), timeout=timeout)


def test_not_installed_and_non_attested_model_refuse_without_generation():
    provider = FakeLocalProvider()

    async def missing():
        return []

    provider.models = missing
    with pytest.raises(OcrLocalModelRefusal, match="not_installed"):
        run(provider)
    assert provider.calls == []

    provider = FakeLocalProvider()

    async def invalid_digest(model_id):
        return "cloud"

    provider.attest_local_model = invalid_digest
    with pytest.raises(OcrLocalModelRefusal, match="identity_invalid"):
        run(provider)
    assert provider.calls == []


def test_changed_attested_model_identity_refuses_after_all_four_cases():
    provider = FakeLocalProvider(after_digest="e" * 64)
    with pytest.raises(OcrLocalModelRefusal, match="identity_changed"):
        run(provider)
    assert len(provider.calls) == 4


def test_wrong_provider_rejected_without_false_local_observation():
    provider = FakeLocalProvider(wrong_provider=True)
    with pytest.raises(OcrLocalModelRefusal, match="identity_changed"):
        run(provider)
    assert len(provider.calls) == 1


def test_invalid_json_output_is_counted_as_invalid_not_clean():
    provider = FakeLocalProvider(
        outputs=(_raw((SECURITY,)), _raw((PAGINATION,)), "{not-json", _raw())
    )
    result = run(provider)
    assert result.evaluation.invalid_review_count == 1
    assert result.evaluation.clean_negative_count == 1
    assert result.evaluation.cases[2].adapter_status == "invalid"


def test_oversized_or_invalid_metrics_refuse_entire_receipt():
    provider = FakeLocalProvider()
    provider.outputs = ("x" * 4097,) + tuple(provider.outputs[1:])
    with pytest.raises(OcrLocalModelRefusal, match="output_invalid"):
        run(provider)
    assert len(provider.calls) == 1

    provider = FakeLocalProvider()
    original = provider.generate

    async def negative_metrics(request):
        return replace(await original(request), prompt_tokens=-1)

    provider.generate = negative_metrics
    with pytest.raises(OcrLocalModelRefusal, match="output_invalid"):
        run(provider)


def test_source_integrity_refuses_before_generation():
    with patch(
        "app.community_review_local_model.ocr_fixed_suite",
        side_effect=CommunitySourceRefusal("source_modified"),
    ):
        provider = FakeLocalProvider()
        with pytest.raises(OcrLocalModelRefusal, match="preflight_failed"):
            run(provider)
        assert provider.calls == []


def test_sealed_receipt_tampering_rejected():
    record = run(FakeLocalProvider()).model_dump(mode="json")
    tampered = json.loads(json.dumps(record))
    tampered["model_digest_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="ocr_local_probe_digest_invalid"):
        OcrLocalModelProbe.model_validate_json(json.dumps(tampered), strict=True)
    tampered = json.loads(json.dumps(record))
    tampered["reviewer_activation_authority"] = True
    with pytest.raises(ValidationError):
        OcrLocalModelProbe.model_validate_json(json.dumps(tampered), strict=True)


def test_cli_requires_explicit_opt_in_without_constructing_a_provider(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("Provider should not start without opt-in")

    monkeypatch.setattr(cli, "PinnedLocalOcrProvider", forbidden)
    assert cli.main(["--model", _MODEL]) == 2
    assert "requires_explicit_opt_in" in capsys.readouterr().err


def test_ocr_provider_preserves_loopback_attestation_and_emits_bounded_512_token_budget(
    monkeypatch,
):
    provider = PinnedLocalOcrProvider()
    assert provider.base_url == "http://127.0.0.1:11434"
    assert provider.use_json_schema is True
    assert provider._http_trust_env is False
    assert provider._structured_action_options == {"num_predict": 512}
    assert callable(provider.attest_local_model)

    sent = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": _MODEL,
                "message": {"content": _raw()},
                "prompt_eval_count": 11,
                "eval_count": 12,
            }

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://127.0.0.1:11434"
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, *, json):
            sent.append((path, json))
            return Response()

    monkeypatch.setattr("app.gateway.httpx.AsyncClient", Client)
    result = asyncio.run(
        provider.generate(
            ModelRequest(
                model_id=_MODEL,
                system_prompt="Trusted case",
                user_messages=["Fixed synthetic case only"],
                timeout_seconds=30,
                output_schema={"type": "object"},
            )
        )
    )
    assert result.model_id == _MODEL and result.provider == "ollama"
    assert sent[0][0] == "/api/chat"
    assert sent[0][1]["model"] == _MODEL
    assert sent[0][1]["think"] is False
    assert sent[0][1]["stream"] is False
    assert sent[0][1]["format"] == {"type": "object"}
    assert sent[0][1]["options"]["num_predict"] == 512
    assert sent[0][1]["options"]["temperature"] == 0
