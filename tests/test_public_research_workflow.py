"""Offline acceptance at real Core/Mind/Research and Browser ownership boundaries.

Only HTTP transport, native rendering and model responses are fixtures. No live
DNS, public retrieval, Docker, model inference, personal DB or source execution.
"""

import asyncio
import hashlib
import json
import shutil
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.api_contracts import ApiEndpointRegistry
from app.browser_operator import contract_data, load_receipt
from app.browser_worker import BrowserRuntime
from app.config import ROOT
from app.database import Database
from app.models import MemoryRecord, Task
from app.research_evidence import (
    BROWSER_SOURCE_MANIFEST_DIGEST,
    MANIFEST_SHA256,
    MODEL,
    MODEL_REQUEST_SETTINGS,
    RESEARCH_TIMEOUT,
    RUN_SCHEMA,
    EvidenceError,
    canonical,
    digest,
    load_source_manifest,
    validate_candidate,
)
from app.tasks import TaskService
from qualification.broker import PageSession
from qualification.renderer import DocumentRenderer
from qualification.renderer_contracts import origins_digest
from scripts.qualify_browser_renderer import expected_image_files
from scripts.qualify_public_research import (
    BUDGETS,
    MODEL_DIGEST,
    exercise,
    qualify,
    research_request,
    research_tools,
    save_report,
    workflow_provider,
    workflow_settings,
)
from tests.test_browser_renderer import Resolver
from tests.test_browser_renderer import setup as renderer_setup
from tests.test_research import ResearchProvider, call, final


@contextmanager
def public_fixture(tmp_path, *, changed_body=False, truncated=False, oversized=False, actions=None):
    """A fully owned synthetic run with a verified fixture receipt, for phase tests."""
    base = ROOT / "runtime" / "public-research"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ROOT / "workspaces").mkdir(exist_ok=True)
    run_id = uuid4().hex
    run_root = base / ("proof-" + run_id)
    workspace = ROOT / "workspaces" / ("public-research-" + run_id)
    run_root.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    try:
        manifest = load_source_manifest()
        bodies = {
            s.path: f"Synthetic {s.source_id} fixture evidence.\n".encode()
            for s in manifest.sources
        }
        if oversized:
            bodies[manifest.sources[0].path] = ("ع" * 4000).encode()
        manifest = manifest.model_copy(
            update={
                "sources": tuple(
                    s.model_copy(
                        update={
                            "body_bytes": len(bodies[s.path]),
                            "sha256": hashlib.sha256(bodies[s.path]).hexdigest(),
                        }
                    )
                    for s in manifest.sources
                )
            }
        )
        if changed_body:
            bodies[manifest.sources[0].path] = b"Different unpinned response."
        prepared = run_root / "prepared"
        prepared.mkdir(mode=0o700)
        build = prepared / "build"
        build.mkdir(mode=0o700)
        contract, bundle, _, _, _ = renderer_setup.__wrapped__(build)
        registry = ApiEndpointRegistry.from_entries((manifest.endpoint_id + "=" + manifest.origin,))
        bindings = tuple(registry.bind(e.endpoint_id) for e in registry.list())
        contract = replace(
            contract,
            execution=replace(
                contract.execution, approved_origins_digest=origins_digest(registry, bindings)
            ),
        )
        expected = expected_image_files(contract.manifest, bundle / "browser-pipe")
        receipt_value = {
            "schema": "zelo-browser-operator-v1",
            "contract": contract_data(contract),
            "image_payload": expected,
            "image_payload_digest": digest(expected),
        }
        receipt_path = prepared / "verified-receipt.json"
        receipt_path.write_bytes(canonical(receipt_value))
        receipt_path.chmod(0o600)
        (prepared / "runtime").mkdir(mode=0o700)
        (prepared / "admission").mkdir(mode=0o700)
        _, receipt_identity = load_receipt(receipt_path, bundle, registry)

        class FixtureResolver(Resolver):
            async def resolve(self, host, port, deadline):
                assert host == "raw.githubusercontent.com" and port == 443
                return ("8.8.8.8",)

        class Body(httpx.AsyncByteStream):
            def __init__(self, data):
                self.data = data

            async def __aiter__(self):
                yield self.data

        class Handle:
            async def render(self, body, media_type, deadline, *, authorize_action=None):
                assert media_type == "text/plain"
                if authorize_action is not None:
                    assert authorize_action()
                return SimpleNamespace(text=body, truncated=truncated, nodes=1)

            async def stop_and_confirm_cleanup(self):
                return True

        def session(*args, **kwargs):
            return PageSession(
                *args,
                **kwargs,
                resolver=FixtureResolver(),
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"Content-Type": "text/plain"},
                        stream=Body(bodies[request.url.path]),
                    )
                ),
            )

        runtime = BrowserRuntime(
            registry,
            contract,
            bundle,
            prepared / "runtime",
            prepared / "admission",
            receipt_identity,
            receipt_path=receipt_path,
            renderer_factory=lambda *args: DocumentRenderer(
                *args, session_factory=session, handle_factory=lambda *_: Handle()
            ),
        )
        settings = workflow_settings(run_root, workspace, receipt_path)
        provider = ResearchProvider(
            *(
                actions
                if actions is not None
                else [
                    {
                        "type": "core_task",
                        "content": "I will investigate the staged evidence.",
                        "kind": "research_run",
                    },
                    {
                        "kind": "research_run",
                        "payload": {"query": research_request(manifest)},
                        "timeout_seconds": RESEARCH_TIMEOUT,
                    },
                    *(call("read_workspace_file", path=s.artifact) for s in manifest.sources),
                    final(
                        "Synthetic RSS evidence [S1], web evidence [S2], and video evidence [S3].",
                        "S1",
                        "S2",
                        "S3",
                    ),
                ]
            )
        )
        report = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "status": "incomplete",
            "phase": "fixture",
            "source_manifest_sha256": MANIFEST_SHA256,
            "source_bundle_manifest_digest": BROWSER_SOURCE_MANIFEST_DIGEST,
            "post_run_image_payload_digest": receipt_value["image_payload_digest"],
            "model": MODEL,
            "model_request_settings": dict(MODEL_REQUEST_SETTINGS),
            "model_digest_before": MODEL_DIGEST,
            "model_digest_after": MODEL_DIGEST,
            "budgets": dict(BUDGETS),
            "execution_mode": "fixture_only",
            "setup_cleanup_errors": [],
            "memory_written": False,
            "external_network_attempted": False,
        }
        yield SimpleNamespace(
            run_root=run_root,
            workspace=workspace,
            settings=settings,
            manifest=manifest,
            runtime=runtime,
            provider=provider,
            report=report,
        )
    finally:
        shutil.rmtree(workspace)
        shutil.rmtree(run_root)


