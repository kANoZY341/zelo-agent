"""Persisted v0.11 self-improvement planning task tests."""

import json

from fastapi.testclient import TestClient

from app.gateway import ModelGateway, ModelRequest, ModelResult
from app.main import create_app
from app.policy import State
from app.schemas import SelfImprovementRunInput
from app.self_improvement import ImprovementProposal, RepositoryInspector
from app.self_improvement_planner import planner_configuration_version
from app.self_improvement_runtime import SelfImprovementPreparation, developer_instruction
from app.tasks import TaskService
from tests.conftest import AUTH
from tests.developer_helpers import FakeWorker
from tests.test_api import wait_for
from tests.test_self_improvement import proposal_draft
from tests.test_self_improvement_evaluation import developer_result


class PlannerProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        payload = {
            "schema_version": "zelo-improvement-proposal-v1",
            "title": "Clarify the self-improvement documentation",
            "problem": "The roadmap can be made easier to verify during future milestones.",
            "evidence": ["app/__init__.py"],
            "expected_benefit": "Make the active milestone easier to inspect and maintain.",
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
        assert result["proposal"]["proposal_fingerprint"] in child["payload"]["instruction"]
        assert result["proposal"]["proposal_fingerprint"] in child["developer_spec"]["review_instruction"]
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
