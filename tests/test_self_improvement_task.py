"""Persisted v0.11 self-improvement planning task tests."""

import json

from fastapi.testclient import TestClient

from app.gateway import ModelGateway, ModelRequest, ModelResult
from app.main import create_app
from app.schemas import SelfImprovementRunInput
from app.tasks import TaskService
from tests.conftest import AUTH
from tests.developer_helpers import FakeWorker
from tests.test_api import wait_for


class PlannerProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        payload = {
            "schema_version": "zelo-improvement-proposal-v1",
            "title": "Clarify the self-improvement documentation",
            "problem": "The roadmap can be made easier to verify during future milestones.",
            "evidence": ["README.md"],
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
        assert (
            result["proposal"]["proposal_fingerprint"]
            in child["payload"]["instruction"]
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
