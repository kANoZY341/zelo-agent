"""Offline broker qualification; fixtures never connect to a network."""

import asyncio
from dataclasses import FrozenInstanceError
from functools import wraps
from pathlib import Path

import httpx
import pytest

from app.api_contracts import ApiEndpointRegistry
from qualification.broker import PageSession, Policy
from qualification.dns import QualificationError


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks=(b"hello",), gate=None):
        self.chunks = chunks
        self.gate = gate
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.started.set()
        if self.gate:
            await self.gate.wait()
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class Resolver:
    def __init__(self, addresses=("8.8.8.8",)):
        self.addresses = addresses
        self.calls = []
        self.closed = False

    async def resolve(self, *args):
        self.calls.append(args)
        return self.addresses

    async def aclose(self):
        self.closed = True


def session(
    *,
    origin="https://example.com",
    status=200,
    headers=None,
    stream=None,
    resolver=None,
    policy=None,
):
    registry = ApiEndpointRegistry.from_entries([f"page={origin}"])
    requests = []
    stream = stream or Stream()

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status, headers=headers or {"Content-Type": "text/html"}, stream=stream
        )

    broker = PageSession(
        registry,
        bindings=(registry.bind("page"),),
        policy=policy,
        resolver=resolver or Resolver(),
        transport=httpx.MockTransport(handle),
    )
    return broker, requests, stream


@run_async
async def test_get_evidence_and_no_forwarded_defaults(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://secret.invalid")
    monkeypatch.setenv("ZELO_API_ENDPOINTS", "evil=https://evil.com")
    broker, requests, stream = session(
        headers={
            "Content-Type": 'text/html; charset="UTF-8"',
            "Set-Cookie": "token=secret",
            "Refresh": "0;url=https://evil.com",
            "Content-Security-Policy": "anything",
        }
    )
    result = await broker.fetch("page", "/article")
    request = requests[0]
    assert request.method == "GET" and str(request.url) == "https://8.8.8.8/article"
    assert dict(request.headers) == {
        "host": "example.com",
        "accept": "text/html, text/plain",
        "accept-encoding": "identity",
        "user-agent": "Zelo-Qualification/1",
    }
    assert request.extensions["sni_hostname"] == "example.com"
    assert result.body == b"hello" and result.text == "hello"
    assert result.source_url == "https://example.com/article"
    assert result.instruction_trust == "none" and stream.closed
    assert not hasattr(result, "headers")
    with pytest.raises(FrozenInstanceError):
        result.text = "changed"
    await broker.aclose()
    with pytest.raises(QualificationError):
        await PageSession().fetch("evil", "/")


@run_async
@pytest.mark.parametrize(
    "path",
    [
        "//x",
        "/a//b",
        "/a/../b",
        "/./",
        "/%61",
        "/?token=x",
        "/#x",
        "/a\n",
        "/a\\b",
        "https://evil.com",
        "/é",
    ],
)
async def test_strict_paths(path):
    broker, requests, _ = session()
    with pytest.raises(ValueError):
        await broker.fetch("page", path)
    assert not requests


@run_async
async def test_binding_and_payload_surface():
    registry = ApiEndpointRegistry.from_entries(["page=https://example.com"])
    other = ApiEndpointRegistry.from_entries(["page=https://other.com"])
    with pytest.raises(QualificationError):
        PageSession(registry, bindings=(other.bind("page"),))
    broker, requests, _ = session()
    with pytest.raises(QualificationError):
        await broker.fetch("missing", "/")
    with pytest.raises(QualificationError):
        await PageSession(registry).fetch("page", "/")
    for field in ("query", "method", "headers", "cookies", "proxy", "url", "body", "callback"):
        with pytest.raises(TypeError):
            await broker.fetch("page", "/", **{field: "bad"})
    assert not requests


@run_async
@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1",),
        ("10.0.0.1",),
        ("224.0.0.1",),
        ("8.8.8.8", "192.168.1.1"),
        ("2002:808:808::1",),
        ("::ffff:8.8.8.8",),
        ("fe80::1%eth0",),
        ("2001:0000:4136:e378:8000:63bf:3fff:fdd2",),
        ("2606:4700:4700:0:0:0:0:1111",),
        ("8.8.8.8",) * 17,
    ],
)
async def test_addresses_denied(addresses):
    broker, requests, _ = session(resolver=Resolver(addresses))
    with pytest.raises(ValueError):
        await broker.fetch("page", "/")
    assert not requests


