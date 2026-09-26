import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.gateway import ModelGateway, ModelGatewayError, ModelResult
from app.main import create_app
from tests.conftest import AUTH
from tests.test_api import wait_for


class FakeProvider:
    def __init__(self, result: ModelResult | None = None, error: str | None = None) -> None:
        self.result = result
        self.error = error
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise ModelGatewayError(self.error)
        return self.result

    async def models(self):
        return ["qwen3:4b-instruct"]


def ai_task(client: TestClient, prompt: str = "Say hello", profile: str = "general") -> dict:
    response = client.post(
        "/tasks",
        json={"kind": "ai_prompt", "payload": {"prompt": prompt, "profile": profile}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_router_supports_dedicated_mind_model() -> None:
    gateway = ModelGateway(
        FakeProvider(),
        "qwen3:4b-instruct",
        "deepseek-r1:7b",
        "1",
        mind_model="qwen3.5:4b",
    )
    assert gateway.select("mind") == ("local_mind", "qwen3.5:4b")

    fallback = ModelGateway(
        FakeProvider(),
        "qwen3:4b-instruct",
        "deepseek-r1:7b",
        "1",
    )
    assert fallback.select("mind") == ("local_mind", "qwen3:4b-instruct")


def test_router_aliases(settings, database) -> None:
    provider = FakeProvider()
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        task = ai_task(client)
        assert task["selected_alias"] == "local_general"
        assert task["selected_model"] == "qwen3:4b-instruct"
        reasoning = ai_task(client, profile="reasoning")
        assert reasoning["selected_alias"] == "local_reasoning"
        assert reasoning["selected_model"] == "deepseek-r1:7b"


def test_ai_call_requires_approval_and_persists_metadata(settings, database) -> None:
    provider = FakeProvider(
        ModelResult(
            text="hello",
            provider="ollama",
            model_id="qwen3:4b-instruct",
            prompt_tokens=4,
            completion_tokens=2,
            total_tokens=6,
            prompt_eval_duration_ns=100,
            generation_duration_ns=1_000_000_000,
            total_duration_ns=1_100_000_000,
            completed_at=datetime.now(UTC),
        )
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        task = ai_task(client)
        assert provider.requests == []
        assert (
            client.post(
                f"/approvals/{task['approvals'][0]['id']}/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        completed = wait_for(client, task["id"], "succeeded")
        assert completed["result"]["text"] == "hello"
        assert completed["result"]["usage"]["total_tokens"] == 6
        assert completed["result"]["timing"]["approx_tokens_per_second"] == 2.0
        assert len(provider.requests) == 1
        events = [event["event"] for event in client.get(f"/tasks/{task['id']}/events").json()]
        assert "model_selected" in events
        assert "model_execution_started" in events
        assert "model_execution_completed" in events


def test_provider_error_is_persisted_without_fallback(settings, database) -> None:
    provider = FakeProvider(error="model_unavailable")
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        task = ai_task(client)
        client.post(
            f"/approvals/{task['approvals'][0]['id']}/decision", json={"decision": "approve"}
        )
        failed = wait_for(client, task["id"], "failed")
        assert failed["error"] == "model_unavailable"
        assert len(provider.requests) == 1
        assert "model_execution_failed" in [
            event["event"] for event in client.get(f"/tasks/{task['id']}/events").json()
        ]


def test_provider_timeout_is_failed_attempt(settings, database) -> None:
    class SlowProvider(FakeProvider):
        async def generate(self, request):
            self.requests.append(request)
            await asyncio.sleep(1)
            return self.result

    provider = SlowProvider()
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        task = client.post(
            "/tasks",
            json={
                "kind": "ai_prompt",
                "payload": {"prompt": "wait", "profile": "general"},
                "timeout_seconds": 0.05,
            },
        ).json()
        client.post(
            f"/approvals/{task['approvals'][0]['id']}/decision", json={"decision": "approve"}
        )
        failed = wait_for(client, task["id"], "failed")
        assert failed["error"] == "model_timeout"
        assert failed["attempts"][0]["outcome"] == "failed"
