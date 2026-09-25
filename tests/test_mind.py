"""Early Talk-to-Zelo conversation -> Core task -> result proof."""

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.gateway import ModelResult
from app.main import create_app
from app.models import Approval, TaskAttempt, utcnow
from app.policy import DomainError
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


class BlockingMindProvider:
    def __init__(self) -> None:
        self.requests = []
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None
        self.active = 0
        self.max_active = 0

    async def generate(self, request):
        if self.entered is None or self.release is None:
            raise RuntimeError("blocking provider events not configured")
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        try:
            await self.release.wait()
            return ModelResult(
                text=json.dumps(reply("Capacity test response.")),
                provider="fake",
                model_id=request.model_id,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )
        finally:
            self.active -= 1

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


def send_turn(
    client: TestClient,
    conversation_id: str,
    text: str,
    key: str,
):
    return client.post(
        f"/conversations/{conversation_id}/turns",
        json={"text": text},
        headers={"Idempotency-Key": key},
    )


def test_direct_conversation_persists_identity_and_history(settings, database):
    provider = MindProvider(reply("We can plan that."), reply("Continuing from the plan."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        first = send_turn(
            client,
            conversation["id"],
            "Help me plan the next Zelo step.",
            "plan-1",
        )
        assert first.status_code == 201, first.text
        assert first.json()["assistant_content"] == "We can plan that."
        assert first.json()["core_task"] is None

        second = send_turn(client, conversation["id"], "Continue.", "plan-2")
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
        assert stored["turn_total"] == 2
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
        response = send_turn(
            client,
            conversation["id"],
            "Count the words in hello from Zelo.",
            "count-words-1",
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

        follow_up = send_turn(
            client,
            conversation["id"],
            "What happened?",
            "count-words-2",
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
        first = send_turn(client, conversation["id"], "Do the same thing.", "same-turn")
        second = send_turn(client, conversation["id"], "Do the same thing.", "same-turn")

        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["core_task"]["id"] == second.json()["core_task"]["id"]
        assert len(provider.requests) == 1
        assert client.get("/tasks").json()["total"] == 1


def test_turn_idempotency_rejects_different_message(settings, database):
    provider = MindProvider(reply("First response."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        first = send_turn(client, conversation["id"], "First message.", "same-key")
        second = send_turn(client, conversation["id"], "Different message.", "same-key")

        assert first.status_code == 201
        assert second.status_code == 409
        assert len(provider.requests) == 1


def test_core_task_recovers_after_crash_between_submit_and_link(
    settings,
    database,
    monkeypatch,
):
    provider = MindProvider(core_task("echo", {"text": "recover me"}))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        mind = client.app.state.mind
        original_link = mind._link_core_task

        def crash_after_submit(turn_id: str, task_id: str):
            raise RuntimeError("injected crash after durable Core submit")

        monkeypatch.setattr(mind, "_link_core_task", crash_after_submit)
        with pytest.raises(RuntimeError, match="injected crash"):
            send_turn(
                client,
                conversation["id"],
                "Run one recoverable task.",
                "recover-crash-1",
            )

        assert len(provider.requests) == 1
        tasks_after_crash = client.get("/tasks").json()
        assert tasks_after_crash["total"] == 1
        original_task_id = tasks_after_crash["items"][0]["id"]

        stored_after_crash = client.get(f"/conversations/{conversation['id']}").json()
        assert stored_after_crash["turn_total"] == 1
        assert stored_after_crash["turns"][0]["core_task"] is None

        monkeypatch.setattr(mind, "_link_core_task", original_link)
        recovered = send_turn(
            client,
            conversation["id"],
            "Run one recoverable task.",
            "recover-crash-1",
        )

        assert recovered.status_code == 201, recovered.text
        assert recovered.json()["core_task"]["id"] == original_task_id
        assert len(provider.requests) == 1
        assert client.get("/tasks").json()["total"] == 1


def test_conversation_reads_are_paginated_and_list_is_summary(settings, database):
    provider = MindProvider(reply("one"), reply("two"), reply("three"))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        for number in range(1, 4):
            response = send_turn(
                client,
                conversation["id"],
                f"message {number}",
                f"page-{number}",
            )
            assert response.status_code == 201

        page = client.get(f"/conversations/{conversation['id']}?turn_limit=1&turn_offset=1").json()
        assert page["turn_limit"] == 1
        assert page["turn_offset"] == 1
        assert page["turn_total"] == 3
        assert [turn["number"] for turn in page["turns"]] == [2]

        listing = client.get("/conversations").json()
        assert listing["total"] == 1
        assert listing["items"][0]["turn_count"] == 3
        assert "turns" not in listing["items"][0]


def test_conversation_task_projection_bounds_attempt_and_approval_histories(
    settings,
    database,
):
    provider = MindProvider(
        core_task("echo", {"text": "bounded"}),
        reply("I can see the bounded Core task state."),
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        created = send_turn(
            client,
            conversation["id"],
            "Create a bounded task projection.",
            "bounded-task-1",
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["core_task"]["id"]
        wait_for(client, task_id, "succeeded")

        now = utcnow()
        with database.transaction() as session:
            for number in range(2, 22):
                session.add(
                    TaskAttempt(
                        task_id=task_id,
                        number=number,
                        started_at=now,
                        ended_at=now,
                        outcome="succeeded",
                        result={"synthetic": number},
                    )
                )
            for number in range(30):
                session.add(
                    Approval(
                        task_id=task_id,
                        action_version=1,
                        action_hash="a" * 64,
                        status="rejected",
                        created_at=now + timedelta(microseconds=number),
                        expires_at=now + timedelta(hours=1),
                        decided_at=now,
                    )
                )

        stored = client.get(
            f"/conversations/{conversation['id']}?turn_limit=1"
        ).json()
        projected = stored["turns"][0]["core_task"]
        assert projected["attempt_count"] == 21
        assert projected["approval_count"] == 30
        assert projected["latest_attempt_outcome"] == "succeeded"
        assert projected["latest_approval_status"] == "rejected"
        assert "attempts" not in projected
        assert "approvals" not in projected

        follow_up = send_turn(
            client,
            conversation["id"],
            "What is the current task state?",
            "bounded-task-2",
        )
        assert follow_up.status_code == 201, follow_up.text
        task_state = json.loads(provider.requests[1].messages[2].content)
        assert task_state["attempt_count"] == 21
        assert task_state["approval_count"] == 30
        assert "attempts" not in task_state
        assert "approvals" not in task_state


def test_mind_generation_has_process_wide_admission_limit(settings, database):
    provider = BlockingMindProvider()
    configured = settings.model_copy(
        update={
            "mind_max_concurrency": 1,
            "mind_admission_timeout_seconds": 0.01,
        }
    )
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        mind = client.app.state.mind
        first = mind.create()
        second = mind.create()

        async def scenario() -> None:
            provider.entered = asyncio.Event()
            provider.release = asyncio.Event()
            first_call = asyncio.create_task(mind.send_turn(first.id, "first", "capacity-1"))
            await provider.entered.wait()
            with pytest.raises(DomainError, match="capacity reached") as error:
                await mind.send_turn(second.id, "second", "capacity-2")
            assert error.value.status_code == 503
            provider.release.set()
            await first_call

        asyncio.run(scenario())

        assert provider.max_active == 1
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
        response = send_turn(client, conversation["id"], "Release it.", "release-denied")

        assert response.status_code == 201, response.text
        assert response.json()["core_task"] is None
        assert len(provider.requests) == 2
        assert client.get("/tasks").json()["total"] == 0


def test_turn_requires_idempotency_key(settings, database):
    provider = MindProvider(reply("unused"))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = client.post(
            f"/conversations/{conversation['id']}/turns",
            json={"text": "hello"},
        )
        assert response.status_code == 422
        assert len(provider.requests) == 0


def test_conversation_routes_require_operator_auth(settings, database):
    provider = MindProvider(reply("unused"))
    with TestClient(create_app(settings, provider=provider)) as client:
        assert client.post("/conversations", json={}).status_code == 401
        assert client.get("/conversations").status_code == 401
        assert client.get("/conversations/missing").status_code == 401
        assert (
            client.post(
                "/conversations/missing/turns",
                json={"text": "hello"},
                headers={"Idempotency-Key": "unauthorized"},
            ).status_code
            == 401
        )
