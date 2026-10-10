"""Offline contracts for the opt-in Hindsight runtime; no Hindsight import."""

import asyncio
import builtins
import hashlib
import json
import os
import pwd
import stat
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.research_evidence import MODEL, MODEL_REQUEST_SETTINGS
from scripts import hindsight_runtime as runtime


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    value = runtime.RuntimeProtocol(
        comparison_run_id="a" * 32,
        public_report_sha256="b" * 64,
        memory_receipt_sha256="c" * 64,
        source_commit="d" * 40,
        runtime_script_sha256=runtime.sha256_file(Path(runtime.__file__)),
        wheel_manifest_sha256="e" * 64,
        model=MODEL,
        model_digest="f" * 64,
        model_request_settings=MODEL_REQUEST_SETTINGS,
    )
    paths = runtime.runtime_paths(value)
    paths.root.mkdir(parents=True, mode=0o700)
    paths.inputs.mkdir(mode=0o700)
    paths.protocol.write_bytes(runtime.canonical(asdict(value)) + b"\n")
    paths.protocol.chmod(0o400)
    (paths.root / "postgres.env").write_text(
        "POSTGRES_PASSWORD=" + "1" * 64 + "\nPOSTGRES_DB=zelo_" + "a" * 32 + "\n"
    )
    (paths.root / "postgres.env").chmod(0o600)
    return value


def test_protocol_admits_only_pinned_owned_immutable_file(protocol):
    paths = runtime.runtime_paths(protocol)
    loaded = runtime.load_runtime_protocol(
        paths.protocol, expected_sha256=runtime.sha256_file(paths.protocol)
    )
    assert loaded == protocol
    paths.protocol.chmod(0o600)
    with pytest.raises(runtime.RuntimeRefusal, match="runtime_file_identity"):
        runtime.load_runtime_protocol(
            paths.protocol, expected_sha256=runtime.sha256_file(paths.protocol)
        )


@pytest.mark.parametrize("change", ["digest", "script", "path", "symlink"])
def test_protocol_drift_refused(protocol, tmp_path, change):
    paths = runtime.runtime_paths(protocol)
    target = paths.protocol
    expected = runtime.sha256_file(target)
    if change == "digest":
        expected = "0" * 64
    elif change == "script":
        target.chmod(0o600)
        value = asdict(protocol)
        value["runtime_script_sha256"] = "0" * 64
        target.write_bytes(runtime.canonical(value))
        target.chmod(0o400)
        expected = runtime.sha256_file(target)
    elif change == "path":
        target = tmp_path / "copy.json"
        target.write_bytes(paths.protocol.read_bytes())
        target.chmod(0o400)
    else:
        target = paths.root / "link.json"
        target.symlink_to(paths.protocol)
    with pytest.raises(runtime.RuntimeRefusal):
        runtime.load_runtime_protocol(target, expected_sha256=expected)


@pytest.mark.parametrize(
    "field,value",
    [
        ("retain_timeout_seconds", True),
        ("retain_timeout_seconds", 601),
        ("recall_timeout_seconds", 121),
        ("extraction_max_completion_tokens", 1024),
        ("extraction_max_completion_tokens", True),
        ("model_request_settings", {**MODEL_REQUEST_SETTINGS, "num_ctx": 8192}),
        ("model_request_settings", {**MODEL_REQUEST_SETTINGS, "seed": True}),
        ("model_request_settings", {**MODEL_REQUEST_SETTINGS, "num_predict": 1000}),
        ("model_request_settings", {**MODEL_REQUEST_SETTINGS, "unknown": 1}),
    ],
)
def test_protocol_rejects_undeclared_budget_or_model_settings(protocol, field, value):
    with pytest.raises(runtime.RuntimeRefusal):
        runtime.RuntimeProtocol(**{**asdict(protocol), field: value})


