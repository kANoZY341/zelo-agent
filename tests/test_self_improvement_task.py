"""Persisted v0.11 self-improvement planning task tests."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.developer import DeveloperRuntime
from app.developer_contracts import WorkerResult
from app.gateway import ModelGateway, ModelRequest, ModelResult
from app.main import create_app
from app.models import Approval, utcnow
from app.policy import DomainError, State
from app.runner import Runner
from app.schemas import SelfImprovementRunInput
from app.self_improvement import ImprovementProposal, RepositoryInspector
from app.self_improvement_planner import (
    SelfImprovementPlanner,
    planner_configuration_version,
)
from app.self_improvement_runtime import (
    SelfImprovementPreparation,
    SelfImprovementRunResult,
    SelfImprovementRuntime,
    developer_instruction,
    developer_review_instruction,
)
from app.tasks import TaskService
from app.worker_registry import WorkerRegistry, descriptor_for_reviewer, descriptor_for_worker
from tests.conftest import AUTH
from tests.developer_helpers import FakeWorker, fixture_commit
from tests.test_api import wait_for
from tests.test_self_improvement import proposal_draft
from tests.test_self_improvement_evaluation import developer_result


class PlannerProvider:
    def __init__(self, payload: dict | None = None) -> None:
        self.requests: list[ModelRequest] = []
        self.payload = payload

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        payload = self.payload or {
            "schema_version": "zelo-improvement-proposal-v1",
            "title": "Clarify the self-improvement documentation",
            "problem": "The roadmap can be made easier to verify during future milestones.",
            "evidence": ["app/__init__.py"],
            "expected_benefit": "Make the active milestone easier to inspect and maintain.",
            "affected_components": ["documentation"],
            "estimated_cost": "One bounded developer attempt; no external spend expected.",
            "rollback_approach": "Discard the isolated documentation candidate.",
            "implementation_instruction": (
                "Add a concise documentation note without changing runtime authority."
            ),
            "acceptance_criteria": ["Documentation remains consistent with the canonical roadmap."],
            "risk": "low",
            "required_capabilities": ["zelo_core_development"],
            "scope_paths": ["docs/self-improvement-note.md"],
        }
        return ModelResult(
            text=json.dumps(payload),
            provider="fake",
            model_id=request.model_id,
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        )

    async def models(self) -> list[str]:
        return ["qwen3:4b-instruct"]


class NoFindingReviewWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.review_requests = []
        self.worker_identity = self.worker_identity.model_copy(
            update={"implementation": "reviewer_fake"}
        )

    async def run(self, request, repo):
        self.calls.append(repo)
        self.review_requests.append(request)
        started = datetime.now(UTC)
        return WorkerResult(
            exit_code=0,
            summary=json.dumps(
                {
                    "summary": "Independent review found no concrete issue.",
                    "findings": [],
                },
                separators=(",", ":"),
            ),
            event_count=2,
            usage={"input_tokens": 5, "output_tokens": 3},
            nonfatal_error_items=0,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=0.01,
        )


def test_self_improvement_task_prepares_approval_gated_developer_child(settings, database):
    provider = PlannerProvider()
    developer_worker = FakeWorker()

    with TestClient(
        create_app(settings, provider=provider, developer_worker=developer_worker),
        headers=AUTH,
    ) as client:
        response = client.post(
            "/tasks",
            json={
                "kind": "self_improvement_run",
                "payload": {"goal": "Improve Zelo safely."},
                "timeout_seconds": 120,
            },
        )
        assert response.status_code == 201, response.text
        submitted = response.json()
        assert submitted["kind"] == "self_improvement_run"
        assert submitted["approvals"] == []
        assert submitted["selected_alias"] == "local_general"
        assert submitted["selected_model"] == "qwen3:4b-instruct"

        completed = wait_for(client, submitted["id"], "succeeded")
        result = completed["result"]
        assert result["goal"] == "Improve Zelo safely."
        assert result["proposal"]["goal"] == "Improve Zelo safely."
        assert result["proposal"]["base_sha"] == result["base_sha"]
        assert result["proposal"]["inspection_fingerprint"] == result["inspection_fingerprint"]
        assert result["proposal"]["draft"]["risk"] == "low"
        assert result["planner_alias"] == "local_general"
        assert result["planner_model"] == "qwen3:4b-instruct"
        assert result["total_tokens"] == 18
        assert len(result["developer_action_hash"]) == 64

        serialized = json.dumps(result)
        assert "Zelo fixture" not in serialized
        assert "Canonical Architecture" not in serialized

        child = client.get(f"/tasks/{result['developer_task_id']}").json()
        assert child["kind"] == "developer_run"
        assert child["status"] == "awaiting_approval"
        assert child["approvals"][0]["id"] == result["developer_approval_id"]
        assert child["approvals"][0]["status"] == "pending"
        assert child["developer_spec"]["base_sha"] == result["base_sha"]
        frozen = SelfImprovementRunResult.model_validate(result)
        assert child["payload"]["instruction"] == developer_instruction(frozen)
        assert child["developer_spec"]["review_instruction"] == developer_review_instruction(frozen)
        assert result["proposal"]["proposal_fingerprint"] in child["payload"]["instruction"]
        assert (
            result["proposal"]["proposal_fingerprint"]
            in child["developer_spec"]["review_instruction"]
        )
        assert (
            "Documentation remains consistent with the canonical roadmap."
            in child["developer_spec"]["review_instruction"]
        )
        assert developer_worker.calls == []

        events = client.get(f"/tasks/{submitted['id']}/events").json()
        names = [event["event"] for event in events]
        assert "self_improvement_inspection_started" in names
        assert "self_improvement_inspection_completed" in names
        assert "self_improvement_proposal_created" in names
        assert "self_improvement_developer_prepared" in names
        assert len(provider.requests) == 1


def test_self_improvement_claim_fails_closed_when_planner_config_changes(settings, database):
    provider = PlannerProvider()
    original = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "config-a")
    service = TaskService(database, settings, gateway=original)
    request = SelfImprovementRunInput(
        kind="self_improvement_run",
        payload={"goal": "Improve Zelo safely."},
        timeout_seconds=120,
    )

    task, created = service.submit(request, "self-improvement-config-change")
    assert created is True
    assert task.status == "pending"

    service.gateway = ModelGateway(
        provider,
        "qwen3:4b-instruct",
        "deepseek-r1:7b",
        "config-b",
    )

    assert service.claim() is None
    cancelled = service.get(task.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "planner_selection_changed"
    assert cancelled.attempts == []


def test_self_improvement_cancellation_before_child_creation_stops_cycle(developer_case, database):
    provider = PlannerProvider()
    gateway = ModelGateway(
        provider,
        "qwen3:4b-instruct",
        "deepseek-r1:7b",
        "cancel-config",
    )
    service = TaskService(
        database,
        developer_case.settings,
        gateway=gateway,
        developer_runtime=developer_case,
    )
    parent, created = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=120,
        ),
        "self-improvement-cancel-before-child",
    )
    assert created is True
    execution = service.claim()
    assert execution is not None
    assert execution.task_id == parent.id

    inspection = RepositoryInspector(developer_case.source_repository).inspect()
    proposal = ImprovementProposal.freeze(
        proposal_draft(
            risk="medium",
            evidence=("test_example.py",),
            scope_paths=("test_example.py",),
        ),
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version=planner_configuration_version(
            model_config_version=gateway.config_version,
            alias="local_general",
            model="qwen3:4b-instruct",
        ),
    )
    preparation = SelfImprovementPreparation(
        goal="Improve Zelo safely.",
        base_sha=inspection.base_sha,
        inspection_fingerprint=inspection.fingerprint,
        tracked_path_count=len(inspection.tracked_paths),
        inspected_content_file_count=len(inspection.files),
        content_truncated=inspection.content_truncated,
        proposal=proposal,
        planner_alias="local_general",
        planner_model="qwen3:4b-instruct",
    )

    service.cancel(parent.id)
    linked = service.finish_self_improvement(
        execution,
        preparation,
        developer_instruction(preparation),
    )
    assert linked is None

    cancelled = service.get(parent.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.result is None
    assert cancelled.error == "cancelled_by_operator"
    assert cancelled.attempts[-1].outcome == State.CANCELLED
    assert cancelled.attempts[-1].error == "cancelled_by_operator"
    assert all(task.kind != "developer_run" for task in service.list_tasks(10, 0).items)


def test_self_improvement_timeout_fails_closed_without_developer_child(settings, database):
    provider = PlannerProvider()
    gateway = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "timeout-config")
    service = TaskService(database, settings, gateway=gateway)
    parent, created = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=1,
        ),
        "self-improvement-timeout",
    )
    assert created is True
    execution = service.claim()
    assert execution is not None

    class HangingRuntime:
        async def prepare(self, *_args, **_kwargs):
            await asyncio.Future()

    runner = Runner(
        service,
        gateway=gateway,
        self_improvement_runtime=HangingRuntime(),
    )
    asyncio.run(runner._run(execution))

    failed = service.get(parent.id)
    assert failed.status == State.FAILED
    assert failed.error == "execution_timeout"
    assert failed.result is None
    assert failed.attempts[-1].outcome == State.FAILED
    assert failed.attempts[-1].error == "execution_timeout"
    assert all(task.kind != "developer_run" for task in service.list_tasks(10, 0).items)


def test_self_improvement_startup_recovery_interrupts_running_parent(settings, database):
    provider = PlannerProvider()
    gateway = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "restart-config")
    service = TaskService(database, settings, gateway=gateway)
    parent, created = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=120,
        ),
        "self-improvement-restart",
    )
    assert created is True
    execution = service.claim()
    assert execution is not None
    assert service.get(parent.id).status == State.RUNNING

    service.recover()

    recovered = service.get(parent.id)
    assert recovered.status == State.INTERRUPTED
    assert recovered.result is None
    assert recovered.error == "process_interrupted"
    assert recovered.attempts[-1].outcome == State.INTERRUPTED
    assert recovered.attempts[-1].error == "process_interrupted"
    assert all(task.kind != "developer_run" for task in service.list_tasks(10, 0).items)
    failures = [
        event
        for event in service.events(parent.id, 100, 0)
        if event.event == "self_improvement_failed"
    ]
    assert len(failures) == 1
    assert failures[0].details["reason"] == "process_interrupted"


def test_developer_completion_records_inconclusive_without_independent_review(
    developer_case, database
):
    provider = PlannerProvider()
    gateway = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "config-a")
    service = TaskService(
        database,
        developer_case.settings,
        gateway=gateway,
        developer_runtime=developer_case,
    )
    parent, created = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=120,
        ),
        "self-improvement-evaluation",
    )
    assert created is True
    parent_execution = service.claim()
    assert parent_execution is not None
    assert parent_execution.task_id == parent.id

    inspection = RepositoryInspector(developer_case.source_repository).inspect()
    proposal = ImprovementProposal.freeze(
        proposal_draft(
            risk="medium",
            evidence=("test_example.py",),
            scope_paths=("test_example.py",),
        ),
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version=planner_configuration_version(
            model_config_version=gateway.config_version,
            alias="local_general",
            model="qwen3:4b-instruct",
        ),
    )
    preparation = SelfImprovementPreparation(
        goal="Improve Zelo safely.",
        base_sha=inspection.base_sha,
        inspection_fingerprint=inspection.fingerprint,
        tracked_path_count=len(inspection.tracked_paths),
        inspected_content_file_count=len(inspection.files),
        content_truncated=inspection.content_truncated,
        proposal=proposal,
        planner_alias="local_general",
        planner_model="qwen3:4b-instruct",
    )
    linked = service.finish_self_improvement(
        parent_execution,
        preparation,
        developer_instruction(preparation),
    )
    assert linked is not None

    child = service.get(linked.developer_task_id)
    assert child.developer_spec is not None
    assert child.developer_spec.reviewer is None
    service.decide(child.approvals[0].id, "approve")
    child_execution = service.claim()
    assert child_execution is not None
    assert child_execution.task_id == child.id

    result = developer_result(
        proposal,
        changed_files=["test_example.py"],
        independent_review=None,
    )
    service.finish(
        child_execution,
        State.SUCCEEDED,
        result=result.model_dump(mode="json"),
    )

    evaluations = [
        event
        for event in service.events(parent.id, 100, 0)
        if event.event == "self_improvement_evaluation_completed"
    ]
    assert len(evaluations) == 1
    assert evaluations[0].details["status"] == "inconclusive"
    assert evaluations[0].details["validation_passed"] is True
    assert evaluations[0].details["scope_valid"] is True
    assert evaluations[0].details["independent_review_present"] is False


def test_self_improvement_child_retry_requires_fresh_parent_run(developer_case, database):
    provider = PlannerProvider()
    gateway = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "retry-config")
    service = TaskService(
        database,
        developer_case.settings,
        gateway=gateway,
        developer_runtime=developer_case,
    )
    parent, _ = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=120,
        ),
        "self-improvement-retry-guard",
    )
    parent_execution = service.claim()
    assert parent_execution is not None

    inspection = RepositoryInspector(developer_case.source_repository).inspect()
    proposal = ImprovementProposal.freeze(
        proposal_draft(
            risk="medium",
            evidence=("test_example.py",),
            scope_paths=("test_example.py",),
        ),
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version=planner_configuration_version(
            model_config_version=gateway.config_version,
            alias="local_general",
            model="qwen3:4b-instruct",
        ),
    )
    preparation = SelfImprovementPreparation(
        goal="Improve Zelo safely.",
        base_sha=inspection.base_sha,
        inspection_fingerprint=inspection.fingerprint,
        tracked_path_count=len(inspection.tracked_paths),
        inspected_content_file_count=len(inspection.files),
        content_truncated=inspection.content_truncated,
        proposal=proposal,
        planner_alias="local_general",
        planner_model="qwen3:4b-instruct",
    )
    linked = service.finish_self_improvement(
        parent_execution,
        preparation,
        developer_instruction(preparation),
    )
    assert linked is not None

    child = service.get(linked.developer_task_id)
    service.decide(child.approvals[0].id, "approve")
    child_execution = service.claim()
    assert child_execution is not None
    service.finish(child_execution, State.FAILED, error="fixture_failure")

    with pytest.raises(DomainError, match="fresh self-improvement run"):
        service.retry(child.id)


def test_self_improvement_full_cycle_smoke_stops_after_accepted_candidate(developer_case, database):
    source = developer_case.source_repository
    docs = source / "docs"
    docs.mkdir()
    (docs / "evidence.md").write_text(
        "The fixture needs one bounded generated documentation artifact.\n",
        encoding="utf-8",
    )
    fixture_commit(source)

    proposal_payload = {
        "schema_version": "zelo-improvement-proposal-v1",
        "title": "Add bounded self-improvement smoke artifact",
        "problem": "The fixture has evidence but no generated smoke artifact.",
        "evidence": ["docs/evidence.md"],
        "expected_benefit": "Prove the complete bounded v0.11 cycle.",
        "affected_components": ["documentation"],
        "estimated_cost": "One local bounded developer attempt; no external spend.",
        "rollback_approach": "Discard the isolated candidate workspace.",
        "implementation_instruction": (
            "Create docs/self-improvement-smoke.md with a short proof note."
        ),
        "acceptance_criteria": [
            "The new documentation artifact exists and trusted validation remains green."
        ],
        "risk": "low",
        "required_capabilities": ["zelo_core_development"],
        "scope_paths": ["docs/self-improvement-smoke.md"],
    }
    provider = PlannerProvider(proposal_payload)
    gateway = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", "smoke-config")

    producer = FakeWorker()

    async def implement(_request, repo):
        target = repo / "docs" / "self-improvement-smoke.md"
        target.write_text("v0.11 bounded cycle candidate\n", encoding="utf-8")

    producer.action = implement
    reviewer = NoFindingReviewWorker()
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
    developer_runtime = DeveloperRuntime(
        developer_case.settings,
        producer,
        source,
        registry,
    )
    self_improvement_runtime = SelfImprovementRuntime(
        RepositoryInspector(source),
        SelfImprovementPlanner(gateway),
    )
    service = TaskService(
        database,
        developer_case.settings,
        gateway=gateway,
        developer_runtime=developer_runtime,
    )
    runner = Runner(
        service,
        gateway=gateway,
        developer_runtime=developer_runtime,
        self_improvement_runtime=self_improvement_runtime,
    )

    parent, created = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=120,
        ),
        "self-improvement-full-cycle-smoke",
    )
    assert created is True
    parent_execution = service.claim()
    assert parent_execution is not None
    asyncio.run(runner._run(parent_execution))

    prepared_parent = service.get(parent.id)
    assert prepared_parent.status == State.SUCCEEDED
    assert prepared_parent.result is not None
    child = service.get(prepared_parent.result["developer_task_id"])
    assert child.status == State.AWAITING_APPROVAL
    assert producer.calls == []

    service.decide(child.approvals[0].id, "approve")
    child_execution = service.claim()
    assert child_execution is not None
    asyncio.run(runner._run(child_execution))

    completed_child = service.get(child.id)
    assert completed_child.status == State.SUCCEEDED
    assert completed_child.result is not None
    assert completed_child.result["validation_passed"] is True
    assert "docs/self-improvement-smoke.md" in completed_child.result["changed_files"]
    assert completed_child.result["ignored_generated_files"]
    assert completed_child.result["independent_review"]["report"]["findings"] == []

    evaluations = [
        event
        for event in service.events(parent.id, 100, 0)
        if event.event == "self_improvement_evaluation_completed"
    ]
    assert len(evaluations) == 1
    evaluation = evaluations[0].details
    assert evaluation["status"] == "accepted_candidate"
    assert evaluation["changed_files"] == ["docs/self-improvement-smoke.md"]
    assert len(evaluation["final_diff_sha256"]) == 64
    assert evaluation["validation_passed"] is True
    assert evaluation["acceptance_criteria_proven"] is True
    assert len(reviewer.review_requests) == 1
    assert proposal_payload["acceptance_criteria"][0] in reviewer.review_requests[0].instruction

    tasks = service.list_tasks(10, 0).items
    assert {task.kind for task in tasks} == {"self_improvement_run", "developer_run"}
    assert not any(task.kind in {"promotion_run", "merge_run", "release_run"} for task in tasks)


def _prepare_bound_self_improvement_child(developer_case, database, key):
    provider = PlannerProvider()
    gateway = ModelGateway(provider, "qwen3:4b-instruct", "deepseek-r1:7b", key)
    service = TaskService(
        database,
        developer_case.settings,
        gateway=gateway,
        developer_runtime=developer_case,
    )
    parent, created = service.submit(
        SelfImprovementRunInput(
            kind="self_improvement_run",
            payload={"goal": "Improve Zelo safely."},
            timeout_seconds=120,
        ),
        key,
    )
    assert created is True
    parent_execution = service.claim()
    assert parent_execution is not None

    inspection = RepositoryInspector(developer_case.source_repository).inspect()
    proposal = ImprovementProposal.freeze(
        proposal_draft(
            risk="medium",
            evidence=("test_example.py",),
            scope_paths=("test_example.py",),
        ),
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version=planner_configuration_version(
            model_config_version=gateway.config_version,
            alias="local_general",
            model="qwen3:4b-instruct",
        ),
    )
    preparation = SelfImprovementPreparation(
        goal="Improve Zelo safely.",
        base_sha=inspection.base_sha,
        inspection_fingerprint=inspection.fingerprint,
        tracked_path_count=len(inspection.tracked_paths),
        inspected_content_file_count=len(inspection.files),
        content_truncated=inspection.content_truncated,
        proposal=proposal,
        planner_alias="local_general",
        planner_model="qwen3:4b-instruct",
    )
    linked = service.finish_self_improvement(
        parent_execution,
        preparation,
        developer_instruction(preparation),
    )
    assert linked is not None
    child = service.get(linked.developer_task_id)
    return service, parent, child, proposal


def test_self_improvement_child_expired_approval_cannot_execute(developer_case, database):
    service, _, child, _ = _prepare_bound_self_improvement_child(
        developer_case,
        database,
        "self-improvement-expired-child",
    )
    approval_id = child.approvals[0].id
    with database.transaction() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        approval.expires_at = utcnow() - timedelta(seconds=1)

    with pytest.raises(DomainError):
        service.decide(approval_id, "approve")

    cancelled = service.get(child.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "approval_expired"
    assert cancelled.attempts == []


def test_self_improvement_child_worker_change_invalidates_approval(developer_case, database):
    service, _, child, _ = _prepare_bound_self_improvement_child(
        developer_case,
        database,
        "self-improvement-worker-change",
    )
    replacement = FakeWorker()
    replacement.worker_identity = replacement.worker_identity.model_copy(
        update={"config_version": "changed-after-preparation"}
    )
    developer_case.worker = replacement

    with pytest.raises(DomainError):
        service.decide(child.approvals[0].id, "approve")

    cancelled = service.get(child.id)
    assert cancelled.status == State.CANCELLED
    assert cancelled.error == "approval_invalidated"
    assert cancelled.approvals[0].status == "invalidated"
    assert replacement.calls == []


def test_self_improvement_real_validation_failure_is_rejected(developer_case, database):
    service, parent, child, _ = _prepare_bound_self_improvement_child(
        developer_case,
        database,
        "self-improvement-validation-failure",
    )

    async def break_validation(_request, repo):
        target = repo / "test_example.py"
        target.write_text("def test_example():\n    assert False\n")

    developer_case.worker.action = break_validation
    service.decide(child.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    asyncio.run(Runner(service, developer_runtime=developer_case)._run(execution))

    completed = service.get(child.id)
    assert completed.status == State.FAILED
    assert completed.result is not None
    assert completed.result["validation_passed"] is False

    evaluations = [
        event
        for event in service.events(parent.id, 100, 0)
        if event.event == "self_improvement_evaluation_completed"
    ]
    assert len(evaluations) == 1
    assert evaluations[0].details["status"] == "rejected_candidate"
    assert evaluations[0].details["validation_passed"] is False