@run_async
@pytest.mark.parametrize("host", ["8.8.8.8", "[2606:4700:4700::1111]"])
async def test_numeric_pinning_and_port(host):
    resolver = Resolver(())
    broker, requests, _ = session(origin=f"https://{host}:8443", resolver=resolver)
    await broker.fetch("page", "/")
    assert not resolver.calls
    assert requests[0].headers["host"] == f"{host}:8443"
    assert requests[0].extensions["sni_hostname"] == host.strip("[]")
    assert str(requests[0].url) == f"https://{host}:8443/"


@run_async
@pytest.mark.parametrize(
    "status,headers,chunks",
    [
        (302, {"location": "https://evil.com"}, (b"x",)),
        (404, {}, (b"x",)),
        (200, {"Content-Disposition": "attachment"}, (b"x",)),
        (200, {"Content-Type": "application/json"}, (b"{}",)),
        (200, {"Content-Type": "multipart/mixed"}, (b"x",)),
        (200, {"Content-Type": "application/javascript"}, (b"x",)),
        (200, {"Content-Type": "text/plain; charset=latin1"}, (b"x",)),
        (200, {"Content-Type": "text/plain; charset=utf-8; other=yes"}, (b"x",)),
        (200, {"Content-Encoding": "gzip"}, (b"x",)),
        (200, {"Content-Encoding": "br"}, (b"x",)),
        (200, {}, (b"\xff",)),
        (200, {}, (b"a" * 262145,)),
    ],
)
async def test_response_denials_close(status, headers, chunks):
    broker, requests, stream = session(
        status=status, headers={"Content-Type": "text/plain", **headers}, stream=Stream(chunks)
    )
    with pytest.raises(ValueError):
        await broker.fetch("page", "/")
    assert len(requests) == 1 and stream.closed


@run_async
async def test_raw_and_aggregate_charges_and_resolve_each_time():
    resolver = Resolver()
    broker, _, _ = session(
        stream=Stream((b"abc", b"de")),
        resolver=resolver,
        policy=Policy(response_bytes=5, aggregate_bytes=9),
    )
    await broker.fetch("page", "/")
    with pytest.raises(QualificationError):
        await broker.fetch("page", "/")
    assert broker._bytes == 10 and len(resolver.calls) == 2
    with pytest.raises(QualificationError):
        await broker.fetch("page", "/")


@run_async
async def test_failed_attempts_charged_and_expiry():
    broker, requests, _ = session()
    for _ in range(4):
        with pytest.raises(ValueError):
            await broker.fetch("page", "/?bad")
    with pytest.raises(QualificationError):
        await broker.fetch("page", "/")
    assert broker._attempts == 4 and not requests
    broker, requests, _ = session(policy=Policy(seconds=0.001))
    await asyncio.sleep(0.005)
    with pytest.raises(QualificationError):
        await broker.fetch("page", "/")
    assert not requests


@run_async
async def test_flood_fails_fast_and_close_cancels_stream():
    broker, requests, stream = session(stream=Stream(gate=asyncio.Event()))
    task = asyncio.create_task(broker.fetch("page", "/"))
    await stream.started.wait()
    results = await asyncio.wait_for(
        asyncio.gather(*(broker.fetch("page", "/") for _ in range(100)), return_exceptions=True),
        0.5,
    )
    assert all(isinstance(result, QualificationError) for result in results)
    assert len(requests) == 1 and broker._attempts == 4
    await broker.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed and broker._resolver.closed


@run_async
async def test_stream_cancel_and_absolute_deadline():
    broker, _, stream = session(stream=Stream(gate=asyncio.Event()), policy=Policy(seconds=0.02))
    with pytest.raises(TimeoutError):
        await broker.fetch("page", "/")
    assert stream.closed
    with pytest.raises(QualificationError):
        await broker.fetch("page", "/")
    broker, _, stream = session(stream=Stream(gate=asyncio.Event()))
    task = asyncio.create_task(broker.fetch("page", "/"))
    await stream.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