def test_configuration_is_explicit_local_and_current(protocol, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://unrelated.invalid")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://unrelated.invalid")
    monkeypatch.setenv("HINDSIGHT_API_LLM_MODEL", "wrong-model")
    paths = runtime.runtime_paths(protocol)
    (paths.root / ".env").write_text("HINDSIGHT_API_LLM_MODEL=wrong-dotenv-model\n")
    environment = runtime.runtime_environment(protocol)
    assert "HTTP_PROXY" not in environment
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in environment
    assert environment["HINDSIGHT_API_LLM_MODEL"] == MODEL
    assert environment["HINDSIGHT_API_LLM_BASE_URL"] == "http://127.0.0.1:11434"
    assert environment["HINDSIGHT_API_LLM_OLLAMA_NUM_CTX"] == "16384"
    assert environment["HINDSIGHT_API_RETAIN_LLM_TIMEOUT"] == "600"
    assert environment["HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES"] == "0"
    assert environment["HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS"] == "2048"
    assert environment["HINDSIGHT_API_LLM_TEMPERATURE_RETAIN"] == "0.7"
    assert json.loads(environment["HINDSIGHT_API_LLM_EXTRA_BODY"])["options"] == {
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
        "seed": 10102026,
    }
    assert environment["HINDSIGHT_API_EMBEDDINGS_ONNX_BATCH_SIZE"] == "1"
    assert environment["HINDSIGHT_API_RERANKER_FLASHRANK_BATCH_SIZE"] == "1"
    for name in (
        "WORKER_ENABLED",
        "MCP_ENABLED",
        "ENABLE_OBSERVATIONS",
        "ENABLE_AUTO_CONSOLIDATION",
        "ENABLE_BANK_LLM_HEALTH",
        "OTEL_TRACES_ENABLED",
        "LLM_TRACE_ENABLED",
    ):
        assert environment[f"HINDSIGHT_API_{name}"] == "false"
    assert environment["HOME"] == pwd.getpwuid(os.getuid()).pw_dir
    assert environment["HF_HOME"] == str(paths.cache / "huggingface")
    assert environment["XDG_CONFIG_HOME"] == str(paths.root / "config")
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert environment["LITELLM_LOCAL_MODEL_COST_MAP"] == "true"
    assert (
        str(paths.inputs / "e5")
        == environment["HINDSIGHT_API_EMBEDDINGS_ONNX_TOKENIZER_NAME_OR_PATH"]
    )


class Response:
    status = 200

    def __init__(self, value):
        self.value = value

    async def text(self):
        return self.value


class Session:
    def __init__(self, replies=None, block=None):
        self.replies = list(replies or [])
        self.block = block
        self.seen = []
        self.closed = 0

    def post(self, url, **kwargs):
        self.seen.append((url, kwargs))
        session = self

        class Post:
            async def __aenter__(self):
                if session.block:
                    session.block.set()
                    await asyncio.Event().wait()
                return Response(session.replies.pop(0))

            async def __aexit__(self, *args):
                return False

        return Post()

    def get(self):
        return self

    async def close(self):
        self.closed += 1


def request_body(protocol):
    return {
        "model": protocol.model,
        "messages": [{"role": "user", "content": "synthetic source must not be logged"}],
        "stream": False,
        "think": False,
        "format": {"type": "object", "properties": {"facts": {"type": "array"}}},
        "options": {
            "num_batch": 512,
            "num_ctx": 16384,
            **{key: MODEL_REQUEST_SETTINGS[key] for key in runtime.SAMPLE_NAMES},
            "num_predict": 2048,
        },
    }


def asgi_scope(label="retain-r01", path="/v1/default/banks/fixture/memories"):
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"x-zelo-operation-id", label.encode())],
    }


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def test_native_audit_counts_capped_and_recursive_calls_without_content(protocol):
    paths = runtime.runtime_paths(protocol)
    replies = [
        '{"message":{"content":"PRIVATE-OUTPUT"},"prompt_eval_count":100,"eval_count":2048,'
        '"done_reason":"length","eval_duration":123}',
        '{"message":{"content":"half-one"},"prompt_eval_count":60,"eval_count":10,"done_reason":"stop"}',
        '{"message":{"content":"half-two"},"prompt_eval_count":70,"eval_count":11,"done_reason":"stop"}',
    ]
    session = Session(replies)
    audit = runtime.ExtractionAudit(paths.extraction_audit, protocol)
    pool = runtime._AuditedPool(session, audit)
    payload = request_body(protocol)
    returned, sent = [], []

    async def app(scope, receive, send):
        # Three independent actual POST contexts model an output-capped extraction
        # and its two child calls. The observer does not infer the count from retain.
        for _ in range(3):
            async with pool.get().post(
                runtime.OLLAMA_ORIGIN + "/api/chat", json=payload, headers={}
            ) as response:
                returned.append(await response.text())
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"{}"})

    async def send(message):
        sent.append(message)

    asyncio.run(runtime.RuntimeBoundary(app, protocol, audit, {})(asgi_scope(), receive, send))
    assert returned == replies
    assert len(session.seen) == 3
    assert all(kwargs["json"] is payload for _, kwargs in session.seen)
    assert all(kwargs["headers"] == {} for _, kwargs in session.seen)
    raw = paths.extraction_audit.read_bytes()
    assert b"PRIVATE-OUTPUT" not in raw
    assert b"synthetic source" not in raw
    summary = runtime.summarize_extraction_audit(raw, operation_id="retain-r01")
    assert summary["native_attempts"] == 3
    assert summary["complete_responses"] == 3
    assert summary["usage_unknown_attempts"] == 0
    assert summary["input_tokens"] == 230
    assert summary["output_tokens"] == 2069
    assert summary["output_limited_responses"] == 1
    assert summary["operation_outcome"] == "complete"
    assert sent[0]["status"] == 200


