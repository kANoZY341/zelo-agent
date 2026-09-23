"""One-cycle self-improvement preparation runtime tests."""

import asyncio

import pytest

from app.self_improvement import RepositoryInspector, SelfImprovementError
from app.self_improvement_planner import SelfImprovementPlanner
from app.self_improvement_runtime import SelfImprovementRuntime, developer_instruction
from tests.developer_helpers import git
from tests.test_self_improvement import make_repo
from tests.test_self_improvement_planner import ScriptedProvider, gateway, valid_response


def runtime_for(repo, provider):
    return SelfImprovementRuntime(
        RepositoryInspector(repo),
        SelfImprovementPlanner(gateway(provider)),
    )


def test_runtime_prepares_one_frozen_candidate_without_mutating_repo(tmp_path):
    repo, sha = make_repo(tmp_path)
    provider = ScriptedProvider(valid_response())
    runtime = runtime_for(repo, provider)

    preparation = asyncio.run(runtime.prepare("Improve Zelo safely."))

    assert preparation.base_sha == sha
    assert preparation.goal == "Improve Zelo safely."
    assert preparation.proposal.goal == preparation.goal
    assert preparation.proposal.base_sha == sha
    assert preparation.proposal.inspection_fingerprint == preparation.inspection_fingerprint
    assert preparation.tracked_path_count >= preparation.inspected_content_file_count
    assert preparation.planner_alias == "local_general"
    assert preparation.planner_model == "qwen3:4b-instruct"
    assert preparation.total_tokens == 18

    serialized = preparation.model_dump_json()
    assert "VALUE = 1" not in serialized
    assert "review evidence that should not enter model content" not in serialized
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == sha


def test_runtime_stops_before_planner_when_repository_is_dirty(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    provider = ScriptedProvider(valid_response())
    runtime = runtime_for(repo, provider)

    with pytest.raises(SelfImprovementError, match="inspection_repository_dirty"):
        asyncio.run(runtime.prepare("Improve Zelo safely."))

    assert provider.requests == []


@pytest.mark.parametrize("goal", ["", " leading", "trailing ", "x" * 1025])
def test_runtime_rejects_noncanonical_goal(tmp_path, goal):
    repo, _ = make_repo(tmp_path)
    provider = ScriptedProvider(valid_response())
    runtime = runtime_for(repo, provider)

    with pytest.raises(SelfImprovementError, match="improvement_goal_invalid"):
        asyncio.run(runtime.prepare(goal))

    assert provider.requests == []


def test_runtime_rejects_repository_change_during_planning(tmp_path):
    repo, _ = make_repo(tmp_path)

    class MutatingProvider(ScriptedProvider):
        async def generate(self, request):
            (repo / "app" / "mid_plan.py").write_text("MUTATED = True\n", encoding="utf-8")
            return await super().generate(request)

    runtime = runtime_for(repo, MutatingProvider(valid_response()))

    with pytest.raises(
        SelfImprovementError,
        match="inspection_repository_(dirty|changed)",
    ):
        asyncio.run(runtime.prepare("Improve Zelo safely."))


def test_developer_instruction_is_bound_to_frozen_proposal(tmp_path):
    repo, _ = make_repo(tmp_path)
    runtime = runtime_for(repo, ScriptedProvider(valid_response()))

    preparation = asyncio.run(runtime.prepare("Improve Zelo safely."))
    instruction = developer_instruction(preparation)

    assert preparation.proposal.proposal_fingerprint in instruction
    assert preparation.base_sha in instruction
    assert preparation.goal in instruction
    for path in preparation.proposal.draft.evidence:
        assert f"- {path}" in instruction
    for path in preparation.proposal.draft.scope_paths:
        assert f"- {path}" in instruction
    for criterion in preparation.proposal.draft.acceptance_criteria:
        assert f"- {criterion}" in instruction
    assert "Do not merge, tag, release, or deploy." in instruction
    assert len(instruction) <= 65536
