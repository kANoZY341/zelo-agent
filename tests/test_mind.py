"""Early Talk-to-Zelo conversation -> Core task -> result proof."""

import json

from fastapi.testclient import TestClient

from app.gateway import ModelResult
from app.main import create_app
from tests.conftest import AUTH
from tests.test_api import wait_for


class MindProvider:
    def __init__(self, *actions) -> None:
        self.actions = list(actions)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        action = self.actions.pop(0)
        return ModelResult(
            text=action if isinstance(action, str) else json.dumps(action),
            provider="fake",
            model_id=request.model_id,
            prompt_tokens=7,
            completion_tokens=5,
            total_tokens=12,
        )

    async def models(self):
        return ["qwen3:4b-instruct", "deepseek-r1:7b"]


def reply(content: str) -> dict:
    return {"type": "reply", "content": content}


def core_task(kind: str, payload: dict, content: str = "I will send that to Core.") -> dict:
    return {
        "type": "core_task",
        "content": content,
        "task": {"kind": kind, "payload": payload},
    }


def new_conversation(client: TestClient) -> dict:
    response = client.post("/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_direct_conversation_persists_identity_and_history(settings, database):
    provider = MindProvider(reply("We can plan that."), reply("Continuing from the plan."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        first = client.post(
            f"/conversations/{conversation['id']}/turns",
            json={"text": "Help me plan the next Zelo step."},
        )
        assert first.status_code == 201, first.text
        assert first.json()["assistant_content"] == "We can plan that."
        assert first.json()["core_task"] is None

        second = client.post(
            f"/conversations/{conversation['id']}/turns",
            json={"text": "Continue."},
        )
        assert second.status_code == 201, second.text
        assert second.json()["number"] == 2

        messages = provider.requests[1].messages
        assert [message.role for message in messages] == ["user", "assistant", "user"]
        assert messages[0].content == "Help me plan the next Zelo step."
        assert messages[1].content == "We can plan that."
        assert messages[2].content == "Continue."
        assert "Zelo Mind" in provider.requests[0].system_prompt

        stored = client.get(f"/conversations/{conversation['id']}").json()
        assert stored["title"] == "Help me plan the next Zelo step."
        assert [turn["number"] for turn in stored["turns"]] == [1, 2]


def test_conversation_core_task_reaches_verified_result(settings, database):
    provider = MindProvider(
        core_task(
            "text_stats",
            {"text": "hello from Zelo"},
            "I am sending that count to Zelo Core.",
        ),
        reply("Core finished the count."),
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = client.post(
            f"/conversations/{conversation['id']}/turns",
            json={"text": "Count the words in hello from Zelo."},
            headers={"Idempotency-Key": "count-words-1"},
        )
        assert response.status_code == 201, response.text
        turn = response.json()
        assert turn["core_task"]["kind"] == "text_stats"
        task_id = turn["core_task"]["id"]

        completed = wait_for(client, task_id, "succeeded")
        assert completed["result"] == {"characters": 15, "words": 3, "lines": 1}

        stored = client.get(f"/conversations/{conversation['id']}").json()
        linked = stored["turns"][0]["core_task"]
        assert linked["id"] == task_id
        assert linked["status"] == "succeeded"
        assert linked["result"]["words"] == 3

        follow_up = client.post(
            f"/conversations/{conversation['id']}/turns",
            json={"text": "What happened?"},
        )
        assert follow_up.status_code == 201, follow_up.text
        messages = provider.requests[1].messages
        assert [message.role for message in messages] == [
            "user",
            "assistant",
            "tool",
            "user",
        ]
        task_state = json.loads(messages[2].content)
        assert task_state["type"] == "core_task_state"
        assert task_state["task_id"] == task_id
        assert task_state["status"] == "succeeded"
        assert json.loads(task_state["result_json"])["words"] == 3
        assert task_state["result_truncated"] is False


def test_turn_idempotency_reuses_same_conversation_action(settings, database):
    provider = MindProvider(core_task("echo", {"text": "same"}))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        path = f"/conversations/{conversation['id']}/turns"
        headers = {"Idempotency-Key": "same-turn"}
        first = client.post(path, json={"text": "Do the same thing."}, headers=headers)
        second = client.post(path, json={"text": "Do the same thing."}, headers=headers)

        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["core_task"]["id"] == second.json()["core_task"]["id"]
        assert len(provider.requests) == 1


def test_turn_idempotency_rejects_different_message(settings, database):
    provider = MindProvider(reply("First response."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        path = f"/conversations/{conversation['id']}/turns"
        headers = {"Idempotency-Key": "same-key"}
        first = client.post(path, json={"text": "First message."}, headers=headers)
        second = client.post(path, json={"text": "Different message."}, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 409
        assert len(provider.requests) == 1


def test_mind_cannot_request_release_authority(settings, database):
    provider = MindProvider(
        {
            "type": "core_task",
            "content": "Trying release.",
            "task": {
                "kind": "release_run",
                "payload": {
                    "merge_task_id": "00000000-0000-0000-0000-000000000000",
                    "tag": "v0.12.0",
                },
            },
        },
        reply("I cannot request that through this conversation contract."),
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = client.post(
            f"/conversations/{conversation['id']}/turns",
            json={"text": "Release it."},
        )

        assert response.status_code == 201, response.text
        assert response.json()["core_task"] is None
        assert len(provider.requests) == 2
        assert client.get("/tasks").json()["total"] == 0


def test_conversation_routes_require_operator_auth(settings, database):
    provider = MindProvider(reply("unused"))
    with TestClient(create_app(settings, provider=provider)) as client:
        assert client.post("/conversations", json={}).status_code == 401
        assert client.get("/conversations").status_code == 401
        assert client.get("/conversations/missing").status_code == 401
        assert (
            client.post("/conversations/missing/turns", json={"text": "hello"}).status_code == 401
        )
