"""v0.15 bounded read-only API Worker tests."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.agent_execution_contracts import AgentExecutionBinding
from app.agent_factory import AgentSpec, WorkerRequirement
from app.api_contracts import ApiEndpointRegistry
from app.api_worker import API_RESPONSE_LIMIT, ApiRuntime, ApiRuntimeError
from app.models import Task
from app.policy import DomainError
from app.runner import Runner
from app.schemas import ApiRunInput
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_api_worker


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    assert host == "api.example.com"
    assert port == 443
    return ("93.184.216.34",)


def runtime(handler, *, resolver=public_resolver, origin="https://api.example.com") -> ApiRuntime:
    registry = ApiEndpointRegistry.from_entries((f"example={origin}",))

    def streamed(request):
        response = handler(request)
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )
        return response

    return ApiRuntime(
        registry,
        transport=httpx.MockTransport(streamed),
        resolver=resolver,
    )


def supervisor(value: ApiRuntime) -> Supervisor[ApiRuntime]:
    return Supervisor(WorkerRegistry(((descriptor_for_api_worker(), value),)))


def run_api(value: ApiRuntime, *, path="/v1/data", query=None) -> dict:
    _, selection = supervisor(value).select(role="api", capabilities=("api_read",))
    spec = value.bind(selection, "example")
    return asyncio.run(
        value.run(
            spec=spec,
            path=path,
            query=query or {},
            timeout_seconds=2,
            emit=lambda *_: None,
            authorize_request=lambda: True,
        )
    )


@pytest.mark.parametrize(
    "entry",
    [
        "example=http://api.example.com",
        "example=https://localhost",
        "example=https://127.0.0.1",
        "example=https://api.example.com/path",
        "example=https://user:pass@api.example.com",
        "example=https://API.example.com",
        "example=https://api.example.com/",
    ],
)
def test_endpoint_registry_rejects_noncanonical_or_unsafe_origins(entry):
    with pytest.raises((ValueError, ValidationError)):
        ApiEndpointRegistry.from_entries((entry,))


def test_endpoint_registry_version_is_deterministic_and_material():
    first = ApiEndpointRegistry.from_entries(("b=https://b.example.com", "a=https://a.example.com"))
    second = ApiEndpointRegistry.from_entries(
        ("a=https://a.example.com", "b=https://b.example.com")
    )
    changed = ApiEndpointRegistry.from_entries(
        ("a=https://a.example.com", "b=https://c.example.com")
    )
    assert first.version == second.version
    assert first.version != changed.version


def test_api_runtime_requires_core_request_authority():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, json={})

    value = runtime(handle)
    _, selection = supervisor(value).select(role="api", capabilities=("api_read",))
    with pytest.raises(ApiRuntimeError, match="api_request_authority_unavailable"):
        asyncio.run(
            value.run(
                spec=value.bind(selection, "example"),
                path="/v1",
                query={},
                timeout_seconds=2,
                emit=lambda *_: None,
            )
        )
    assert requests == []


def test_runtime_pins_public_ip_and_preserves_https_host():
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "93.184.216.34"
        assert request.url.path == "/v1/data"
        assert request.url.params["q"] == "safe"
        assert request.headers["host"] == "api.example.com"
        assert "authorization" not in request.headers
        assert request.extensions["sni_hostname"] == "api.example.com"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True},
        )

    result = run_api(runtime(handle), query={"q": "safe"})
    assert result["status_code"] == 200
    assert result["body"] == {"ok": True}
    assert result["response_bytes"] > 0


def test_runtime_rejects_private_resolution():
    async def private_resolver(_host: str, _port: int) -> tuple[str, ...]:
        return ("127.0.0.1",)

    value = runtime(
        lambda _request: httpx.Response(200, json={"unsafe": True}),
        resolver=private_resolver,
    )
    with pytest.raises(ApiRuntimeError, match="api_target_not_public"):
        run_api(value)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(
                302,
                headers={
                    "location": "https://other.example.com/",
                    "content-type": "application/json",
                },
                json={},
            ),
            "api_redirect_denied",
        ),
        (
            httpx.Response(200, headers={"content-type": "text/html"}, text="<b>x</b>"),
            "api_content_type_denied",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"x" * (API_RESPONSE_LIMIT + 1),
            ),
            "api_response_too_large",
        ),
    ],
)
def test_runtime_rejects_redirect_content_type_and_oversize(response, error):
    value = runtime(lambda _request: response)
    with pytest.raises(ApiRuntimeError, match=error):
        run_api(value)


@pytest.mark.parametrize(
    "payload",
    [
        {"endpoint_id": "example", "path": "/../secrets"},
        {"endpoint_id": "example", "path": "//other.example.com/x"},
        {"endpoint_id": "example", "path": "/x?q=1"},
        {"endpoint_id": "example", "path": "/safe", "query": {"x\nheader": "bad"}},
        {"endpoint_id": "example", "path": "/safe", "authorization": "secret"},
    ],
)
def test_api_input_rejects_path_query_or_credential_escape(payload):
    with pytest.raises(ValidationError):
        ApiRunInput(kind="api_run", payload=payload)


def task_runtime(origin="https://api.example.com") -> ApiRuntime:
    return runtime(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"ok": True},
        ),
        origin=origin,
    )


def test_submission_persists_exact_api_execution_spec(settings, database):
    value = task_runtime()
    service = TaskService(database, settings, api_supervisor=supervisor(value))
    created, was_created = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    assert was_created is True
    assert created.api_spec is not None
    assert created.api_spec.worker_selection.descriptor.worker_id == "api_core"
    assert created.api_spec.worker_selection.authority_profile == (
        "public-api-read-no-credentials-v1"
    )
    assert created.api_spec.endpoint.endpoint.origin == "https://api.example.com"

    execution = service.claim()
    assert execution is not None
    assert execution.api_request == created.api_spec


def test_api_execution_spec_fails_closed_if_endpoint_registry_changes(settings, database):
    first = task_runtime()
    service = TaskService(database, settings, api_supervisor=supervisor(first))
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    service.api_supervisor = supervisor(task_runtime("https://other.example.com"))

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "api_execution_spec_invalid"


def test_api_retry_rebinds_current_endpoint_registry(settings, database):
    first = task_runtime()
    service = TaskService(database, settings, api_supervisor=supervisor(first))
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    service.cancel(created.id)
    service.api_supervisor = supervisor(task_runtime("https://other.example.com"))

    retried = service.retry(created.id)
    assert retried.action_version == 2
    assert retried.api_spec is not None
    assert retried.api_spec.endpoint.endpoint.origin == "https://other.example.com"


def test_api_action_hash_binds_execution_spec(settings, database):
    value = task_runtime()
    service = TaskService(database, settings, api_supervisor=supervisor(value))
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    with database.transaction() as session:
        persisted = session.get(Task, created.id)
        assert persisted is not None
        tampered = dict(persisted.api_spec)
        selection = dict(tampered["worker_selection"])
        selection["excluded_worker_ids"] = ["other_worker"]
        tampered["worker_selection"] = selection
        persisted.api_spec = tampered

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "action_changed"


def test_runner_requires_bound_api_execution_spec(settings, database):
    value = task_runtime()
    api_supervisor = supervisor(value)
    service = TaskService(database, settings, api_supervisor=api_supervisor)
    service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    runner = Runner(service, api_supervisor=api_supervisor)

    with pytest.raises(ApiRuntimeError, match="api_execution_spec_unavailable"):
        asyncio.run(runner._api_worker(replace(execution, api_request=None)))


def test_runner_rejects_stale_api_execution_spec(settings, database):
    first = task_runtime()
    service = TaskService(database, settings, api_supervisor=supervisor(first))
    service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    runner = Runner(
        service,
        api_supervisor=supervisor(task_runtime("https://other.example.com")),
    )

    with pytest.raises(ApiRuntimeError, match="api_execution_spec_stale"):
        asyncio.run(runner._api_worker(execution))


@pytest.mark.parametrize("invalidate", ["cancel", "endpoint_change"])
def test_api_task_cannot_send_after_authority_changes_during_dns(settings, database, invalidate):
    entered = asyncio.Event()
    proceed = asyncio.Event()
    requests = []

    async def held_resolver(host, port):
        entered.set()
        await proceed.wait()
        return await public_resolver(host, port)

    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    value = runtime(handle, resolver=held_resolver)
    api_supervisor = supervisor(value)
    service = TaskService(database, settings, api_supervisor=api_supervisor)
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
        None,
    )
    execution = service.claim()
    assert execution is not None and execution.task_id == created.id

    async def exercise():
        running = asyncio.create_task(
            Runner(service, api_supervisor=api_supervisor)._run(execution)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        if invalidate == "cancel":
            service.cancel(created.id)
        else:
            service.api_supervisor = supervisor(task_runtime("https://other.example.com"))
        proceed.set()
        await asyncio.wait_for(running, timeout=5)

    asyncio.run(exercise())
    current = service.get(created.id)
    assert current.status == ("cancelled" if invalidate == "cancel" else "failed")
    assert current.result is None
    assert requests == []


def test_agent_api_task_refused_before_database_write(settings, database):
    spec = AgentSpec(
        agent_id="api_reader",
        role="api_reader",
        objective="Read a configured API",
        capabilities=("api_read",),
        allowed_task_kinds=("api_run",),
        memory_namespace="agent/api_reader",
        worker_requirement=WorkerRequirement(role="api", capabilities=("api_read",)),
        lifecycle="active",
    )
    binding = AgentExecutionBinding(
        agent_instance_id="11111111-1111-4111-8111-111111111111",
        project_id="22222222-2222-4222-8222-222222222222",
        registry_version="a" * 64,
        spec=spec,
        task_kind="api_run",
    )
    service = TaskService(database, settings, api_supervisor=supervisor(task_runtime()))

    with pytest.raises(DomainError, match="Agent API execution unavailable"):
        service.submit(
            ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/v1"}),
            None,
            binding,
        )


def test_api_runner_marks_non_success_http_status_failed(settings, database):
    value = runtime(
        lambda _request: httpx.Response(
            404,
            headers={"content-type": "application/json"},
            json={"error": "missing"},
        )
    )
    api_supervisor = supervisor(value)
    service = TaskService(database, settings, api_supervisor=api_supervisor)
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/missing"}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    asyncio.run(Runner(service, api_supervisor=api_supervisor)._run(execution))

    failed = service.get(created.id)
    assert failed.status == "failed"
    assert failed.error == "api_http_status"
    assert failed.result["status_code"] == 404
    assert "api_failed" in [item.event for item in service.events(created.id, 100, 0)]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::",
        "::1",
        "ff02::1",
        "fe80::1",
        "fc00::1",
        "::ffff:93.184.216.34",
        "2002:5db8:d822::1",
        "2001:db8::1",
        "fe80::1%eth0",
    ],
)
def test_any_unsafe_dns_answer_denies_before_transport(address):
    async def mixed_resolver(_host, _port):
        return ("93.184.216.34", address)

    def forbidden(_request):
        pytest.fail("unsafe DNS answer reached transport")

    with pytest.raises(ApiRuntimeError, match="api_target_not_public"):
        run_api(runtime(forbidden, resolver=mixed_resolver))


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/x",
        "/%252e/x",
        "/x%2fy",
        "/x//y",
        "/./x",
        "/x/..",
        "/x\\y",
        "/a b",
        "/é",
        "/x\n",
        "/x\t",
        "/x#fragment",
    ],
)
def test_noncanonical_paths_denied_at_runtime(path):
    def forbidden(_request):
        pytest.fail("invalid path reached transport")

    with pytest.raises(ApiRuntimeError, match="api_input_invalid"):
        run_api(runtime(forbidden), path=path)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b" ",
        b"<html/>",
        b'"\xff"',
        b'{"x":1,"x":2}',
        b"NaN",
        b"1e999",
        b'"\\ud800"',
        b"[" * 66 + b"0" + b"]" * 66,
        b"1 2",
    ],
)
def test_invalid_json_denied(body):
    value = runtime(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        )
    )
    with pytest.raises(ApiRuntimeError, match="api_response_invalid"):
        run_api(value)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data, size):
        self.data, self.size = data, size

    async def __aiter__(self):
        for offset in range(0, len(self.data), self.size):
            yield self.data[offset : offset + self.size]


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("chunk_size", [1, 65536])
@pytest.mark.parametrize("case", ["valid", "limit", "bomb", "truncated", "trailing"])
def test_compressed_response_bounds(encoding, chunk_size, case):
    import zlib

    body = b'{"ok":true}'
    if case in {"limit", "bomb"}:
        body = b'"' + b"x" * (API_RESPONSE_LIMIT - 2 + (case == "bomb")) + b'"'
    compressor = zlib.compressobj(wbits=31 if encoding == "gzip" else 15)
    wire = compressor.compress(body) + compressor.flush()
    if case == "truncated":
        wire = wire[:-1]
    if case == "trailing":
        wire += b"junk"
    value = runtime(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": encoding},
            stream=Chunks(wire, chunk_size),
        )
    )
    if case in {"valid", "limit"}:
        assert run_api(value)["response_bytes"] == len(body)
    else:
        error = "api_response_too_large" if case == "bomb" else "api_response_invalid"
        with pytest.raises(ApiRuntimeError, match=error):
            run_api(value)


def test_real_transport_preserves_pinned_tcp_and_verified_tls_identity(monkeypatch):
    import ssl

    from httpcore._backends.auto import AutoBackend

    observed = []

    class Stream:
        async def start_tls(self, ssl_context, server_hostname, timeout):
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname is True
            assert server_hostname == "api.example.com"
            observed.append("tls")
            return self

        async def write(self, buffer, timeout=None):
            observed.append(buffer)

        async def read(self, max_bytes, timeout=None):
            return (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
            )

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    async def connect(_self, host, port, **kwargs):
        assert host == "93.184.216.34"
        assert port == 443
        observed.append("tcp")
        return Stream()

    monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
    for name in ("HTTPS_PROXY", "ALL_PROXY", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    value = ApiRuntime(
        ApiEndpointRegistry.from_entries(("example=https://api.example.com",)),
        resolver=public_resolver,
    )
    assert run_api(value)["body"] == {}
    assert observed[:2] == ["tcp", "tls"]
    wire = b"".join(item for item in observed if isinstance(item, bytes)).lower()
    assert b"host: api.example.com\r\n" in wire
    assert b"authorization:" not in wire
    assert b"cookie:" not in wire


def test_dns_resolution_is_inside_total_deadline():
    async def slow(_host, _port):
        await asyncio.sleep(1)
        pytest.fail("deadline did not cancel resolver")

    value = runtime(lambda _: pytest.fail("unexpected transport"), resolver=slow)
    _, selection = supervisor(value).select(role="api", capabilities=("api_read",))
    with pytest.raises(ApiRuntimeError, match="api_timeout"):
        asyncio.run(
            value.run(
                spec=value.bind(selection, "example"),
                path="/",
                query={},
                timeout_seconds=0.1,
                emit=lambda *_: None,
                authorize_request=lambda: True,
            )
        )


def test_registry_and_endpoint_cannot_be_mutated():
    from dataclasses import FrozenInstanceError

    registry = ApiEndpointRegistry.from_entries(("example=https://api.example.com",))
    with pytest.raises(FrozenInstanceError):
        registry._version = "0" * 64
    with pytest.raises(TypeError):
        registry._endpoints["other"] = registry.get("example")
    with pytest.raises(ValidationError):
        registry.get("example").origin = "https://other.example.com"


@pytest.mark.parametrize("phase", ["claim", "runner"])
def test_policy_change_invalidates_selection(settings, database, monkeypatch, phase):
    import app.api_worker as module

    value = task_runtime()
    selected = supervisor(value)
    service = TaskService(database, settings, api_supervisor=selected)
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/"}),
        None,
    )
    execution = service.claim() if phase == "runner" else None
    monkeypatch.setattr(module, "API_POLICY_SCHEMA", "changed-policy")
    if phase == "claim":
        assert service.claim() is None
        assert service.get(created.id).error == "api_execution_spec_invalid"
    else:
        with pytest.raises(ApiRuntimeError, match="api_execution_spec_stale"):
            asyncio.run(Runner(service, api_supervisor=selected)._api_worker(execution))


@pytest.mark.parametrize(
    "query",
    [{"q": "x" * 2049}, {"x" * 129: "v"}, {str(i): "v" for i in range(33)}, {"q": "é" * 2048}],
)
def test_query_limits(query):
    with pytest.raises(ValidationError):
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/", "query": query})


def test_mind_does_not_route_or_accept_api_actions():
    import json

    from app.mind import parse_mind_action, parse_mind_route

    with pytest.raises(ValidationError):
        parse_mind_route(json.dumps({"type": "core_task", "content": "read", "kind": "api_run"}))
    with pytest.raises(ValidationError):
        parse_mind_action(
            json.dumps(
                {
                    "type": "core_task",
                    "content": "read",
                    "task": {"kind": "api_run", "payload": {"endpoint_id": "example", "path": "/"}},
                }
            )
        )


@pytest.mark.parametrize("field", ["method", "headers", "credentials", "url", "proxy", "workspace"])
def test_task_cannot_supply_extra_authority(field):
    with pytest.raises(ValidationError):
        ApiRunInput(
            kind="api_run",
            payload={
                "endpoint_id": "example",
                "path": "/",
                field: "untrusted",
            },
        )


def test_retry_reselects_exact_current_worker(settings, database):
    value = task_runtime()
    service = TaskService(database, settings, api_supervisor=supervisor(value))
    created, _ = service.submit(
        ApiRunInput(kind="api_run", payload={"endpoint_id": "example", "path": "/"}),
        None,
    )
    service.cancel(created.id)
    descriptor = descriptor_for_api_worker().model_copy(update={"worker_id": "replacement_api"})
    service.api_supervisor = Supervisor(WorkerRegistry(((descriptor, value),)))
    retried = service.retry(created.id)
    assert retried.action_version == created.action_version + 1
    assert retried.api_spec != created.api_spec
    assert retried.api_spec.worker_selection.descriptor.worker_id == "replacement_api"
    assert service.claim().api_request == retried.api_spec
