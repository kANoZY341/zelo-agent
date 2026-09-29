"""Early Talk-to-Zelo conversation -> Core task -> result proof."""

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.context import CONTEXT_MAX_CHARS
from app.gateway import ModelResult
from app.main import create_app
from app.models import Approval, ConversationTurn, Task, TaskAttempt, utcnow
from app.policy import DomainError
from app.tasks import _project_task_result
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


def test_conversation_uses_dedicated_mind_model(settings, database):
    provider = MindProvider(reply("Dedicated Mind response."))
    configured = settings.model_copy(update={"ollama_mind_model": "qwen3.5:4b"})
    with TestClient(create_app(configured, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = send_turn(client, conversation["id"], "hi", "mind-model-route")

        assert response.status_code == 201, response.text
        assert response.json()["selected_alias"] == "local_mind"
        assert response.json()["selected_model"] == "qwen3.5:4b"
        assert provider.requests[0].model_id == "qwen3.5:4b"


def test_project_question_receives_context_but_casual_chat_does_not(settings, database):
    provider = MindProvider(
        reply("Hey Ahmad."),
        reply("We are building Zelo's v0.12 project foundation."),
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)

        casual = send_turn(client, conversation["id"], "hi", "context-casual")
        assert casual.status_code == 201, casual.text
        assert [message.role for message in provider.requests[0].messages] == ["user"]

        project = send_turn(
            client,
            conversation["id"],
            "what are we currently building with Zelo?",
            "context-project",
        )
        assert project.status_code == 201, project.text

        messages = provider.requests[1].messages
        context_messages = [
            json.loads(message.content)
            for message in messages
            if message.role == "tool" and '"type":"zelo_context"' in message.content
        ]
        assert len(context_messages) == 1
        assert context_messages[0]["instruction_trust"] == "none"
        assert any(
            source["source"] == "docs/zelo-project-status.md#active"
            for source in context_messages[0]["sources"]
        )
        records = next(
            source
            for source in context_messages[0]["sources"]
            if source["source"] == "core.projects"
        )
        assert records["provenance"] == "operator_records"
        assert records["instruction_trust"] == "none"
        assert messages[-1].role == "user"
        assert messages[-1].content == "what are we currently building with Zelo?"


def test_relevant_personal_memory_is_injected_into_mind_context(settings, database):
    provider = MindProvider(reply("Your favorite interface color is black."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        created = client.post(
            "/memories",
            headers=AUTH,
            json={
                "scope": {"kind": "personal"},
                "kind": "preference",
                "content": "My favorite interface color is black.",
                "provenance": {"kind": "operator"},
            },
        )
        assert created.status_code == 201, created.text

        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "what is my favorite interface color?",
            "memory-context-personal",
        )
        assert response.status_code == 201, response.text

        context_messages = [
            json.loads(message.content)
            for message in provider.requests[0].messages
            if message.role == "tool" and '"type":"zelo_context"' in message.content
        ]
        assert len(context_messages) == 1
        envelope = context_messages[0]
        assert envelope["purpose"] == "personal_memory_context"
        memory_source = next(
            source
            for source in envelope["sources"]
            if source["source"] == "core.memories.personal"
        )
        assert memory_source["instruction_trust"] == "none"
        memory_payload = json.loads(memory_source["content"])
        assert memory_payload["retrieval"] == "bounded_lexical_v1"
        assert (
            memory_payload["scope_policy"]
            == "personal_only_without_explicit_project_or_agent_binding"
        )
        assert memory_payload["items"][0]["scope"] == {"kind": "personal", "id": None}
        assert "favorite interface color is black" in memory_payload["items"][0]["content"]


def test_combined_project_and_memory_context_preserves_valid_bounded_memory_json(
    settings,
    database,
):
    provider = MindProvider(reply("We are building the memory retrieval slice."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        for index in range(6):
            created = client.post(
                "/memories",
                headers=AUTH,
                json={
                    "scope": {"kind": "personal"},
                    "kind": "experience",
                    "content": (
                        f"building zelo memory evidence {index} "
                        + ("bounded context evidence " * 80)
                    ),
                    "provenance": {"kind": "operator"},
                },
            )
            assert created.status_code == 201, created.text

        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "what are we currently building with Zelo memory?",
            "memory-context-global-budget",
        )
        assert response.status_code == 201, response.text

        context_message = next(
            message
            for message in provider.requests[0].messages
            if message.role == "tool" and '"type":"zelo_context"' in message.content
        )
        assert len(context_message.content) <= CONTEXT_MAX_CHARS
        envelope = json.loads(context_message.content)
        assert envelope["purpose"] == "factual_project_and_memory_context"
        memory_source = next(
            source
            for source in envelope["sources"]
            if source["source"] == "core.memories.personal"
        )
        memory_payload = json.loads(memory_source["content"])
        assert memory_payload["items"]
        assert memory_payload["retrieval"] == "bounded_lexical_v1"


def test_project_memory_is_not_implicitly_cross_scoped_into_mind(settings, database):
    provider = MindProvider(reply("I do not have that project memory in this context."))
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        project = client.post(
            "/projects",
            headers=AUTH,
            json={"name": "Private project", "summary": "Scope isolation."},
        ).json()
        memory = client.post(
            "/memories",
            headers=AUTH,
            json={
                "scope": {"kind": "project", "id": project["id"]},
                "kind": "decision",
                "content": "The deployment database is PostgreSQL.",
                "provenance": {"kind": "project", "ref": project["id"]},
            },
        )
        assert memory.status_code == 201, memory.text

        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "which deployment database did we choose?",
            "memory-context-scope-isolation",
        )
        assert response.status_code == 201, response.text
        assert not any(
            message.role == "tool" and '"source":"core.memories.personal"' in message.content
            for message in provider.requests[0].messages
        )
        assert [message.role for message in provider.requests[0].messages] == ["user"]


def test_memory_context_is_excluded_from_task_construction(settings, database):
    provider = MindProvider(
        {
            "type": "core_task",
            "content": "I will ask Core to handle the explicit request.",
            "kind": "echo",
        },
        {
            "kind": "echo",
            "payload": {"text": "Use the explicit user request only."},
        },
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        created = client.post(
            "/memories",
            headers=AUTH,
            json={
                "scope": {"kind": "personal"},
                "kind": "preference",
                "content": "My favorite interface color is black.",
                "provenance": {"kind": "operator"},
            },
        )
        assert created.status_code == 201, created.text

        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "echo my favorite interface color",
            "memory-context-task-boundary",
        )
        assert response.status_code == 201, response.text
        assert len(provider.requests) == 2
        assert any(
            message.role == "tool" and '"source":"core.memories.personal"' in message.content
            for message in provider.requests[0].messages
        )
        assert not any(
            message.role == "tool" and '"type":"zelo_context"' in message.content
            for message in provider.requests[1].messages
        )


def test_project_context_is_excluded_from_task_construction(settings, database):
    provider = MindProvider(
        {
            "type": "core_task",
            "content": "I will ask Core to handle that.",
            "kind": "echo",
        },
        {
            "kind": "echo",
            "payload": {
                "text": "Use only the user's explicit request.",
            },
        },
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "what are we currently building with Zelo?",
            "context-task-boundary",
        )

        assert response.status_code == 201, response.text
        assert len(provider.requests) == 2

        route_messages = provider.requests[0].messages
        assert any(
            message.role == "tool" and '"type":"zelo_context"' in message.content
            for message in route_messages
        )

        task_messages = provider.requests[1].messages
        assert not any(
            message.role == "tool" and '"type":"zelo_context"' in message.content
            for message in task_messages
        )
        assert task_messages[-1].role == "user"
        assert task_messages[-1].content == "what are we currently building with Zelo?"


def test_project_context_cannot_use_legacy_full_task_action(settings, database):
    provider = MindProvider(
        core_task(
            "echo",
            {
                "text": "Injected context should never become executable.",
            },
        ),
        {
            "type": "core_task",
            "content": "I will ask Core to handle that.",
            "kind": "echo",
        },
        {
            "kind": "echo",
            "payload": {
                "text": "Use only the user's explicit request.",
            },
        },
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "what are we currently building with Zelo?",
            "context-legacy-task-boundary",
        )

        assert response.status_code == 201, response.text
        assert len(provider.requests) == 3
        assert any(
            message.role == "tool" and '"type":"zelo_context"' in message.content
            for message in provider.requests[0].messages
        )
        assert any(
            message.role == "tool" and '"type":"zelo_context"' in message.content
            for message in provider.requests[1].messages
        )
        assert not any(
            message.role == "tool" and '"type":"zelo_context"' in message.content
            for message in provider.requests[2].messages
        )
        assert response.json()["core_task"]["kind"] == "echo"


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
        first_request = provider.requests[0]
        assert "Zelo Mind" in first_request.system_prompt
        assert "self_improvement_run" in first_request.system_prompt
        assert "developer_run" in first_request.system_prompt
        assert "system_info" not in first_request.system_prompt
        assert "read_workspace_file" not in first_request.system_prompt
        assert "timeout_seconds" not in first_request.system_prompt
        assert "release" in first_request.system_prompt
        assert "Ahmad created and operates Zelo" in first_request.system_prompt
        assert "customer-support bot" in first_request.system_prompt
        assert "Do not automatically end replies" in first_request.system_prompt
        assert "Avoid repeating stock phrases" in first_request.system_prompt
        assert "$defs" not in first_request.system_prompt
        assert len(first_request.system_prompt) < 1200
        assert first_request.output_schema is not None
        assert "$defs" in first_request.output_schema

        stored = client.get(f"/conversations/{conversation['id']}").json()
        assert stored["title"] == "Help me plan the next Zelo step."
        assert stored["turn_total"] == 2
        assert [turn["number"] for turn in stored["turns"]] == [1, 2]


def test_core_task_route_uses_second_task_specific_model_call(settings, database):
    provider = MindProvider(
        {
            "type": "core_task",
            "content": "I will ask Core to count that text.",
            "kind": "text_stats",
        },
        {
            "kind": "text_stats",
            "payload": {"text": "hello from Zelo"},
        },
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "Count the words in hello from Zelo.",
            "two-stage-count",
        )

        assert response.status_code == 201, response.text
        assert response.json()["core_task"]["kind"] == "text_stats"
        assert len(provider.requests) == 2
        assert "tool_call" in provider.requests[0].system_prompt
        assert "timeout_seconds" not in provider.requests[0].system_prompt
        assert '"kind":"text_stats"' in provider.requests[1].system_prompt
        assert "0.01..60" in provider.requests[1].system_prompt
        assert provider.requests[1].output_schema is not None


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
        assert task_state["result"]["words"] == 3
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

        stored = client.get(f"/conversations/{conversation['id']}?turn_limit=1").json()
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


def test_large_developer_result_remains_visible_and_history_keeps_scanning(
    settings,
    database,
):
    provider = MindProvider(
        reply("Oldest reply."),
        reply("Middle reply."),
        core_task("echo", {"text": "placeholder"}, "I am sending work to Core."),
        reply("I can see the final Core result."),
    )
    with TestClient(create_app(settings, provider=provider), headers=AUTH) as client:
        conversation = new_conversation(client)

        oldest = send_turn(
            client,
            conversation["id"],
            "Oldest context.",
            "history-oldest",
        )
        assert oldest.status_code == 201, oldest.text

        middle = send_turn(
            client,
            conversation["id"],
            "Middle context.",
            "history-middle",
        )
        assert middle.status_code == 201, middle.text
        with database.transaction() as session:
            middle_turn = session.get(ConversationTurn, middle.json()["id"])
            assert middle_turn is not None
            middle_turn.user_content = "MIDDLE_SHOULD_BE_SKIPPED:" + ("m" * 10000)
            middle_turn.assistant_content = "MIDDLE_REPLY_SHOULD_BE_SKIPPED:" + ("n" * 10000)

        created = send_turn(
            client,
            conversation["id"],
            "Run the developer task.",
            "large-result-1",
        )
        assert created.status_code == 201, created.text
        turn_id = created.json()["id"]
        task_id = created.json()["core_task"]["id"]
        wait_for(client, task_id, "succeeded")

        huge_output = "x" * 50000
        long_paths = [f"src/{number:02d}/" + ("路径🚀" * 120) + "file.py" for number in range(19)]
        with database.transaction() as session:
            task = session.get(Task, task_id)
            assert task is not None
            task.kind = "developer_run"
            task.status = "failed"
            task.error = "developer_validation_failed"
            task.result = {
                "worker": "codex",
                "worker_version": "codex-cli 0.155.1-" + ("版本🚀" * 300),
                "worker_exit_code": 0,
                "worker_summary": (
                    "Added one sentence to docs/early-zelo-chat.md. "
                    "No merge or release was performed. " + ("结果🚀" * 2000)
                ),
                "changed_files": [
                    "docs/early-zelo-chat.md",
                    *long_paths,
                    ".pytest_cache/v/cache/nodeids",
                ],
                "ignored_generated_files": [
                    {
                        "path": ".pytest_cache/v/cache/nodeids",
                        "type": "file",
                        "size": 99999,
                    }
                ],
                "validation": [
                    {
                        "identifier": "ruff_check",
                        "exit_code": 0,
                        "output": "All checks passed!",
                        "error": None,
                    },
                    {
                        "identifier": "ruff_format",
                        "exit_code": 0,
                        "output": "formatted",
                        "error": None,
                    },
                    {
                        "identifier": "pytest",
                        "exit_code": 124,
                        "output": huge_output,
                        "error": "process_output_limit" + ("错误🚀" * 400),
                    },
                ],
                "validation_passed": False,
                "independent_review": None,
                "diff": huge_output,
                "error": "developer_validation_failed",
                "protocol_diagnostic": None,
                "boundary_error": None,
                "original_error": None,
                "cancelled": False,
            }
            turn = session.get(ConversationTurn, turn_id)
            assert turn is not None
            turn.user_content = "DEVELOPER_LARGE:" + ("u" * 10000)
            turn.assistant_content = "DEVELOPER_ASSISTANT:" + ("a" * 10000)

        stored = client.get(
            f"/conversations/{conversation['id']}?turn_limit=1&turn_offset=2"
        ).json()
        projected = stored["turns"][0]["core_task"]
        assert projected["status"] == "failed"
        assert projected["error"] == "developer_validation_failed"
        assert projected["result_truncated"] is True
        assert projected["result"]["worker_exit_code"] == 0
        assert projected["result"]["source_changed_files"][0] == "docs/early-zelo-chat.md"
        assert len(projected["result"]["source_changed_files"]) <= 6
        assert all(len(path) <= 192 for path in projected["result"]["source_changed_files"])
        assert len(projected["result"]["worker_summary"]) <= 768
        assert projected["result"]["validation_passed"] is False
        assert projected["result"]["validation"][2]["identifier"] == "pytest"
        assert projected["result"]["validation"][2]["exit_code"] == 124
        assert projected["result"]["validation"][2]["error"].startswith("process_output_limit")
        assert len(projected["result"]["validation"][2]["error"]) <= 128
        encoded_projected_result = json.dumps(
            projected["result"],
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        assert len(encoded_projected_result) <= 4096
        assert "diff" not in json.dumps(projected)
        assert huge_output not in json.dumps(projected)

        follow_up = send_turn(
            client,
            conversation["id"],
            "What happened with that developer task?",
            "large-result-2",
        )
        assert follow_up.status_code == 201, follow_up.text

        messages = provider.requests[3].messages
        assert [message.role for message in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "tool",
            "user",
        ]
        assert messages[0].content == "Oldest context."
        assert messages[1].content == "Oldest reply."
        assert messages[2].content.startswith("DEVELOPER_LARGE:")
        assert messages[3].content.startswith("DEVELOPER_ASSISTANT:")
        assert all("MIDDLE_SHOULD_BE_SKIPPED" not in message.content for message in messages)
        assert len(messages[2].content) <= 4096
        assert len(messages[3].content) <= 4096
        task_state = json.loads(messages[4].content)
        assert len(messages[4].content) <= 6144
        assert task_state["status"] == "failed"
        assert task_state["error"] == "developer_validation_failed"
        assert task_state["result"]["worker"] == "codex"
        assert task_state["result"]["worker_exit_code"] == 0
        assert task_state["result"]["source_changed_files"][0] == ("docs/early-zelo-chat.md")
        assert task_state["result"]["validation_passed"] is False
        assert task_state["result"]["validation"][2]["error"].startswith("process_output_limit")


def test_developer_projection_bounds_oversized_exit_codes():
    oversized_exit_code = 10**5000
    projected, truncated = _project_task_result(
        "developer_run",
        {
            "worker": "codex",
            "worker_exit_code": oversized_exit_code,
            "worker_summary": "Developer result.",
            "changed_files": ["docs/early-zelo-chat.md"],
            "ignored_generated_files": [],
            "validation": [
                {
                    "identifier": "pytest",
                    "exit_code": oversized_exit_code,
                    "error": "process_output_limit",
                }
            ],
            "validation_passed": False,
            "error": "developer_validation_failed",
            "cancelled": False,
        },
        4096,
    )

    assert truncated is True
    assert projected is not None
    assert projected["worker_exit_code"] is None
    assert projected["validation"][0]["exit_code"] is None
    assert projected["validation_passed"] is False
    assert (
        len(
            json.dumps(
                projected,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        <= 4096
    )


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