def run_candidate(case):
    asyncio.run(
        exercise(
            case.settings,
            case.report,
            runtime=case.runtime,
            provider=case.provider,
            manifest=case.manifest,
        )
    )
    case.report["status"] = "passed"
    return save_report(case.run_root, case.report)


def test_real_core_mind_research_candidate_has_complete_evidence_and_no_memory(tmp_path):
    with public_fixture(tmp_path) as case:
        path = run_candidate(case)
        assert path.stat().st_mode & 0o777 == 0o400
        assert case.report["runtime_cleanup_confirmed"] is True
        assert case.report["mind_selected_alias"] == "local_mind"
        assert case.report["research_result"]["actual_model"] == MODEL
        assert len(case.report["evidence"]) == 3
        db = Database(case.settings.database_path)
        try:
            tasks = TaskService(db, workflow_settings(case.run_root, case.workspace))
            result = validate_candidate(
                case.report, tasks, research_tools(case.settings), case.manifest
            )
            assert {s.locator for s in result.sources} == {
                "workspace:" + s.artifact for s in case.manifest.sources
            }
            assert all(not s.truncated and s.tool == "read_workspace_file" for s in result.sources)
            with db.read() as session:
                assert session.scalar(select(func.count()).select_from(MemoryRecord)) == 0
                assert session.scalar(select(func.count()).select_from(Task)) == 4
            assert all(request.model_id == MODEL for request in case.provider.requests)
        finally:
            db.close()


