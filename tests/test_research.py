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

from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, default_agent_registry
from app.agent_instances import AgentInstanceService
from app.config import ROOT
from app.gateway import ModelGateway, ModelGatewayError, ModelResult, OllamaProvider
from app.main import create_app
from app.memory import MemoryService
from app.models import Task
from app.projects import ProjectService
from app.research import RESEARCH_ACTION, ResearchRuntime, ResearchRuntimeError
from app.runner import Runner
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceTransition,
    AgentTaskCreate,
    ProjectCreate,
    ResearchRunInput,
)
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
from tests.test_agent_execution import direct_services
from tests.test_agent_execution import project as agent_project
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
        ):
            validator.validate(action)
        assert not validator.is_valid(final("No evidence yet. [S2]", "S2"))
        Draft202012Validator(provider.requests[-1].output_schema).validate(
            final("The workspace proof is green. [S2]", "S2")
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
    assert not validator.is_valid(final("An answer needs observed evidence. [S1]", "S1"))
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
        with pytest.raises(ResearchRuntimeError, match="research_tool_unavailable"):
            asyncio.run(run)
        assert provider.requests == []
        return
    assert asyncio.run(run)["tool_calls"] == 1
    validator = Draft202012Validator(provider.requests[0].output_schema)
    assert not validator.is_valid(answer)
    assert Draft202012Validator(provider.requests[1].output_schema).is_valid(answer)
    assert validator.is_valid(call("list_workspace_files"))
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
    assert {action["properties"]["type"]["const"] for action in alternatives} == {"tool_call"}
    assert {action["properties"]["tool"]["const"] for action in alternatives} == {
        "list_workspace_files",
        "read_workspace_file",
    }
    for action in alternatives:
        assert list(action["properties"]) == ["type", "tool", "arguments"]
        assert action["required"] == ["type", "tool", "arguments"]
    assert all(request["format"] == schema for request in captured)


@pytest.mark.parametrize("truncated_listing", [False, True], ids=["listing", "truncated-listing"])
def test_research_schemas_follow_observed_sources_without_mutating_requests(
    research_settings, truncated_listing
):
    if truncated_listing:
        (research_settings.workspace_root / "second.txt").write_text("second public fixture")
        research_settings = research_settings.model_copy(update={"workspace_max_entries": 1})

    class RecordingProvider(ResearchProvider):
        def __init__(self, *actions):
            super().__init__(*actions)
            self.schemas_at_send = []

        async def generate(self, request):
            self.schemas_at_send.append(json.dumps(request.output_schema))
            return await super().generate(request)

    provider = RecordingProvider(
        call("list_workspace_files"),
        call("read_workspace_file", path="facts.txt"),
        final("The listing [S1] and file [S2] provide evidence.", "S1", "S2"),
    )
    tools = ToolGateway(
        research_settings, ToolRegistry((ListWorkspaceFilesTool(), ReadWorkspaceFileTool()))
    )
    runtime = ResearchRuntime(
        research_settings, research_gateway(research_settings, provider), tools
    )
    result = asyncio.run(
        runtime.run(
            query="Inspect the workspace proof.",
            selected_alias="local_general",
            selected_model=research_settings.ollama_general_model,
            timeout_seconds=research_settings.agent_timeout_seconds,
            emit=lambda *_: None,
        )
    )
    assert result["tool_calls"] == 2
    assert [source["id"] for source in result["sources"]] == ["S1", "S2"]
    assert result["sources"][0]["truncated"] is truncated_listing
    schemas = [request.output_schema for request in provider.requests]
    assert [json.dumps(schema) for schema in schemas] == provider.schemas_at_send
    before, after_listing, after_file = [Draft202012Validator(schema) for schema in schemas]
    assert not before.is_valid(final("No evidence yet. [S1]", "S1"))
    assert before.is_valid(call("list_workspace_files"))
    assert before.is_valid(call("read_workspace_file", path="facts.txt"))
    assert after_listing.is_valid(final("Observed listing. [S1]", "S1"))
    assert not after_listing.is_valid(final("Unobserved file. [S2]", "S2"))
    assert after_file.is_valid(final("Both sources. [S1] [S2]", "S1", "S2"))
    assert not after_file.is_valid(final("Invented source. [S9]", "S9"))
    for validator in (after_listing, after_file):
        assert validator.is_valid(call("list_workspace_files"))
        assert validator.is_valid(call("read_workspace_file", path="facts.txt"))
    baseline_final = RESEARCH_ACTION.json_schema()["$defs"]["ResearchFinalAction"]
    for schema, known_ids in zip(schemas[1:], (["S1"], ["S1", "S2"]), strict=True):
        final_schema = schema["$defs"]["ResearchFinalAction"]
        assert schema["anyOf"][0] == {"$ref": "#/$defs/ResearchFinalAction"}
        expected = json.loads(json.dumps(baseline_final))
        expected["properties"]["source_ids"]["items"]["enum"] = known_ids
        assert final_schema == expected
        assert list(final_schema["properties"]) == ["type", "content", "source_ids"]


def test_research_final_only_schema_keeps_original_bounds_and_observed_ids():
    schema = ResearchRuntime._action_schema([], ("S1",))
    expected = RESEARCH_ACTION.json_schema()["$defs"]["ResearchFinalAction"]
    expected["properties"]["source_ids"]["items"]["enum"] = ["S1"]
    assert schema == expected
    assert list(schema["properties"]) == ["type", "content", "source_ids"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid(final("Observed evidence. [S1]", "S1"))
    assert not validator.is_valid(final("Invented evidence. [S2]", "S2"))
    assert not validator.is_valid(final("No IDs."))
    assert not validator.is_valid(final("", "S1"))
    assert not validator.is_valid(final("a" * 16385, "S1"))
    assert not validator.is_valid(final("Too many IDs. [S1]", *(["S1"] * 17)))


@pytest.mark.parametrize(
    "answer,schema_accepts",
    [
        (final("An unknown source. [S9]", "S9"), False),
        (final("A duplicated source. [S1]", "S1", "S1"), True),
    ],
    ids=["unknown-source", "duplicate-source"],
)
def test_research_keeps_independent_final_validation(research_settings, answer, schema_accepts):
    provider = ResearchProvider(call("read_workspace_file", path="facts.txt"), answer, answer)
    tools = ToolGateway(
        research_settings, ToolRegistry((ListWorkspaceFilesTool(), ReadWorkspaceFileTool()))
    )
    runtime = ResearchRuntime(
        research_settings, research_gateway(research_settings, provider), tools
    )
    events = []
    with pytest.raises(ResearchRuntimeError, match="research_citation_invalid"):
        asyncio.run(
            runtime.run(
                query="Read the workspace proof.",
                selected_alias="local_general",
                selected_model=research_settings.ollama_general_model,
                timeout_seconds=research_settings.agent_timeout_seconds,
                emit=lambda event, details: events.append(event),
            )
        )
    assert len(provider.requests) == 3
    assert events.count("research_tool_completed") == 1
    assert events.count("research_evidence_rejected") == 2
    assert (
        Draft202012Validator(provider.requests[1].output_schema).is_valid(answer) is schema_accepts
    )


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


@pytest.mark.parametrize("pause_before_model_reply", [False, True])
def test_research_agent_checks_live_authority_before_workspace_read(
    research_settings, database, pause_before_model_reply
):
    class HeldProvider(ResearchProvider):
        def __init__(self):
            super().__init__(
                call("read_workspace_file", path="facts.txt"),
                final("The workspace proof is green. [S1]", "S1"),
            )
            self.entered = asyncio.Event()
            self.proceed = asyncio.Event()

        async def generate(self, request):
            if not self.requests:
                self.entered.set()
                await self.proceed.wait()
            return await super().generate(request)

    provider = HeldProvider()
    gateway = research_gateway(research_settings, provider)
    tools = ToolGateway(research_settings)
    runtime = ResearchRuntime(research_settings, gateway, tools)
    supervisor = Supervisor(WorkerRegistry(((descriptor_for_research_worker(), runtime),)))
    factory = AgentFactory(default_agent_registry())
    instances = AgentInstanceService(database, factory, MemoryService(database))
    tasks = TaskService(
        database,
        research_settings,
        gateway=gateway,
        tool_gateway=tools,
        agent_factory=factory,
        research_supervisor=supervisor,
    )
    project = ProjectService(database).create(
        ProjectCreate(name="Research read boundary", summary="Pause before tool dispatch.")
    )
    instance = instances.create(AgentInstanceCreate(agent_id="research", project_id=project.id))
    created, _ = AgentExecutionService(database, factory, tasks).submit(
        instance.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run", payload={"query": "Read the workspace proof."}
            )
        ),
        "research:pause-before-tool",
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == created.id
    assert claimed.agent_execution is not None

    async def exercise():
        running = asyncio.create_task(Runner(tasks, research_supervisor=supervisor)._run(claimed))
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        if pause_before_model_reply:
            instances.transition(instance.id, AgentInstanceTransition(action="pause"))
        provider.proceed.set()
        await asyncio.wait_for(running, timeout=5)

    asyncio.run(exercise())

    current = tasks.get(created.id)
    events = [event.event for event in tasks.events(created.id, 100, 0)]
    if pause_before_model_reply:
        assert current.status == "cancelled"
        assert current.error == "agent_execution_invalidated"
        assert "research_tool_requested" not in events
        assert "research_tool_completed" not in events
    else:
        assert current.status == "succeeded"
        assert current.result["answer"] == "The workspace proof is green. [S1]"
        assert "research_tool_requested" in events
        assert "research_tool_completed" in events


def test_bound_research_runtime_requires_live_core_tool_guard(research_settings, database):
    _factory, instances, tasks, agent_execution = direct_services(database, research_settings)
    owned_project = agent_project(database, "Guarded research")
    instance = instances.create(
        AgentInstanceCreate(agent_id="research", project_id=owned_project.id)
    )
    created, _ = agent_execution.submit(
        instance.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run", payload={"query": "Read the workspace proof."}
            )
        ),
        "research:missing-use-guard",
    )
    claimed = tasks.claim()
    assert claimed is not None and claimed.task_id == created.id
    assert claimed.agent_execution is not None

    provider = ResearchProvider(
        call("read_workspace_file", path="facts.txt"),
        final("The workspace proof is green. [S1]", "S1"),
    )
    gateway = research_gateway(research_settings, provider)
    runtime = ResearchRuntime(research_settings, gateway, ToolGateway(research_settings))
    alias, model = gateway.select("general")
    with pytest.raises(ResearchRuntimeError, match="research_tool_authority_unavailable"):
        asyncio.run(
            runtime.run(
                query="Read the workspace proof.",
                selected_alias=alias,
                selected_model=model,
                timeout_seconds=30,
                emit=lambda _event, _details: None,
                agent_execution=claimed.agent_execution,
            )
        )
    assert provider.requests == []


