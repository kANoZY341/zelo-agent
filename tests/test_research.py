"""v0.12 fixed Research specialist tests."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.config import ROOT
from app.gateway import ModelGateway, ModelGatewayError, ModelResult, OllamaProvider
from app.main import create_app
from app.models import Task
from app.research import RESEARCH_ACTION, ResearchRuntime, ResearchRuntimeError
from app.runner import Runner
from app.schemas import ResearchRunInput
from app.tasks import TaskService, _project_task_result
from app.tools import (
    ListWorkspaceFilesTool,
    ReadWorkspaceFileTool,
    RepoStatusTool,
    SystemInfoTool,
    ToolGateway,
    ToolRegistry,
    ToolRisk,
    ToolRuntimeError,
)
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_research_worker
from tests.conftest import AUTH
from tests.test_api import wait_for
from tests.test_campaigns import campaign_fixture
from tests.test_mind import new_conversation, send_turn


class ResearchProvider:
    def __init__(self, *actions) -> None:
        self.actions = list(actions)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
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


def call(tool: str, **arguments) -> dict:
    return {"type": "tool_call", "tool": tool, "arguments": arguments}


def final(content: str, *source_ids: str) -> dict:
    return {
        "type": "final",
        "content": content,
        "source_ids": list(source_ids),
    }


@pytest.fixture
def research_settings(settings):
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="test-research-", dir=ROOT / "workspaces") as directory:
        root = Path(directory)
        (root / "facts.txt").write_text(
            "Zelo research evidence: the workspace proof is green.",
            encoding="utf-8",
        )
        yield settings.model_copy(update={"workspace_root": root})


def submit_research(client: TestClient, *, campaign_id: str | None = None) -> dict:
    body = {
        "kind": "research_run",
        "payload": {"query": "Find the workspace proof and summarize it with evidence."},
    }
    if campaign_id is not None:
        body["campaign_id"] = campaign_id
    response = client.post("/tasks", json=body, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()


def test_research_retrieves_real_evidence_and_persists_cited_metadata(
    research_settings,
    database,
):
    provider = ResearchProvider(
        call("list_workspace_files"),
        call("read_workspace_file", path="facts.txt"),
        final("The workspace proof is green. [S2]", "S2"),
    )
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        created = submit_research(client)
        assert created["approvals"] == []
        assert created["selected_alias"] == "local_general"
        assert created["selected_model"] == "qwen3:4b-instruct"
        selection = created["research_spec"]
        assert selection["descriptor"]["worker_id"] == "research_core"
        assert selection["descriptor"]["role"] == "research"
        assert selection["descriptor"]["capabilities"] == ["research"]
        assert selection["authority_profile"] == "workspace-read-no-network-v1"

        completed = wait_for(client, created["id"], "succeeded")
        result = completed["result"]
        assert result["answer"] == "The workspace proof is green. [S2]"
        assert result["model_steps"] == 3
        assert result["tool_calls"] == 2
        assert len(result["sources"]) == 1
        source = result["sources"][0]
        assert source["id"] == "S2"
        assert source["tool"] == "read_workspace_file"
        assert source["locator"] == "workspace:facts.txt"
        assert len(source["sha256"]) == 64
        assert source["truncated"] is False

        events = client.get(f"/tasks/{created['id']}/events", headers=AUTH).json()
        names = [event["event"] for event in events]
        assert "research_started" in names
        assert "supervisor_routing_requested" in names
        assert "supervisor_worker_selected" in names
        assert "supervisor_worker_revalidated" in names
        assert names.count("research_tool_requested") == 2
        assert names.count("research_tool_completed") == 2
        assert "research_completed" in names
        assert "workspace proof is green" not in json.dumps(events)

        request = provider.requests[0]
        validator = Draft202012Validator(request.output_schema)
        for action in (
            call("list_workspace_files"),
            call("read_workspace_file", path="facts.txt"),
            call("repo_status"),
            final("The workspace proof is green. [S2]", "S2"),
        ):
            validator.validate(action)
        assert (
            request.output_schema["$defs"]["ResearchFinalAction"]
            == (RESEARCH_ACTION.json_schema()["$defs"]["ResearchFinalAction"])
        )
        assert "read_workspace_file" in request.system_prompt
        assert "list_workspace_files" in request.system_prompt
        assert "repo_status" in request.system_prompt
        assert "system_info" not in request.system_prompt
        assert "browser" not in request.system_prompt

        first_source = json.loads(provider.requests[1].messages[-1].content)
        second_source = json.loads(provider.requests[2].messages[-1].content)
        assert first_source["source_id"] == "S1"
        assert second_source["source_id"] == "S2"
        assert first_source["untrusted"] is True
        assert second_source["untrusted"] is True


def test_research_model_schema_matches_actual_registered_tool_inputs(research_settings):
    tools = ToolGateway(
        research_settings,
        ToolRegistry((ListWorkspaceFilesTool(), ReadWorkspaceFileTool())),
    )
    provider = ResearchProvider(
        call("read_workspace_file", path="facts.txt"),
        final("The workspace proof is green. [S1]", "S1"),
    )
    runtime = ResearchRuntime(
        research_settings, research_gateway(research_settings, provider), tools
    )
    result = asyncio.run(
        runtime.run(
            query="Read the workspace proof.",
            selected_alias="local_general",
            selected_model=research_settings.ollama_general_model,
            timeout_seconds=research_settings.agent_timeout_seconds,
            emit=lambda *_: None,
        )
    )
    assert result["tool_calls"] == 1
    schema = provider.requests[0].output_schema
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for action in (
        call("list_workspace_files"),
        call("list_workspace_files", path="."),
        call("read_workspace_file", path="facts.txt"),
        call("read_workspace_file", path="a" * 512),
    ):
        tools.validate(action["tool"], action["arguments"])
        assert validator.is_valid(action), action
    # The final choice remains present even before the first tool result.
    assert validator.is_valid(final("An answer needs observed evidence. [S1]", "S1"))
    for action in (
        call("read_workspace_file"),
        call("read_workspace_file", path=""),
        call("read_workspace_file", path="a" * 513),
        call("read_workspace_file", path={"path": "facts.txt"}),
        call("read_workspace_file", path="facts.txt", unexpected={"nested": {}}),
        call("list_workspace_files", unexpected="value"),
    ):
        with pytest.raises(ToolRuntimeError, match="invalid_tool_arguments"):
            tools.validate(action["tool"], action["arguments"])
        assert not validator.is_valid(action), action
    assert not validator.is_valid(call("system_info"))
    assert not validator.is_valid(call("repo_status"))


@pytest.mark.parametrize("all_denied", [False, True], ids=["listing-only", "no-tools"])
def test_research_schema_omits_denied_and_non_research_tools(research_settings, all_denied):
    listing = ListWorkspaceFilesTool()
    read = ReadWorkspaceFileTool()
    read.risk = ToolRisk.SENSITIVE_READ
    repo = RepoStatusTool()
    repo.approval_required = True
    if all_denied:
        listing.approval_required = True
    tools = ToolGateway(
        research_settings,
        ToolRegistry((listing, read, repo, SystemInfoTool())),
    )
    answer = final("Observed workspace listing. [S1]", "S1")
    provider = ResearchProvider(
        *([answer, answer] if all_denied else [call("list_workspace_files"), answer])
    )
    runtime = ResearchRuntime(
        research_settings, research_gateway(research_settings, provider), tools
    )
    run = runtime.run(
        query="Inspect workspace evidence.",
        selected_alias="local_general",
        selected_model=research_settings.ollama_general_model,
        timeout_seconds=research_settings.agent_timeout_seconds,
        emit=lambda *_: None,
    )
    if all_denied:
        with pytest.raises(ResearchRuntimeError, match="research_citation_invalid"):
            asyncio.run(run)
    else:
        assert asyncio.run(run)["tool_calls"] == 1
    validator = Draft202012Validator(provider.requests[0].output_schema)
    assert validator.is_valid(answer)  # No state gate was added to the final choice.
    assert validator.is_valid(call("list_workspace_files")) is not all_denied
    assert not validator.is_valid(call("read_workspace_file", path="facts.txt"))
    assert not validator.is_valid(call("repo_status"))
    assert not validator.is_valid(call("system_info"))
    assert "read_workspace_file" not in provider.requests[0].system_prompt
    assert "repo_status" not in provider.requests[0].system_prompt
    assert "system_info" not in provider.requests[0].system_prompt


@pytest.mark.parametrize(
    "response,error_code",
    [
        (call("system_info"), "research_tool_not_allowed"),
        (call("repo_status"), "research_tool_unavailable"),
        (
            call("read_workspace_file", path="facts.txt", extra="value"),
            "invalid_tool_arguments",
        ),
        (
            call("read_workspace_file", path={"path": "facts.txt"}),
            "invalid_tool_arguments",
        ),
        (
            {**call("read_workspace_file", path="facts.txt"), "extra": "value"},
            "research_parse_retries_exhausted",
        ),
        (
            '{"type":"tool_call","tool":"read_workspace_file",'
            '"tool":"list_workspace_files","arguments":{}}',
            "research_parse_retries_exhausted",
        ),
    ],
    ids=[
        "non-research-tool",
        "unregistered-tool",
        "extra-input",
        "nested-input",
        "extra-action",
        "duplicate-key",
    ],
)
def test_research_still_rejects_schema_violating_provider_responses(
    research_settings, monkeypatch, response, error_code
):
    captured = []
    executions = []
    tools = ToolGateway(
        research_settings,
        ToolRegistry((ListWorkspaceFilesTool(), ReadWorkspaceFileTool())),
    )
    real_execute = tools.execute

    def execute(name, arguments):
        executions.append((name, arguments))
        return real_execute(name, arguments)

    monkeypatch.setattr(tools, "execute", execute)

    def handle(request):
        captured.append(json.loads(request.content))
        text = response if isinstance(response, str) else json.dumps(response)
        return httpx.Response(200, json={"message": {"content": text}})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.gateway.httpx.AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)),
    )
    provider = OllamaProvider("http://127.0.0.1:11434", use_json_schema=True)
    runtime = ResearchRuntime(
        research_settings, research_gateway(research_settings, provider), tools
    )
    with pytest.raises(ResearchRuntimeError, match=error_code):
        asyncio.run(
            runtime.run(
                query="Read the workspace proof.",
                selected_alias="local_general",
                selected_model=research_settings.ollama_general_model,
                timeout_seconds=research_settings.agent_timeout_seconds,
                emit=lambda *_: None,
            )
        )
    assert executions == []
    expected_calls = (
        research_settings.max_model_parse_retries + 1
        if error_code == "research_parse_retries_exhausted"
        else 1
    )
    assert len(captured) == expected_calls
    schema = captured[0]["format"]
    alternatives = [schema["$defs"][choice["$ref"].rsplit("/", 1)[1]] for choice in schema["anyOf"]]
    assert alternatives[0] == RESEARCH_ACTION.json_schema()["$defs"]["ResearchFinalAction"]
    assert list(alternatives[0]["properties"]) == ["type", "content", "source_ids"]
    assert {action["properties"]["tool"]["const"] for action in alternatives[1:]} == {
        "list_workspace_files",
        "read_workspace_file",
    }
    for action in alternatives[1:]:
        assert list(action["properties"]) == ["type", "tool", "arguments"]
        assert action["required"] == ["type", "tool", "arguments"]
    assert all(request["format"] == schema for request in captured)


def test_research_source_locator_accepts_maximum_workspace_path():
    path = "a/" * 255 + "aa"
    assert len(path) == 512
    source = ResearchRuntime._source(
        "S1",
        "read_workspace_file",
        {"path": path},
        {"path": path, "text": "evidence", "truncated": False},
        truncated=False,
    )
    assert source.locator == f"workspace:{path}"
    assert len(source.locator) == 522


def test_research_preserves_tool_native_truncation(
    research_settings,
    database,
):
    (research_settings.workspace_root / "second.txt").write_text(
        "second evidence",
        encoding="utf-8",
    )
    configured = research_settings.model_copy(update={"workspace_max_entries": 1})
    provider = ResearchProvider(
        call("list_workspace_files"),
        final("The listing is incomplete. [S1]", "S1"),
    )
    with TestClient(
        create_app(configured, provider=provider),
        headers=AUTH,
    ) as client:
        created = submit_research(client)
        completed = wait_for(client, created["id"], "succeeded")
        source = completed["result"]["sources"][0]
        assert source["tool"] == "list_workspace_files"
        assert source["truncated"] is True

        envelope = json.loads(provider.requests[1].messages[-1].content)
        assert envelope["truncated"] is True


def test_research_projection_honors_encoded_budget():
    result = {
        "answer": "é" * 3072,
        "sources": [
            {
                "id": f"S{index}",
                "tool": "read_workspace_file",
                "locator": "workspace:" + ("é" * 256),
                "sha256": "a" * 64,
                "truncated": False,
            }
            for index in range(1, 9)
        ],
        "model_steps": 3,
        "tool_calls": 2,
    }
    projection, truncated = _project_task_result("research_run", result, 4096)
    encoded = json.dumps(
        projection,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    assert truncated is True
    assert len(encoded) <= 4096
    assert projection["answer"]


def test_research_rejects_non_allowlisted_tool_without_execution(
    research_settings,
    database,
    monkeypatch,
):
    provider = ResearchProvider(call("system_info"))
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        executions = []
        tool = client.app.state.tool_gateway.registry.get("system_info")
        monkeypatch.setattr(tool, "run", lambda *args: executions.append(args))

        created = submit_research(client)
        failed = wait_for(client, created["id"], "failed")

        assert failed["error"] == "research_tool_not_allowed"
        assert executions == []


def test_research_rejects_undeclared_citation(
    research_settings,
    database,
):
    provider = ResearchProvider(
        call("read_workspace_file", path="facts.txt"),
        final("One real source [S1] plus an invented source [S9].", "S1"),
        final("Still invalid [S1] [S9].", "S1"),
    )
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        created = submit_research(client)
        failed = wait_for(client, created["id"], "failed")
        assert failed["error"] == "research_citation_invalid"


def test_research_cannot_finish_with_invented_or_missing_evidence(
    research_settings,
    database,
):
    provider = ResearchProvider(
        final("Unsupported claim. [S1]", "S1"),
        final("Still unsupported. [S1]", "S1"),
    )
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        created = submit_research(client)
        failed = wait_for(client, created["id"], "failed")

        assert failed["error"] == "research_citation_invalid"
        events = client.get(f"/tasks/{created['id']}/events", headers=AUTH).json()
        assert sum(event["event"] == "research_evidence_rejected" for event in events) == 2


def test_research_retry_preserves_bound_model_and_starts_fresh_evidence_loop(
    research_settings,
    database,
):
    provider = ResearchProvider(
        call("read_workspace_file", path="facts.txt"),
        ModelGatewayError("model_unavailable"),
        call("read_workspace_file", path="facts.txt"),
        final("Recovered research result. [S1]", "S1"),
    )
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        created = submit_research(client)
        failed = wait_for(client, created["id"], "failed")
        assert failed["error"] == "model_unavailable"
        assert failed["selected_alias"] == "local_general"
        assert failed["selected_model"] == "qwen3:4b-instruct"

        retry = client.post(f"/tasks/{created['id']}/retry", headers=AUTH)
        assert retry.status_code == 202, retry.text

        completed = wait_for(client, created["id"], "succeeded")
        assert [attempt["number"] for attempt in completed["attempts"]] == [1, 2]
        assert completed["selected_alias"] == failed["selected_alias"]
        assert completed["selected_model"] == failed["selected_model"]
        assert completed["result"]["answer"] == "Recovered research result. [S1]"
        assert completed["result"]["sources"][0]["id"] == "S1"
        assert len(provider.requests) == 4

        first_attempt_first_request = provider.requests[0]
        second_attempt_first_request = provider.requests[2]
        assert first_attempt_first_request.messages == second_attempt_first_request.messages

        events = client.get(f"/tasks/{created['id']}/events", headers=AUTH).json()
        assert sum(event["event"] == "research_started" for event in events) == 2
        assert sum(event["event"] == "research_failed" for event in events) == 1
        assert sum(event["event"] == "research_completed" for event in events) == 1


def test_research_task_keeps_campaign_ownership(
    research_settings,
    database,
):
    _, _, campaign = campaign_fixture(database)
    provider = ResearchProvider(
        call("read_workspace_file", path="facts.txt"),
        final("Campaign research is grounded. [S1]", "S1"),
    )
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        created = submit_research(client, campaign_id=campaign.id)
        assert created["campaign_id"] == campaign.id

        completed = wait_for(client, created["id"], "succeeded")
        assert completed["campaign_id"] == campaign.id

        events = client.get(
            f"/campaigns/{campaign.id}/events",
            headers=AUTH,
        ).json()["items"]
        attached = [
            event
            for event in events
            if event["event"] == "task_attached" and event["task_id"] == created["id"]
        ]
        finished = [
            event
            for event in events
            if event["event"] == "task_finished" and event["task_id"] == created["id"]
        ]
        assert len(attached) == 1
        assert len(finished) == 1
        assert finished[0]["details"]["status"] == "succeeded"


def test_zelo_mind_can_delegate_to_research_specialist(
    research_settings,
    database,
):
    provider = ResearchProvider(
        {
            "type": "core_task",
            "content": "I will have Research inspect the workspace evidence.",
            "kind": "research_run",
        },
        {
            "kind": "research_run",
            "payload": {"query": "Inspect the workspace proof and report the evidence."},
        },
        call("read_workspace_file", path="facts.txt"),
        final("Research found the workspace proof. [S1]", "S1"),
    )
    with TestClient(
        create_app(research_settings, provider=provider),
        headers=AUTH,
    ) as client:
        conversation = new_conversation(client)
        response = send_turn(
            client,
            conversation["id"],
            "Research the proof in our workspace.",
            "research-from-mind",
        )
        assert response.status_code == 201, response.text
        task = response.json()["core_task"]
        assert task["kind"] == "research_run"
        assert "payload" not in task
        full_task = client.get(f"/tasks/{task['id']}", headers=AUTH)
        assert full_task.status_code == 200, full_task.text
        assert (
            full_task.json()["payload"]["query"]
            == "Inspect the workspace proof and report the evidence."
        )

        completed = wait_for(client, task["id"], "succeeded")
        assert completed["result"]["answer"] == "Research found the workspace proof. [S1]"
        assert "research_run" in provider.requests[0].system_prompt
        assert '"kind":"research_run"' in provider.requests[1].system_prompt


def research_gateway(settings, provider=None) -> ModelGateway:
    return ModelGateway(
        provider or ResearchProvider(),
        settings.ollama_general_model,
        settings.ollama_reasoning_model,
        settings.model_config_version,
        mind_model=settings.ollama_mind_model,
    )


def research_supervisor(worker_id: str = "research_core") -> Supervisor[object]:
    registry = WorkerRegistry[object](
        ((descriptor_for_research_worker(worker_id=worker_id), object()),)
    )
    return Supervisor[object](registry)


def test_runner_requires_bound_research_worker_selection(settings, database):
    supervisor = research_supervisor()
    service = TaskService(
        database,
        settings,
        gateway=research_gateway(settings),
        research_supervisor=supervisor,
    )
    created, _ = service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    assert execution.research_selection is not None

    unbound = replace(execution, research_selection=None)
    runner = Runner(service, research_supervisor=supervisor)

    with pytest.raises(ResearchRuntimeError, match="research_worker_selection_unavailable"):
        asyncio.run(runner._research_worker(unbound))


def test_runner_rejects_stale_research_worker_selection(settings, database):
    supervisor = research_supervisor()
    service = TaskService(
        database,
        settings,
        gateway=research_gateway(settings),
        research_supervisor=supervisor,
    )
    service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    assert execution.research_selection is not None

    runner = Runner(service, research_supervisor=research_supervisor("research_next"))

    with pytest.raises(ResearchRuntimeError, match="research_worker_selection_stale"):
        asyncio.run(runner._research_worker(execution))


def test_research_action_hash_binds_worker_selection_snapshot(settings, database):
    supervisor = research_supervisor()
    service = TaskService(
        database,
        settings,
        gateway=research_gateway(settings),
        research_supervisor=supervisor,
    )
    created, _ = service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}),
        None,
    )

    with database.transaction() as session:
        persisted = session.get(Task, created.id)
        assert persisted is not None
        tampered = dict(persisted.research_spec)
        tampered["excluded_worker_ids"] = ["other_worker"]
        persisted.research_spec = tampered

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "action_changed"


def test_research_worker_selection_fails_closed_if_registry_changes(settings, database):
    service = TaskService(
        database,
        settings,
        gateway=research_gateway(settings),
        research_supervisor=research_supervisor(),
    )
    created, was_created = service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}),
        None,
    )

    assert was_created is True
    assert created.research_spec is not None
    assert created.research_spec.descriptor.worker_id == "research_core"

    service.research_supervisor = research_supervisor("research_next")

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "research_worker_selection_invalid"


def test_research_retry_reselects_current_worker(settings, database):
    service = TaskService(
        database,
        settings,
        gateway=research_gateway(settings),
        research_supervisor=research_supervisor(),
    )
    created, _ = service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}),
        None,
    )
    service.cancel(created.id)
    service.research_supervisor = research_supervisor("research_next")

    retried = service.retry(created.id)

    assert retried.action_version == 2
    assert retried.status == "pending"
    assert retried.research_spec is not None
    assert retried.research_spec.descriptor.worker_id == "research_next"
    execution = service.claim()
    assert execution is not None
    assert execution.research_selection is not None
    assert execution.research_selection.descriptor.worker_id == "research_next"


def test_pre_v015_pending_research_task_fails_closed_then_retry_rebinds(settings, database):
    service = TaskService(
        database,
        settings,
        gateway=research_gateway(settings),
        research_supervisor=research_supervisor(),
    )
    created, _ = service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}),
        None,
    )

    with database.transaction() as session:
        persisted = session.get(Task, created.id)
        assert persisted is not None
        persisted.research_spec = None

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "research_worker_selection_invalid"

    retried = service.retry(created.id)
    assert retried.status == "pending"
    assert retried.research_spec is not None
    assert retried.research_spec.descriptor.worker_id == "research_core"