def test_public_workflow_binds_distinct_mind_and_research_http_timeouts(tmp_path, monkeypatch):
    with public_fixture(tmp_path) as case:
        actions = list(case.provider.actions)
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={"model": MODEL, "message": {"content": json.dumps(actions.pop(0))}},
            )

        original = httpx.AsyncClient

        def client(**kwargs):
            if str(kwargs.get("base_url", "")) == "http://127.0.0.1:11434":
                return original(**kwargs, transport=httpx.MockTransport(handle))
            return original(**kwargs)

        monkeypatch.setattr("app.gateway.httpx.AsyncClient", client)
        case.provider = workflow_provider(case.settings)
        run_candidate(case)
        assert len(requests) == 6 and actions == []
        for request, timeout in zip(requests, [240, 240, 480, 480, 480, 480], strict=True):
            assert request.extensions["timeout"] == {
                "connect": timeout,
                "read": timeout,
                "write": timeout,
                "pool": timeout,
            }
            body = json.loads(request.content)
            assert body["stream"] is False and body["think"] is False
            assert body["model"] == "qwen3.5:4b"
            assert isinstance(body["format"], dict)
            assert body["options"] == {
                "temperature": 0.7,
                "num_predict": 1024,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repeat_penalty": 1.0,
                "seed": 10102026,
                "num_ctx": 16384,
            }
            assert case.report["model_request_settings"] == {
                "format": "json_schema",
                "think": body["think"],
                **body["options"],
            }
        assert case.settings.model_timeout_seconds == 240
        assert case.settings.scheduler_mind_window_seconds == 966
        assert case.settings.agent_timeout_seconds == 600
        assert case.report["budgets"] == {
            "browser_timeout_seconds": 30,
            "model_timeout_seconds": 240,
            "research_model_timeout_seconds": 480,
            "research_timeout_seconds": 600,
            "dispatch_grace_seconds": 15,
            "max_agent_steps": 6,
            "max_agent_tool_calls": 4,
            "max_model_parse_retries": 1,
            "max_agent_tool_result_chars": 16384,
        }
        database = Database(case.settings.database_path)
        try:
            task = TaskService(database, case.settings).get(case.report["research_task_id"])
            assert task.timeout_seconds == 600 and task.status == "succeeded"
        finally:
            database.close()
        assert case.report["runtime_cleanup_confirmed"] is True
        assert len(case.report["research_result"]["sources"]) == 3