def test_research_rechecks_authority_inside_tool_thread(research_settings, monkeypatch):
    provider = ResearchProvider(call("read_workspace_file", path="facts.txt"))
    gateway = research_gateway(research_settings, provider)
    tools = ToolGateway(research_settings)
    executed = []

    def forbidden(*args, **kwargs):
        executed.append((args, kwargs))
        raise AssertionError("Revoked tool reached the executor")

    monkeypatch.setattr(tools, "execute", forbidden)
    runtime = ResearchRuntime(research_settings, gateway, tools)
    events = []
    authorized = True

    def emit(event, _details):
        nonlocal authorized
        events.append(event)
        if event == "research_tool_requested":
            authorized = False

    alias, model = gateway.select("general")
    with pytest.raises(ResearchRuntimeError, match="research_tool_authority_invalid"):
        asyncio.run(
            runtime.run(
                query="Read the workspace proof.",
                selected_alias=alias,
                selected_model=model,
                timeout_seconds=30,
                emit=emit,
                authorize_tool=lambda _name: authorized,
            )
        )
    assert "research_tool_requested" in events
    assert "research_tool_completed" not in events
    assert executed == []


def test_research_tool_use_check_rejects_cancelled_unbound_task(settings, database):
    supervisor = research_supervisor()
    service = TaskService(
        database, settings, gateway=research_gateway(settings), research_supervisor=supervisor
    )
    created, _ = service.submit(
        ResearchRunInput(kind="research_run", payload={"query": "Find evidence."}), None
    )
    claimed = service.claim()
    assert claimed is not None and claimed.task_id == created.id
    assert service.research_tool_allowed(claimed, "repo_status")
    service.cancel(created.id)
    assert not service.research_tool_allowed(claimed, "repo_status")


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