def test_aliased_provider_is_observed_once_per_actual_native_post(protocol):
    paths = runtime.runtime_paths(protocol)
    session = Session(['{"prompt_eval_count":5,"eval_count":7,"done_reason":"stop"}'])
    provider = SimpleNamespace(provider="ollama", model=MODEL, timeout=600, _ollama_http=session)
    memory = SimpleNamespace(
        _llm_config=SimpleNamespace(_provider_impl=provider, max_retries=0),
        _retain_llm_config=SimpleNamespace(_provider_impl=provider, max_retries=0),
    )
    audit = runtime.ExtractionAudit(paths.extraction_audit, protocol)
    runtime.install_native_audit(memory, audit)

    async def app(scope, receive, send):
        async with memory._retain_llm_config._provider_impl._ollama_http.get().post(
            runtime.OLLAMA_ORIGIN + "/api/chat", json=request_body(protocol), headers={}
        ) as response:
            await response.text()
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"{}"})

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(runtime.RuntimeBoundary(app, protocol, audit, {})(asgi_scope(), receive, send))
    summary = runtime.summarize_extraction_audit(
        paths.extraction_audit.read_bytes(), operation_id="retain-r01"
    )
    assert len(session.seen) == 1
    assert summary["native_attempts"] == 1
    assert summary["complete_responses"] == 1
    assert summary["input_tokens"] == 5
    assert summary["output_tokens"] == 7
    assert summary["operation_outcome"] == "complete"
    assert sent[0]["status"] == 200