@pytest.mark.parametrize(
    "variant,code",
    [
        ("changed_body", "public_source_identity_mismatch"),
        ("truncated", "public_source_identity_mismatch"),
        ("oversized", "public_source_tool_result_incomplete"),
    ],
)
def test_incomplete_or_unpinned_source_stops_before_mind(tmp_path, variant, code):
    with public_fixture(tmp_path, **{variant: True}) as case:
        with pytest.raises(EvidenceError, match=code):
            asyncio.run(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
        assert case.provider.requests == []
        assert case.report["runtime_cleanup_confirmed"] is True
        assert case.report["failure_phase"] == "evidence_staging"
        assert case.report["failure_code"] == code
        assert case.report["evidence"] == []
        assert not (case.workspace / case.manifest.sources[0].artifact).exists()
        case.report["status"] = "failed"
        saved = save_report(case.run_root, case.report)
        assert json.loads(saved.read_bytes())["failure_code"] == code
        assert case.settings.database_path.exists()


def test_actual_mind_reply_is_retained_without_forced_delegation(tmp_path):
    with public_fixture(
        tmp_path, actions=[{"type": "reply", "content": "I cannot delegate this request."}]
    ) as case:
        with pytest.raises(EvidenceError, match="public_mind_did_not_delegate"):
            asyncio.run(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
        assert case.report["mind_turn_id"]
        assert "research_task_id" not in case.report
        assert len(case.report["evidence"]) == 3
        assert case.report["runtime_cleanup_confirmed"]


def test_unexpected_mind_task_is_cancelled_before_dispatch(tmp_path):
    actions = [
        {"type": "core_task", "content": "Echo the request.", "kind": "echo"},
        {"kind": "echo", "payload": {"text": "Unexpected route"}},
    ]
    with public_fixture(tmp_path, actions=actions) as case:
        with pytest.raises(EvidenceError, match="public_mind_task_contract_mismatch"):
            asyncio.run(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
        db = Database(case.settings.database_path)
        try:
            task = TaskService(db, case.settings).get(case.report["research_task_id"])
            assert task.kind == "echo" and task.status == "cancelled"
            assert task.attempts == []
        finally:
            db.close()


def test_init_only_settings_ignore_ambient_models_and_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("ZELO_OLLAMA_GENERAL_MODEL", "untrusted-model")
    monkeypatch.setenv("ZELO_WORKSPACE_ROOT", str(tmp_path))
    settings = workflow_settings(
        ROOT / "runtime" / "public-test-settings", ROOT / "workspaces" / "public-test-settings"
    )
    assert settings.ollama_general_model == MODEL
    assert settings.workspace_root != tmp_path
    assert settings.scheduler_enabled is False and settings.browser_worker_enabled is False


def test_live_entry_rejects_ambient_environment_before_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with pytest.raises(EvidenceError, match="public_environment_not_minimal"):
        qualify(tmp_path, BROWSER_SOURCE_MANIFEST_DIGEST)


def test_preparation_cleanup_failure_retains_cleanup_unproven_report(monkeypatch, tmp_path):
    import os

    from scripts import qualify_public_research as workflow

    for name in tuple(os.environ):
        if name not in workflow.MINIMAL_ENVIRONMENT:
            monkeypatch.delenv(name)

    async def metadata():
        return MODEL_DIGEST

    def failed_preparation(*args):
        raise RuntimeError("browser_preparation_cleanup_unproven")

    monkeypatch.setattr(workflow, "inspect_model", metadata)
    monkeypatch.setattr(workflow, "prepare", failed_preparation)
    path, passed = workflow.qualify(tmp_path, BROWSER_SOURCE_MANIFEST_DIGEST)
    report = json.loads(path.read_bytes())
    try:
        assert passed is False
        assert report["status"] == "cleanup_unproven"
        assert report["failure_phase"] == "browser_preparation"
        assert report["failure_code"] == "browser_preparation_cleanup_unproven"
        assert report["setup_cleanup_errors"] == ["browser_preparation_cleanup_unproven"]
        assert report["external_network_attempted"] is False
    finally:
        shutil.rmtree(ROOT / "workspaces" / ("public-research-" + report["run_id"]))
        shutil.rmtree(path.parent)


def test_cancel_during_claim_accounts_for_thread_and_attempt(monkeypatch, tmp_path):
    import threading

    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    # The events—not a tight wall-clock deadline—prove the cancellation race.
    # Allow a bounded scheduler delay on the single self-hosted full-suite runner
    # without skipping the real claim or the post-cancellation completion check.
    handoff_timeout_seconds = 20
    real_claim = TaskService.claim

    def held_claim(service):
        execution = real_claim(service)
        assert execution is not None
        entered.set()
        try:
            assert release.wait(handoff_timeout_seconds)
            return execution
        finally:
            returned.set()

    monkeypatch.setattr(TaskService, "claim", held_claim)
    with public_fixture(tmp_path) as case:

        async def scenario():
            operation = asyncio.create_task(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, handoff_timeout_seconds)
                operation.cancel()
                await asyncio.sleep(0)  # Deliver cancellation while the real claim is held.
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                assert await asyncio.to_thread(returned.wait, handoff_timeout_seconds)
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                assert await asyncio.to_thread(returned.wait, handoff_timeout_seconds)

        asyncio.run(scenario())
        assert case.provider.requests == []
        assert case.report["runtime_cleanup_confirmed"] is True
        db = Database(case.settings.database_path)
        try:
            with db.read() as session:
                saved = session.scalars(select(Task)).all()
                assert len(saved) == 1
                task_id = saved[0].id
            task = TaskService(db, case.settings).get(task_id)
            assert task.status == "interrupted"
            assert task.error == "service_shutdown"
            assert len(task.attempts) == 1
            assert task.attempts[0].outcome == "interrupted"
        finally:
            db.close()


@pytest.mark.parametrize(
    "gateway_code,expected_code,status",
    [
        ("model_timeout", "model_timeout", 503),
        ("model_unavailable", "model_unavailable", 503),
        ("DO_NOT_PERSIST_GATEWAY_DETAIL", "public_workflow_failed", 409),
    ],
)
def test_mind_gateway_failure_retains_only_allowlisted_code(
    tmp_path, gateway_code, expected_code, status
):
    from app.gateway import ModelGatewayError
    from app.models import ConversationTurn
    from app.policy import DomainError

    provider_error = ModelGatewayError(gateway_code)
    provider_error.args = ("DO_NOT_PERSIST_EXCEPTION_TEXT",)
    with public_fixture(tmp_path, actions=[provider_error]) as case:
        with pytest.raises(DomainError) as caught:
            asyncio.run(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
        assert caught.value.message == "Zelo Mind model unavailable"
        assert caught.value.status_code == status
        assert caught.value.__context__ is provider_error
        assert case.report["failure_phase"] == "mind_turn"
        assert case.report["failure_type"] == "DomainError"
        assert case.report["runtime_cleanup_confirmed"] is True
        assert "mind_turn_id" not in case.report
        assert "research_task_id" not in case.report
        case.report["status"] = "failed"
        raw = save_report(case.run_root, case.report).read_bytes()
        saved = json.loads(raw)
        assert saved["failure_code"] == expected_code
        assert b"DO_NOT_PERSIST" not in raw
        assert b"Zelo Mind model unavailable" not in raw
        db = Database(case.settings.database_path)
        try:
            with db.read() as session:
                assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 0
                assert session.scalar(select(func.count()).select_from(MemoryRecord)) == 0
                kinds = session.scalars(select(Task.kind)).all()
                assert kinds == ["browser_run"] * 3
        finally:
            db.close()


def test_mind_invalid_action_exhaustion_retains_bounded_reason(tmp_path):
    from app.models import ConversationTurn
    from app.policy import DomainError

    malformed = (
        '{"type":"core_task","content":"DO_NOT_PERSIST_INVALID_RESPONSE",'
        '"kind":"research_run","timeout_seconds":48-than-480}'
    )
    with public_fixture(tmp_path, actions=[malformed, malformed]) as case:
        with pytest.raises(DomainError) as caught:
            asyncio.run(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
        assert caught.value.message == "Zelo Mind returned an invalid action"
        assert caught.value.status_code == 502
        assert isinstance(caught.value.__context__, json.JSONDecodeError)
        assert len(case.provider.requests) == case.settings.max_model_parse_retries + 1
        assert case.report["failure_phase"] == "mind_turn"
        assert case.report["failure_type"] == "DomainError"
        assert case.report["runtime_cleanup_confirmed"] is True
        assert case.report["setup_cleanup_errors"] == []
        assert case.report["memory_written"] is False
        assert "mind_turn_id" not in case.report
        assert "research_task_id" not in case.report
        case.report["status"] = "failed"
        raw = save_report(case.run_root, case.report).read_bytes()
        assert b"DO_NOT_PERSIST" not in raw
        assert b"Zelo Mind returned an invalid action" not in raw
        db = Database(case.settings.database_path)
        try:
            with db.read() as session:
                assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 0
                assert session.scalar(select(func.count()).select_from(MemoryRecord)) == 0
                kinds = session.scalars(select(Task.kind)).all()
                assert kinds == ["browser_run"] * 3
        finally:
            db.close()
        assert json.loads(raw)["failure_code"] == "public_mind_invalid_action"


def test_mind_default_timeout_is_cancelled_before_research_dispatch(tmp_path):
    actions = [
        {"type": "core_task", "content": "Investigate the evidence.", "kind": "research_run"},
        {"kind": "research_run", "payload": {"query": "Read the staged evidence."}},
    ]
    with public_fixture(tmp_path, actions=actions) as case:
        with pytest.raises(EvidenceError, match="public_mind_task_contract_mismatch"):
            asyncio.run(
                exercise(
                    case.settings,
                    case.report,
                    runtime=case.runtime,
                    provider=case.provider,
                    manifest=case.manifest,
                )
            )
        assert len(case.provider.requests) == 2
        assert case.report["runtime_cleanup_confirmed"] is True
        assert case.report["setup_cleanup_errors"] == []
        assert case.report["memory_written"] is False
        db = Database(case.settings.database_path)
        try:
            task = TaskService(db, case.settings).get(case.report["research_task_id"])
            assert task.kind == "research_run" and task.status == "cancelled"
            assert task.timeout_seconds == 120
            assert task.attempts == []
            with db.read() as session:
                assert session.scalar(select(func.count()).select_from(MemoryRecord)) == 0
        finally:
            db.close()
