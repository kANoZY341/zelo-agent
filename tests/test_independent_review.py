"""Independent reviewer binding/execution regressions for v0.10."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.developer import DeveloperError, DeveloperRuntime
from app.developer_contracts import WorkerResult
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from app.worker_registry import WorkerRegistry, descriptor_for_reviewer, descriptor_for_worker
from tests.developer_helpers import FakeWorker


class ReviewWorker(FakeWorker):
    def __init__(self, implementation: str = "reviewer_fake", *, mutate: bool = False):
        super().__init__()
        self.mutate = mutate
        self.worker_identity = self.worker_identity.model_copy(
            update={"implementation": implementation}
        )

    async def run(self, request, repo):
        self.calls.append(repo)
        started = datetime.now(UTC)
        if self.mutate:
            (repo / "REVIEWER_MUTATION.txt").write_text("forbidden\n")
        summary = json.dumps(
            {
                "summary": "Independent review complete",
                "findings": [
                    {
                        "severity": "low",
                        "message": "No release-blocking issue found",
                        "path": None,
                    }
                ],
            },
            separators=(",", ":"),
        )
        return WorkerResult(
            exit_code=0,
            summary=summary,
            event_count=2,
            usage={"input_tokens": 10, "output_tokens": 5},
            nonfatal_error_items=0,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=0.01,
        )


def review_runtime(developer_case, *, mutate: bool = False):
    producer = FakeWorker()
    reviewer = ReviewWorker(mutate=mutate)
    registry = WorkerRegistry(
        [
            (
                descriptor_for_worker(
                    producer,
                    implementation="fake",
                    worker_id="producer",
                    priority=100,
                ),
                producer,
            ),
            (
                descriptor_for_reviewer(
                    reviewer,
                    implementation="reviewer_fake",
                    worker_id="independent_reviewer",
                ),
                reviewer,
            ),
        ]
    )
    runtime = DeveloperRuntime(
        developer_case.settings,
        producer,
        developer_case.source_repository,
        registry,
    )
    return runtime, producer, reviewer


def named_review_runtime(
    developer_case,
    producer_implementation: str,
    reviewer_implementation: str,
):
    producer = FakeWorker()
    producer.worker_identity = producer.worker_identity.model_copy(
        update={"implementation": producer_implementation}
    )
    reviewer = ReviewWorker(reviewer_implementation)
    registry = WorkerRegistry(
        [
            (
                descriptor_for_worker(
                    producer,
                    implementation=producer_implementation,
                    worker_id=f"{producer_implementation}_core",
                    priority=100,
                ),
                producer,
            ),
            (
                descriptor_for_reviewer(
                    reviewer,
                    implementation=reviewer_implementation,
                    worker_id=f"{reviewer_implementation}_reviewer",
                ),
                reviewer,
            ),
        ]
    )
    return DeveloperRuntime(
        developer_case.settings,
        producer,
        developer_case.source_repository,
        registry,
    )


@pytest.mark.parametrize(
    ("producer_implementation", "reviewer_implementation"),
    [
        ("codex", "claude_code"),
        ("claude_code", "codex"),
        ("opencode", "claude_code"),
    ],
)
def test_cross_implementation_worker_review_runs_end_to_end(
    developer_case,
    producer_implementation,
    reviewer_implementation,
):
    runtime = named_review_runtime(
        developer_case,
        producer_implementation,
        reviewer_implementation,
    )
    request = runtime.request("Make the normal harmless fixture change", "zelo_core", 60)
    assert request.reviewer is not None
    assert request.worker.implementation == producer_implementation
    assert request.reviewer.worker.implementation == reviewer_implementation
    assert (
        request.worker_selection.descriptor.worker_id
        != request.reviewer.worker_selection.descriptor.worker_id
    )

    result = asyncio.run(
        runtime.run(
            str(uuid4()),
            str(uuid4()),
            request,
            lambda *_: None,
        )
    )

    assert result["error"] is None
    assert result["validation_passed"] is True
    assert result["worker"] == producer_implementation
    assert result["independent_review"] is not None
    assert result["independent_review"]["implementation"] == reviewer_implementation
    assert result["independent_review"]["report"]["summary"] == "Independent review complete"


def test_developer_request_binds_independent_reviewer_and_audits_selection(
    developer_case, database
):
    runtime, _, reviewer = review_runtime(developer_case)
    service = TaskService(database, runtime.settings, developer_runtime=runtime)

    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "test"}), None
    )

    request = runtime.request("test", "zelo_core", task.timeout_seconds)
    assert request.reviewer is not None
    assert request.reviewer.worker == reviewer.identity()
    assert request.reviewer.worker_selection.descriptor.worker_id == "independent_reviewer"
    assert request.reviewer.worker_selection.authority_profile == "workspace-read-no-network-v1"
    assert "producer" in request.reviewer.worker_selection.excluded_worker_ids

    events = service.events(task.id, 50, 0)
    selected = [event for event in events if event.event == "reviewer_selected"]
    assert len(selected) == 1
    assert selected[0].details["worker_id"] == "independent_reviewer"


def test_reviewer_identity_change_invalidates_approval_binding(developer_case):
    runtime, _, reviewer = review_runtime(developer_case)
    request = runtime.request("test", "zelo_core", 30)
    assert request.reviewer is not None

    reviewer.worker_identity = reviewer.identity().model_copy(update={"cli_version": "changed"})

    assert not runtime.approval_compatible(request, 30)


@pytest.mark.skipif(not Path("/usr/bin/bwrap").is_file(), reason="Bubblewrap required")
def test_independent_reviewer_executes_read_only_and_returns_structured_report(developer_case):
    runtime, _, reviewer = review_runtime(developer_case)
    request = runtime.request("test", "zelo_core", 30)
    repo = runtime.path(str(uuid4()))
    repo.parent.mkdir(parents=True)
    asyncio.run(runtime.prepare(repo, request))
    events = []

    result = asyncio.run(
        runtime.run_independent_review(
            repo,
            request,
            lambda event, details: events.append((event, details)),
        )
    )

    assert result is not None
    assert result["worker_id"] == "independent_reviewer"
    assert result["report"]["summary"] == "Independent review complete"
    assert result["report"]["findings"][0]["severity"] == "low"
    assert reviewer.calls == [repo]
    names = [name for name, _ in events]
    assert names == [
        "reviewer_capacity_claimed",
        "review_started",
        "review_completed",
        "reviewer_capacity_released",
    ]


@pytest.mark.skipif(not Path("/usr/bin/bwrap").is_file(), reason="Bubblewrap required")
def test_reviewer_mutation_is_detected_and_fails_closed(developer_case):
    runtime, _, _ = review_runtime(developer_case, mutate=True)
    request = runtime.request("test", "zelo_core", 30)
    repo = runtime.path(str(uuid4()))
    repo.parent.mkdir(parents=True)
    asyncio.run(runtime.prepare(repo, request))
    events = []

    with pytest.raises(DeveloperError, match="reviewer_boundary_violation"):
        asyncio.run(
            runtime.run_independent_review(
                repo,
                request,
                lambda event, details: events.append((event, details)),
            )
        )

    names = [name for name, _ in events]
    assert "review_failed" in names
    assert names[-1] == "reviewer_capacity_released"