def test_cancelled_native_attempt_preserves_cancellation_and_unknown_usage(protocol):
    paths = runtime.runtime_paths(protocol)
    audit = runtime.ExtractionAudit(paths.extraction_audit, protocol)

    async def scenario():
        entered = asyncio.Event()
        session = Session(block=entered)
        pool = runtime._AuditedPool(session, audit)

        async def app(scope, receive, send):
            async with pool.get().post(
                runtime.OLLAMA_ORIGIN + "/api/chat", json=request_body(protocol), headers={}
            ):
                pytest.fail("The blocked request cannot have returned")

        async def send(message):
            pytest.fail("A cancelled operation cannot return success")

        task = asyncio.create_task(
            runtime.RuntimeBoundary(app, protocol, audit, {})(asgi_scope(), receive, send)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(session.seen) == 1

    asyncio.run(scenario())
    summary = runtime.summarize_extraction_audit(
        paths.extraction_audit.read_bytes(), operation_id="retain-r01"
    )
    assert summary["native_attempts"] == 1
    assert summary["usage_unknown_attempts"] == 1
    assert summary["cancelled_attempts"] == 1
    assert summary["unfinished_attempts"] == 0
    assert summary["input_tokens"] == 0
    assert summary["operation_outcome"] == "interrupted"


def test_absolute_operation_timeout_stops_waiting_handler(protocol, monkeypatch):
    paths = runtime.runtime_paths(protocol)
    audit = runtime.ExtractionAudit(paths.extraction_audit, protocol)
    monkeypatch.setattr(runtime, "RETAIN_SECONDS", 0.01)
    sent, cancelled = [], []

    async def app(scope, receive, send):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def send(message):
        sent.append(message)

    asyncio.run(runtime.RuntimeBoundary(app, protocol, audit, {})(asgi_scope(), receive, send))
    assert cancelled == [True]
    assert sent[0]["status"] == 504
    summary = runtime.summarize_extraction_audit(
        paths.extraction_audit.read_bytes(), operation_id="retain-r01"
    )
    assert summary["operation_outcome"] == "timeout"
    assert summary["native_attempts"] == 0


def test_missing_operation_label_refuses_before_handler(protocol):
    paths = runtime.runtime_paths(protocol)
    audit = runtime.ExtractionAudit(paths.extraction_audit, protocol)
    sent = []

    async def app(*args):
        pytest.fail("Unlabelled work must not enter the engine")

    async def send(message):
        sent.append(message)

    scope = asgi_scope()
    scope["headers"] = []
    asyncio.run(runtime.RuntimeBoundary(app, protocol, audit, {})(scope, receive, send))
    assert sent[0]["status"] == 400
    assert paths.extraction_audit.read_bytes() == b""


def test_stale_pid_refuses_before_signal(protocol, monkeypatch):
    paths = runtime.runtime_paths(protocol)
    readiness = {
        "pid": 54321,
        "process_starttime": 10,
        "run_id": protocol.comparison_run_id,
        "runtime_protocol_sha256": runtime.sha256_file(paths.protocol),
        "extraction_audit": str(paths.extraction_audit),
    }
    paths.readiness.write_bytes(runtime.canonical(readiness))
    paths.readiness.chmod(0o400)
    monkeypatch.setattr(runtime, "process_starttime", lambda pid: 11)
    monkeypatch.setattr(
        runtime.os, "pidfd_open", lambda pid: pytest.fail("Reused PID must not open")
    )
    with pytest.raises(runtime.RuntimeRefusal, match="runtime_pid_reused"):
        runtime.stop_runtime(protocol, readiness)
    assert not (paths.root / "stop-receipt.json").exists()


def test_explicit_factory_uses_library_no_dotenv_and_writes_startup_identity(protocol, monkeypatch):
    paths = runtime.runtime_paths(protocol)
    monkeypatch.setattr(runtime, "verify_runtime_assets", lambda protocol: {})
    monkeypatch.setattr(runtime, "verify_installed_runtime", lambda protocol: None)
    monkeypatch.setattr(runtime.os, "environ", {"HTTP_PROXY": "http://ambient.invalid"})
    seen = {}

    def engine(**kwargs):
        seen["engine"] = kwargs
        assert "HTTP_PROXY" not in runtime.os.environ
        assert runtime.os.environ["HINDSIGHT_API_LLM_MODEL"] == MODEL

        def provider():
            return SimpleNamespace(
                provider="ollama", model=MODEL, timeout=600, _ollama_http=Session()
            )

        return SimpleNamespace(
            _llm_config=SimpleNamespace(_provider_impl=provider(), max_retries=0),
            _retain_llm_config=SimpleNamespace(_provider_impl=provider(), max_retries=0),
        )

    async def app(scope, receive, send):
        assert scope["type"] == "lifespan"
        await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})

    def create_app(**kwargs):
        seen["app"] = kwargs
        return app

    def run(boundary, **kwargs):
        seen["uvicorn"] = kwargs
        sent = []

        async def send(message):
            sent.append(message)

        asyncio.run(boundary({"type": "lifespan"}, receive, send))
        assert sent[0]["type"] == "lifespan.startup.complete"

    monkeypatch.setitem(sys.modules, "hindsight_api", SimpleNamespace(MemoryEngine=engine))
    monkeypatch.setitem(sys.modules, "hindsight_api.api", SimpleNamespace(create_app=create_app))
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))
    monkeypatch.setattr(runtime.logging, "disable", lambda level: None)
    runtime.serve_runtime(protocol)
    assert seen["engine"]["run_migrations"] is True
    assert seen["engine"]["skip_llm_verification"] is True
    assert seen["app"]["mcp_api_enabled"] is False
    assert seen["app"]["initialize_memory"] is True
    assert seen["uvicorn"]["host"] == "127.0.0.1"
    assert seen["uvicorn"]["workers"] == 1
    receipt = json.loads(paths.readiness.read_bytes())
    assert receipt["runtime_protocol_sha256"] == runtime.sha256_file(paths.protocol)
    assert receipt["state"] == "startup_complete"
    assert receipt["process_starttime"] == runtime.process_starttime(runtime.os.getpid())
    assert receipt["external_exports"] is False
    assert paths.readiness.stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize(
    "failing_phase,exception_class",
    [("imports", "ModuleNotFoundError"), ("engine", "ValueError")],
)
def test_startup_failure_keeps_bounded_class_and_phase_without_error_text(
    protocol, monkeypatch, capsys, failing_phase, exception_class
):
    paths = runtime.runtime_paths(protocol)
    monkeypatch.setattr(runtime, "verify_runtime_assets", lambda protocol: {})
    monkeypatch.setattr(runtime, "verify_installed_runtime", lambda protocol: None)
    monkeypatch.setattr(runtime.os, "environ", {})
    monkeypatch.setattr(runtime.logging, "disable", lambda level: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hindsight_runtime.py",
            "serve",
            "--protocol",
            str(paths.protocol),
            "--expected-protocol-sha256",
            runtime.sha256_file(paths.protocol),
        ],
    )

    def engine(**kwargs):
        raise ValueError("PRIVATE-STARTUP-CREDENTIAL")

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "hindsight_api", SimpleNamespace(MemoryEngine=engine))
    monkeypatch.setitem(
        sys.modules,
        "hindsight_api.api",
        SimpleNamespace(create_app=lambda **kwargs: pytest.fail("No application may start")),
    )
    original_import = builtins.__import__

    def import_module(name, *args, **kwargs):
        if name == "hindsight_api" and failing_phase == "imports":
            raise ModuleNotFoundError("PRIVATE-STARTUP-CREDENTIAL")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    assert runtime.main() == 1
    output = capsys.readouterr()
    assert "PRIVATE-STARTUP-CREDENTIAL" not in output.out + output.err
    assert json.loads(output.out) == {
        "status": "failed",
        "code": "runtime_failed",
        "action": "serve",
        "phase": failing_phase,
        "exception_class": exception_class,
    }
    assert not paths.readiness.exists()
    assert not paths.extraction_audit.exists()