def test_policy_and_no_runtime_integration():
    assert Policy().digest == Policy().digest
    assert Policy(response_bytes=123).digest != Policy().digest
    assert Policy(seconds=10).digest != Policy().digest
    for kwargs in (
        {"attempts": 5},
        {"seconds": float("nan")},
        {"seconds": 31},
        {"aggregate_bytes": 524289},
        {"response_bytes": True},
        {"version": "v2"},
    ):
        with pytest.raises(ValueError):
            Policy(**kwargs)
    root = Path(__file__).resolve().parents[1]
    for file in (root / "app").rglob("*.py"):
        assert "from qualification" not in file.read_text()
        assert "import qualification" not in file.read_text()
    for file in (root / "qualification").glob("*.py"):
        assert "register_worker(" not in file.read_text()
        assert "register_capability(" not in file.read_text()


@run_async
async def test_default_transport_configuration_and_fresh_connections(monkeypatch):
    from qualification import broker as module

    made = []
    calls = []

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, **kwargs):
            made.append(kwargs)
            self.closed = False

        async def handle_async_request(self, request):
            calls.append(request)
            return httpx.Response(200, headers={"content-type": "text/plain"}, stream=Stream())

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(module.httpx, "AsyncHTTPTransport", Transport)
    registry = ApiEndpointRegistry.from_entries(["page=https://example.com:8443"])
    resolver = Resolver()
    broker = PageSession(registry, bindings=(registry.bind("page"),), resolver=resolver)
    await broker.fetch("page", "/")
    await broker.fetch("page", "/")
    assert len(made) == len(resolver.calls) == 2
    assert all(
        item["verify"] is True and item["trust_env"] is False and item["retries"] == 0
        for item in made
    )
    assert all(item["limits"].max_keepalive_connections == 0 for item in made)
    assert calls[0].headers["host"] == "example.com:8443"
    assert calls[0].extensions["sni_hostname"] == "example.com"
    assert calls[1].extensions["timeout"]["read"] < calls[0].extensions["timeout"]["read"]
    await broker.aclose()


@run_async
async def test_invalid_text_bytes_remain_charged_and_raw_only(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("decoded response reader forbidden")
        yield b""

    monkeypatch.setattr(httpx.Response, "aiter_bytes", forbidden)
    broker, _, _ = session(stream=Stream((b"ab", b"\xff")))
    with pytest.raises(QualificationError, match="invalid_utf8"):
        await broker.fetch("page", "/")
    assert broker._bytes == 3 and broker._attempts == 1 and broker._active is None


@run_async
async def test_cleanup_failure_poison_and_close_still_closes_transport():
    class BadStream(Stream):
        async def aclose(self):
            raise OSError("fixture cleanup failure")

    class Transport(httpx.AsyncBaseTransport):
        closed = False

        async def handle_async_request(self, request):
            return httpx.Response(302, stream=BadStream())

        async def aclose(self):
            self.closed = True

    transport = Transport()
    registry = ApiEndpointRegistry.from_entries(["page=https://example.com"])
    broker = PageSession(
        registry, bindings=(registry.bind("page"),), resolver=Resolver(), transport=transport
    )
    with pytest.raises(QualificationError, match="poisoned"):
        await broker.fetch("page", "/")
    assert transport.closed and broker._active is None
    with pytest.raises(QualificationError):
        await broker.fetch("page", "/")


@run_async
async def test_repeated_cancel_during_stream_cleanup_keeps_admission_closed():
    gate = asyncio.Event()
    cleaning = asyncio.Event()

    class SlowClose(Stream):
        async def aclose(self):
            cleaning.set()
            await gate.wait()
            self.closed = True

    broker, requests, stream = session(stream=SlowClose(gate=asyncio.Event()))
    task = asyncio.create_task(broker.fetch("page", "/"))
    await stream.started.wait()
    task.cancel()
    await cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(QualificationError, match="busy"):
        await broker.fetch("page", "/")
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed and len(requests) == 1
    await asyncio.gather(*(broker.aclose() for _ in range(50)))
