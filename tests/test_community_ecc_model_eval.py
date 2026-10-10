"""Real local model responses to fixed ECC cases, never active agent authority."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.community_ecc_eval import ecc_fixed_suite
from app.community_ecc_model_eval import (
    EccLocalModelProbe,
    EccLocalModelRefusal,
    PinnedLocalOllamaProvider,
    _local_gguf_digest,
    probe_local_ecc_model,
)
from app.gateway import ModelGatewayError, ModelRequest, ModelResult

MODEL = "qwen3:4b-instruct"
DIGEST = "d" * 64


def _local_entry(**changes):
    result = {
        "name": MODEL,
        "size": 2497293803,
        "digest": DIGEST,
        "details": {"format": "gguf", "family": "qwen3"},
    }
    result.update(changes)
    return result


class FakeOllamaProvider:
    def __init__(self, answers=("fail", "pass", "unverified"), *, raw=None, model=MODEL):
        self.answers = answers
        self.raw = raw
        self.model = model
        self.requests: list[ModelRequest] = []
        self.model_queries = 0
        self.attestation_queries = 0

    async def attest_local_model(self, model_id):
        self.attestation_queries += 1
        assert model_id == MODEL
        return DIGEST

    async def models(self):
        self.model_queries += 1
        return [MODEL]

    async def generate(self, request):
        self.requests.append(request)
        idx = len(self.requests) - 1
        body = self.raw if self.raw is not None else json.dumps({"verdict": self.answers[idx]})
        return ModelResult(
            text=body,
            provider="ollama",
            model_id=self.model,
            prompt_tokens=12,
            completion_tokens=3,
        )


def _run(provider, skill="ecc_security_review", model=MODEL, timeout=10.0):
    return asyncio.run(
        probe_local_ecc_model(provider, skill_id=skill, model_id=model, timeout_per_case=timeout)
    )


def test_three_actual_provider_responses_are_scored_but_no_agent_authority():
    provider = FakeOllamaProvider()
    receipt = _run(provider)
    assert receipt.model_id == MODEL
    assert receipt.provider == "ollama"
    assert receipt.evaluation.agreement_count == 3
    assert receipt.evaluation.unsafe_miss_count == 0
    assert receipt.evaluation.candidate_label.startswith("ollama-")
    assert len(receipt.evaluation.candidate_label) <= 64
    assert len(receipt.case_observations) == len(provider.requests) == 3
    assert [x.answer for x in receipt.case_observations] == ["fail", "pass", "unverified"]
    assert all(
        x.prompt_tokens == 12 and x.completion_tokens == 3 for x in receipt.case_observations
    )
    assert all(x.measured_duration_ns >= 0 for x in receipt.case_observations)
    assert receipt.local_model_responses_observed is True
    assert receipt.actual_agent_task_or_permission_evaluated is False
    assert receipt.imported_ecc_source_injected is False
    assert receipt.runtime_authority is False
    assert receipt.skill_promotion_authority is False
    assert receipt.tool_grants == ()
    assert receipt.result_admitted_to_mind is False
    assert len(receipt.probe_sha256) == 64
    assert EccLocalModelProbe.model_validate_json(receipt.model_dump_json(), strict=True) == receipt
    assert provider.model_queries == 1
    assert provider.attestation_queries == 2
    assert receipt.model_digest_sha256 == DIGEST
    for request in provider.requests:
        assert request.model_id == MODEL
        assert request.output_schema["required"] == ["verdict"]
        assert request.output_schema["additionalProperties"] is False
        assert "no tools" in request.system_prompt.lower()
        assert "ECC" not in json.dumps(request.output_schema)
        assert "upstream" not in request.system_prompt.lower()


def test_incorrect_model_decisions_are_measured_not_forgiven():
    receipt = _run(FakeOllamaProvider(("pass", "fail", "pass")))
    assert receipt.evaluation.agreement_count == 0
    assert receipt.evaluation.unsafe_miss_count == 1
    assert receipt.evaluation.false_alarm_count == 1
    assert receipt.evaluation.insufficient_evidence_overclaim_count == 1
    assert receipt.evaluation.actual_agent_behavior_qualified is False


def test_pinned_local_model_tag_and_chat_clients_ignore_http_proxy(monkeypatch):
    import httpx

    from app.gateway import ModelRequest

    events = []

    class LocalResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class LocalClient:
        def __init__(self, **kwargs):
            events.append(("client", kwargs))
            assert kwargs["base_url"] == "http://127.0.0.1:11434"
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, path):
            assert path == "/api/tags"
            return LocalResponse({"models": [_local_entry()]})

        async def post(self, path, *, json):
            assert path == "/api/chat"
            assert json["model"] == MODEL
            return LocalResponse({"model": MODEL, "message": {"content": '{"verdict":"fail"}'}})

    monkeypatch.setattr(httpx, "AsyncClient", LocalClient)
    provider = PinnedLocalOllamaProvider()

    async def exercise():
        assert await provider.models() == [MODEL]
        assert await provider.attest_local_model(MODEL) == DIGEST
        answer = await provider.generate(
            ModelRequest(MODEL, "check local case", ["only fixed evidence"], 5.0)
        )
        assert answer.text == '{"verdict":"fail"}'

    asyncio.run(exercise())
    assert len(events) == 3


def test_local_gguf_metadata_verifier_accepts_only_large_derived_weights():
    assert _local_gguf_digest([_local_entry()], MODEL) == DIGEST
    assert PinnedLocalOllamaProvider().base_url == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "bad",
    [
        [
            {
                "name": MODEL,
                "size": 1,
                "digest": DIGEST,
                "details": {"format": "gguf", "family": "qwen3"},
            }
        ],
        [_local_entry(size=True)],
        [_local_entry(size=1)],
        [_local_entry(digest="not-digest")],
        [_local_entry(details={"format": "cloud", "family": "qwen3"})],
        [_local_entry(details={"format": "gguf", "family": ""})],
        [_local_entry(name="some-other:model")],
        [_local_entry(), _local_entry()],
        "untrusted catalog string",
        [],
    ],
)
def test_invalid_or_cloud_ollama_metadata_refuses_without_generation(bad):
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_not_locally_qualified"):
        _local_gguf_digest(bad, MODEL)


@pytest.mark.parametrize("cloud_id", ["qwen3.5:cloud", "gpt-oss:120b-cloud", "cloud-model:latest"])
def test_cloud_tag_is_never_admitted_even_with_large_gguf_bytes(cloud_id):
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_not_locally_qualified"):
        _local_gguf_digest([_local_entry(name=cloud_id)], cloud_id)


def test_local_ollama_digest_drift_between_first_and_second_attestation_refuses():
    class SwappingModel(FakeOllamaProvider):
        async def attest_local_model(self, model_id):
            self.attestation_queries += 1
            return DIGEST if self.attestation_queries == 1 else "e" * 64

    provider = SwappingModel()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_identity_changed"):
        _run(provider)
    assert provider.attestation_queries == 2
    assert len(provider.requests) == 3


@pytest.mark.parametrize("timeout", [None, "ten", True, 0, 60, float("nan")])
def test_invalid_timeout_refuses_before_any_backend_io(timeout):
    provider = FakeOllamaProvider()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_request_invalid"):
        _run(provider, timeout=timeout)
    assert provider.model_queries == 0
    assert provider.requests == []


def test_model_catalog_must_be_typed_exact_list_not_substring_match():
    class UntrustedCatalog(FakeOllamaProvider):
        async def models(self):
            self.model_queries += 1
            return "prefix" + MODEL + "suffix"

    provider = UntrustedCatalog()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_unavailable"):
        _run(provider)
    assert provider.requests == []


def test_model_not_installed_refuses_before_any_generation():
    provider = FakeOllamaProvider()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_not_installed"):
        _run(provider, model="uninstalled-model:1b")
    assert provider.model_queries == 1
    assert provider.requests == []


@pytest.mark.parametrize(
    "bad_model",
    ["", "http://evil/", "../untrusted", "é", "a" * 65],
)
def test_invalid_model_identity_refuses_before_provider_io(bad_model):
    provider = FakeOllamaProvider()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_request_invalid"):
        _run(provider, model=bad_model)
    assert provider.model_queries == 0
    assert provider.requests == []


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{}",
        '{"verdict":"pass","tool":"shell"}',
        '{"verdict":"pass","verdict":"fail"}',
        '{"verdict":true}',
        '{"verdict":"approved"}',
        "x" * 513,
        "\ud800",
    ],
)
def test_malformed_or_untrusted_generated_json_never_yields_positive_receipt(raw):
    provider = FakeOllamaProvider(raw=raw)
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_response_invalid"):
        _run(provider)
    assert provider.model_queries == 1
    assert len(provider.requests) == 1


def test_changing_model_identity_after_selection_refuses_without_score():
    provider = FakeOllamaProvider(model="other:3b")
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_identity_changed"):
        _run(provider)
    assert len(provider.requests) == 1


def test_backend_generation_failure_fails_closed():
    class BrokenProvider(FakeOllamaProvider):
        async def generate(self, request):
            self.requests.append(request)
            raise ModelGatewayError("model_timeout")

    provider = BrokenProvider()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_generation_failed"):
        _run(provider)
    assert len(provider.requests) == 1


def test_changed_fixture_mid_run_refuses_after_all_three_responses(monkeypatch):
    from app import community_ecc_model_eval as module

    original = module.ecc_fixed_suite
    reads = 0

    def changed_fixture(skill):
        nonlocal reads
        reads += 1
        suite = original(skill)
        if reads > 1:
            return suite.model_copy(update={"suite_sha256": "0" * 64})
        return suite

    monkeypatch.setattr(module, "ecc_fixed_suite", changed_fixture)
    provider = FakeOllamaProvider()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_source_changed"):
        _run(provider)
    assert len(provider.requests) == 3
    assert reads == 2


def test_pinned_ecc_source_unavailable_refuses_before_any_model_request(monkeypatch):
    from app import community_ecc_model_eval as module
    from app.community_sources import CommunitySourceRefusal

    def broken_source(*args, **kwargs):
        raise CommunitySourceRefusal("credential_path_should_never_leave_source_boundary")

    monkeypatch.setattr(module, "ecc_fixed_suite", broken_source)
    provider = FakeOllamaProvider()
    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_source_unavailable"):
        _run(provider)
    assert provider.requests == []
    assert provider.model_queries == 0


def test_model_output_metrics_must_not_be_negative():
    class BadTokenProvider(FakeOllamaProvider):
        async def generate(self, request):
            self.requests.append(request)
            return ModelResult(
                text=json.dumps({"verdict": "pass"}),
                provider="ollama",
                model_id=MODEL,
                prompt_tokens=-1,
            )

    with pytest.raises(EccLocalModelRefusal, match="ecc_local_model_metrics_invalid"):
        _run(BadTokenProvider())


def test_probe_receipt_tampering_does_not_validate():
    original = _run(FakeOllamaProvider())
    changed = original.model_dump(mode="json")
    changed["evaluation"]["agreement_count"] = 2
    with pytest.raises(ValidationError):
        EccLocalModelProbe.model_validate_json(json.dumps(changed), strict=True)
    changed = original.model_dump(mode="json")
    changed["runtime_authority"] = True
    with pytest.raises(ValidationError):
        EccLocalModelProbe.model_validate_json(json.dumps(changed), strict=True)


def test_cli_refuses_model_contact_without_explicit_opt_in(capsys, monkeypatch):
    from scripts import evaluate_ecc_draft_local as cli

    def never_construct(*args, **kwargs):
        raise AssertionError("Model provider must not be created")

    monkeypatch.setattr(cli, "PinnedLocalOllamaProvider", never_construct)
    code = cli.main(["--skill-id", "ecc_security_review", "--model", MODEL])
    assert code == 2
    assert "requires_explicit_opt_in" in capsys.readouterr().err


def test_cli_opted_in_prints_only_bounded_evidence_json(capsys, monkeypatch):
    from scripts import evaluate_ecc_draft_local as cli

    provider = FakeOllamaProvider()

    def factory():
        return provider

    monkeypatch.setattr(cli, "PinnedLocalOllamaProvider", factory)
    code = cli.main(
        [
            "--skill-id",
            "ecc_security_review",
            "--model",
            MODEL,
            "--allow-local-ecc-probe",
        ]
    )
    assert code == 0
    stdout = capsys.readouterr().out.strip()
    decoded = EccLocalModelProbe.model_validate_json(stdout, strict=True)
    assert decoded.evaluation.agreement_count == 3
    assert len(provider.requests) == 3


def test_local_probe_skips_external_source_and_preserves_playbook_digest():
    suite = ecc_fixed_suite("ecc_security_review")
    assert len(suite.cases) == 3
    receipt = _run(FakeOllamaProvider())
    assert receipt.source_commit == suite.source_commit
    assert receipt.suite_sha256 == suite.suite_sha256
    assert receipt.playbook_sha256 == suite.playbook_sha256