@pytest.mark.parametrize("missing_model", [False, True])
def test_flashrank_metadata_is_preserved_only_in_archive(protocol, monkeypatch, missing_model):
    paths = runtime.runtime_paths(protocol)
    files = {
        "flashrank-MiniLM-L-12-v2_Q.onnx": b"synthetic ONNX",
        "tokenizer_config.json": b'{"tokenizer_class":"fixture"}',
        "special_tokens_map.json": b'{"unk_token":"[UNK]"}',
        "config.json": b'{"model_type":"fixture"}',
        "tokenizer.json": b'{"fixture":true}',
    }
    model = "ms-marco-MiniLM-L-12-v2"
    archive_path = paths.inputs / (model + ".zip")
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(model + "/", b"")
        archive.writestr("__MACOSX/._" + model, b"metadata")
        archive.writestr(model + "/.DS_Store", b"finder metadata")
        archive.writestr("__MACOSX/" + model + "/._.DS_Store", b"metadata")
        for name, content in files.items():
            if missing_model and name == "tokenizer.json":
                continue
            archive.writestr(model + "/" + name, content)
            archive.writestr("__MACOSX/" + model + "/._" + name, b"metadata")
    original_archive = archive_path.read_bytes()
    uv_path = paths.inputs / "uv-0.13.0.whl"
    with zipfile.ZipFile(uv_path, "w") as archive:
        archive.writestr("uv.data/purelib/uv/uv", b"\x7fELFsynthetic; never executed")
    paths.source.mkdir()
    (paths.source / "fixture.py").write_text("# inert source fixture\n")
    monkeypatch.setattr(runtime, "SLIM_TREE_SHA1", runtime._git_tree(paths.source))
    for name, constant in [
        ("uv.lock", "LOCK_BLOB_SHA1"),
        ("LICENSE.Hindsight", "LICENSE_BLOB_SHA1"),
    ]:
        path = paths.inputs / name
        path.write_text("synthetic pinned input\n")
        monkeypatch.setattr(runtime, constant, runtime._git_blob(path))
    monkeypatch.setattr(
        runtime,
        "ASSET_PINS",
        {
            path.name: (path.stat().st_size, runtime.sha256_file(path))
            for path in [archive_path, uv_path]
        },
    )
    # Installation/dependency planning is outside this archive-staging contract.
    monkeypatch.setattr(
        runtime, "build_runtime_plan", lambda _: {"requirements": {"build": "", "runtime": ""}}
    )
    (paths.root / "postgres.env").unlink()  # Remove only the fixture's dummy configuration.
    if missing_model:
        with pytest.raises(runtime.RuntimeRefusal, match="runtime_flashrank_incomplete"):
            runtime.prepare_runtime_files(protocol)
        assert not paths.cache.exists()
    else:
        result = runtime.prepare_runtime_files(protocol)
        expected_names = {model + "/" + name for name in files}
        assert set(result["flashrank_members"]) == expected_names
        actual_files = {
            str(path.relative_to(paths.cache / "flashrank"))
            for path in (paths.cache / "flashrank").rglob("*")
            if path.is_file()
        }
        assert actual_files == expected_names
        for name, content in files.items():
            assert (paths.flashrank / name).read_bytes() == content
        assert not (paths.cache / "flashrank" / "__MACOSX").exists()
        assert not (paths.flashrank / ".DS_Store").exists()
    assert archive_path.read_bytes() == original_archive


