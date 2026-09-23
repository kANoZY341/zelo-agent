"""Policy-bound self-improvement planner tests."""

import asyncio
import json

import pytest

from app.gateway import ModelGateway, ModelRequest, ModelResult
from app.self_improvement import RepositoryInspector, SelfImprovementError
from app.self_improvement_planner import (
    PLANNER_SYSTEM_PROMPT,
    SelfImprovementPlanner,
    SelfImprovementPolicy,
)
from tests.test_self_improvement import make_repo, proposal_draft


class ScriptedProvider:
    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        text = self.response if isinstance(self.response, str) else json.dumps(self.response)
        return ModelResult(
            text=text,
            provider="fake",
            model_id=request.model_id,
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        )

    async def models(self) -> list[str]:
        return ["qwen3:4b-instruct"]


def gateway(provider: ScriptedProvider) -> ModelGateway:
    return ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "1")


def valid_response(**changes) -> dict:
    draft = proposal_draft(risk="medium")
    payload = draft.model_dump(mode="json")
    payload.update(changes)
    return payload


def test_planner_returns_frozen_policy_checked_proposal(tmp_path):
    repo, sha = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    provider = ScriptedProvider(valid_response())
    planner = SelfImprovementPlanner(gateway(provider))

    result = asyncio.run(planner.plan(inspection))

    assert result.alias == "local_general"
    assert result.actual_model == "qwen3:4b-instruct"
    assert result.total_tokens == 18
    assert result.proposal.base_sha == sha
    assert result.proposal.inspection_fingerprint == inspection.fingerprint
    assert len(result.proposal.planner_config_version) == 64

    request = provider.requests[0]
    assert request.output_schema
    assert "implementation_instruction" in request.output_schema["properties"]
    assert request.messages[0].role == "user"
    assert str(repo) not in request.messages[0].content
    assert inspection.fingerprint in request.messages[0].content
    assert request.system_prompt == PLANNER_SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (valid_response(risk="high"), "proposal_risk_denied"),
        (
            valid_response(required_capabilities=["python_development"]),
            "proposal_capability_denied",
        ),
        (valid_response(evidence=["missing.py"]), "proposal_evidence_unavailable"),
        (valid_response(scope_paths=["app/policy.py"]), "proposal_high_risk_scope"),
        (valid_response(risk="low"), "proposal_risk_understated"),
        (valid_response(scope_paths=["pyproject.toml"]), "proposal_scope_denied"),
    ],
)
def test_planner_policy_rejects_model_authored_authority_expansion(tmp_path, response, error):
    repo, _ = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    planner = SelfImprovementPlanner(gateway(ScriptedProvider(response)))

    with pytest.raises(SelfImprovementError, match=error):
        asyncio.run(planner.plan(inspection))


def test_planner_rejects_vendor_or_worker_fields_not_in_schema(tmp_path):
    repo, _ = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    response = valid_response()
    response["worker_id"] = "codex_core"

    planner = SelfImprovementPlanner(gateway(ScriptedProvider(response)))

    with pytest.raises(SelfImprovementError, match="planner_output_invalid"):
        asyncio.run(planner.plan(inspection))


def test_planner_rejects_duplicate_json_keys(tmp_path):
    repo, _ = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    payload = valid_response()
    encoded = json.dumps(payload)
    duplicate = encoded[:-1] + ',"title":"second title"}'

    planner = SelfImprovementPlanner(gateway(ScriptedProvider(duplicate)))

    with pytest.raises(SelfImprovementError, match="planner_output_invalid"):
        asyncio.run(planner.plan(inspection))


def test_policy_allows_low_risk_docs_only_proposal(tmp_path):
    repo, _ = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    draft = proposal_draft(
        risk="low",
        evidence=("README.md",),
        scope_paths=("docs/new-design-note.md",),
    )

    SelfImprovementPolicy().validate(draft, inspection)
