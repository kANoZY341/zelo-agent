import asyncio
from datetime import UTC, datetime

from scripts.smoke_promotion import (
    SMOKE_CANDIDATE,
    SMOKE_CANDIDATE_CONTENT,
    DeterministicSmokeWorker,
    smoke_branch,
)


def test_smoke_branch_is_stable_and_namespaced():
    assert smoke_branch("a" * 40) == "zelo/promotion/smoke-aaaaaaaaaaaa"


def test_deterministic_worker_creates_fixed_candidate(tmp_path):
    worker = DeterministicSmokeWorker()
    repo = tmp_path / "repo"
    repo.mkdir()
    started = datetime.now(UTC)

    result = asyncio.run(worker.run(None, repo))

    assert result.exit_code == 0
    assert (repo / SMOKE_CANDIDATE).read_text() == SMOKE_CANDIDATE_CONTENT
    assert result.started_at >= started