@pytest.mark.parametrize("kind", ["traversal", "symlink"])
def test_ignored_flashrank_metadata_keeps_path_and_link_checks(tmp_path, kind):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(
            "__MACOSX/../escape" if kind == "traversal" else "__MACOSX/._ms-marco-MiniLM-L-12-v2"
        )
        if kind == "symlink":
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"untrusted")
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(runtime.RuntimeRefusal, match="runtime_archive_path"):
            runtime._flashrank_members(archive)


def test_untrusted_zip_member_is_refused_before_extract(tmp_path):
    target = tmp_path / "archive.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../outside", b"untrusted")
    with zipfile.ZipFile(target) as archive:
        with pytest.raises(runtime.RuntimeRefusal, match="runtime_archive_path"):
            runtime._zip_members(archive)
    assert not (tmp_path.parent / "outside").exists()


def test_missing_offline_wheel_blocks_plan_before_commands(protocol):
    paths = runtime.runtime_paths(protocol)
    # A protocol's trusted manifest digest does not waive local input validation.
    with pytest.raises(runtime.RuntimeRefusal, match="runtime_input_missing"):
        runtime.build_runtime_plan(protocol)
    assert not paths.venv.exists()
    assert not (paths.root / "offline-plan.json").exists()


def _wheelhouse_fixture(
    protocol, monkeypatch, *, extra_name=None, extra_lock="", missing_size=None
):
    paths = runtime.runtime_paths(protocol)
    (paths.inputs / "wheels").mkdir(mode=0o700)
    rows, packages, build_pins = [], [extra_lock], {}
    roles = {
        "build": list(runtime.BUILD_WHEELS),
        "runtime": [
            "flashrank",
            "onnxruntime",
            "transformers",
            "tokenizers",
            "numpy",
        ]
        + ([extra_name] if extra_name else []),
    }
    for role, names in roles.items():
        for name in names:
            body = ("synthetic wheel " + name).encode()
            digest = hashlib.sha256(body).hexdigest()
            filename = name.replace("-", "_") + "-1.0-py3-none-any.whl"
            url = "https://files.pythonhosted.org/" + filename
            (paths.inputs / "wheels" / filename).write_bytes(body)
            rows.append(
                dict(
                    name=name,
                    version="1.0",
                    filename=filename,
                    url=url,
                    sha256=digest,
                    size=len(body),
                    role=role,
                )
            )
            if role == "build":
                build_pins[name] = ("1.0", len(body), digest)
            else:
                packages.append(
                    "[[package]]\nname="
                    + json.dumps(name)
                    + '\nversion="1.0"\n'
                    + "[[package.wheels]]\nurl="
                    + json.dumps(url)
                    + '\nhash="sha256:'
                    + digest
                    + '"\n'
                    + ("" if name == missing_size else "size=" + str(len(body)) + "\n")
                )
    lock = paths.inputs / "uv.lock"
    lock.write_text("\n".join(packages))
    monkeypatch.setattr(runtime, "LOCK_BLOB_SHA1", runtime._git_blob(lock))
    monkeypatch.setattr(runtime, "BUILD_WHEELS", build_pins)
    manifest = paths.inputs / "wheel-manifest.json"
    manifest.write_bytes(
        runtime.canonical({"schema": "zelo-hindsight-wheelhouse-v1", "wheels": rows})
    )
    manifest.chmod(0o400)
    fixture_protocol = runtime.RuntimeProtocol(
        **{**asdict(protocol), "wheel_manifest_sha256": runtime.sha256_file(manifest)}
    )
    return fixture_protocol


@pytest.mark.parametrize("forbidden_name", ["triton", "onnxruntime-gpu", "mlx"])
def test_cpu_plan_rejects_accelerator_wheel_even_when_lock_and_bytes_match(
    protocol, monkeypatch, forbidden_name
):
    fixture_protocol = _wheelhouse_fixture(protocol, monkeypatch, extra_name=forbidden_name)
    paths = runtime.runtime_paths(protocol)
    with pytest.raises(runtime.RuntimeRefusal, match="^runtime_wheel_manifest$"):
        runtime.build_runtime_plan(fixture_protocol)
    assert not paths.venv.exists()
    assert not (paths.root / "offline-plan.json").exists()


@pytest.mark.parametrize("missing_size", [None, "onnxruntime"])
def test_unselected_lock_wheels_may_omit_size_but_selected_wheels_must_match(
    protocol, monkeypatch, missing_size
):
    # These two entries reproduce the exact omitted-size shape in the pinned
    # uv.lock. They are outside the selected CPU-only closure.
    unselected = """
[[package]]
name = "torch"
version = "2.13.0"
[[package.wheels]]
url = "https://download-r2.pytorch.org/whl/cpu/torch-2.13.0-cp311-cp311-macosx_14_0_arm64.whl"
hash = "sha256:e76f9bcecc52b8ff711239a2f7547d5353df95878ab232f0773c1d95928b92f8"
upload-time = "2026-07-08T12:26:13Z"
[[package]]
name = "torch"
version = "2.13.0+cpu"
[[package.wheels]]
url = "https://download-r2.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp311-cp311-linux_s390x.whl"
hash = "sha256:6e9817dbdf5ea76789babd46e457eac5bf14ff566cf85f8addbfdff2d56601ce"
upload-time = "2026-07-08T19:27:52Z"
"""
    fixture_protocol = _wheelhouse_fixture(
        protocol, monkeypatch, extra_lock=unselected, missing_size=missing_size
    )
    if missing_size:
        with pytest.raises(runtime.RuntimeRefusal, match="^runtime_wheel_unpinned$"):
            runtime.build_runtime_plan(fixture_protocol)
    else:
        plan = runtime.build_runtime_plan(fixture_protocol)
        assert "torch" not in plan["requirements"]["runtime"]
        assert "onnxruntime==1.0 --hash=sha256:" in plan["requirements"]["runtime"]


def test_offline_plan_uses_separate_environments_and_exact_local_inputs(protocol, monkeypatch):
    rows = [
        {"name": "hatchling", "version": "1.27.0", "sha256": "1" * 64, "role": "build"},
        {"name": "onnxruntime", "version": "1.20.1", "sha256": "2" * 64, "role": "runtime"},
    ]
    monkeypatch.setattr(runtime, "_wheel_manifest", lambda protocol: rows)
    plan = runtime.build_runtime_plan(protocol)
    paths = runtime.runtime_paths(protocol)
    commands = [row["argv"] for row in plan["commands"]]
    assert commands[0][-1] == str(paths.build_venv)
    assert commands[1][-1] == str(paths.venv)
    assert all("--no-project" in command for command in commands[:2])
    assert "hatchling==1.27.0 --hash=sha256:" in plan["requirements"]["build"]
    assert "onnxruntime==1.20.1 --hash=sha256:" in plan["requirements"]["runtime"]
    install = [command for command in commands if "--require-hashes" in command]
    assert len(install) == 2
    assert all("--offline" in command and "--no-index" in command for command in install)
    assert all("--only-binary" in command for command in install)
    assert commands[-1][-1] == runtime.PG_IMAGE
    assert "127.0.0.1:15432:5432" in commands[-1]
    assert "--pull=never" in commands[-1]
    assert plan["environment"]["HOME"] == pwd.getpwuid(os.getuid()).pw_dir
    assert plan["environment"]["UV_NO_CONFIG"] == "1"
    assert plan["environment"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert plan["environment"]["PYTHON_DOTENV_DISABLED"] == "1"
    assert plan["environment"]["XDG_CONFIG_HOME"] == str(paths.root / "config")
    assert "HINDSIGHT_API_DATABASE_URL" not in plan["environment"]
    assert plan["serve_argv"][1:3] == ["-I", "-B"]
    assert not paths.venv.exists()
    assert not paths.build_venv.exists()


def test_stop_targets_verified_pidfd_and_preserves_evidence(protocol, monkeypatch):
    paths = runtime.runtime_paths(protocol)
    readiness = {
        "pid": 54321,
        "process_starttime": 10,
        "run_id": protocol.comparison_run_id,
        "runtime_protocol_sha256": runtime.sha256_file(paths.protocol),
        "extraction_audit": str(paths.extraction_audit),
    }
    retained = runtime.canonical(readiness)
    paths.readiness.write_bytes(retained)
    paths.readiness.chmod(0o400)
    states = iter([10, 10, None, None])
    monkeypatch.setattr(runtime, "process_starttime", lambda pid: next(states, None))
    opened, sent, closed = [], [], []
    monkeypatch.setattr(runtime.os, "pidfd_open", lambda pid: opened.append(pid) or 98765)
    monkeypatch.setattr(runtime.signal, "pidfd_send_signal", lambda fd, sig: sent.append((fd, sig)))
    original_close = runtime.os.close

    def close(fd):
        if fd == 98765:
            closed.append(fd)
        else:
            original_close(fd)

    monkeypatch.setattr(runtime.os, "close", close)
    result = runtime.stop_runtime(protocol, readiness)
    assert opened == [54321]
    assert sent == [(98765, runtime.signal.SIGTERM)]
    assert closed == [98765]
    assert result["api_process_gone"] is True
    assert result["forced"] is False
    assert result["postgres_preserved"] is True
    assert paths.readiness.read_bytes() == retained
    assert (paths.root / "stop-receipt.json").stat().st_mode & 0o777 == 0o400
