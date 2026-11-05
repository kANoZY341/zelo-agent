"""Bounded persistent-computer CONNECT proxy tests."""

import asyncio
import base64

import pytest

from app.agent_factory import AgentComputerSpec, AgentFactory, AgentRegistry, AgentSpec
from app.agent_instances import AgentInstanceService
from app.api_contracts import ApiEndpointRegistry
from app.computer_egress import ComputerEgressPolicy
from app.computer_egress_proxy import ComputerEgressProxy
from app.config import Settings
from app.memory import MemoryService
from app.models import AgentComputerPermission, utcnow
from app.projects import ProjectService
from app.schemas import AgentInstanceCreate, ProjectCreate


class FakeResolver:
    def __init__(self, calls: list[tuple[str, int]], address: str = "93.184.216.34"):
        self.calls = calls
        self.address = address
        self.closed = False

    async def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        assert deadline > asyncio.get_running_loop().time()
        self.calls.append((host, port))
        return (self.address,)

    async def aclose(self) -> None:
        self.closed = True


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        computer_supervisor_url="http://127.0.0.1:4312",
        computer_supervisor_token="s" * 24,
        computer_token="c" * 24,
        computer_browser_endpoints=("public_site=https://example.com",),
    )


def setup_policy(database):
    spec = AgentSpec(
        agent_id="proxy_agent",
        role="proxy_agent",
        objective="Use only trusted HTTPS computer egress.",
        capabilities=("computer_navigate", "computer_read"),
        allowed_task_kinds=("computer_run",),
        memory_namespace="agent/proxy_agent",
        computer=AgentComputerSpec(
            browser=True,
            files=True,
            browser_endpoint_ids=("public_site",),
        ),
        lifecycle="active",
    )
    factory = AgentFactory(AgentRegistry((spec,)))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    project = ProjectService(database).create(
        ProjectCreate(name="Proxy", summary="CONNECT proxy test.")
    )
    instance = instances.create(AgentInstanceCreate(agent_id=spec.agent_id, project_id=project.id))
    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance.id)
        assert row is not None
        row.enabled = True
        row.browser = True
        row.updated_at = utcnow()
    endpoints = ApiEndpointRegistry.from_entries(configured_settings().computer_browser_endpoints)
    policy = ComputerEgressPolicy(
        database,
        factory,
        endpoints,
        configured_settings(),
    )
    return policy, instance


def auth_header(instance_id: str, password: str) -> str:
    raw = f"{instance_id}:{password}".encode("ascii")
    return "Basic " + base64.b64encode(raw).decode("ascii")


async def read_headers(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)


def test_connect_tunnels_only_after_agent_policy_and_exact_resolver_target(database):
    policy, instance = setup_policy(database)
    resolver_calls: list[tuple[str, int]] = []
    connector_calls: list[tuple[str, int]] = []

    async def scenario():
        async def echo(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                data = await reader.readexactly(4)
                writer.write(data)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        async def connector(host: str, port: int):
            connector_calls.append((host, port))
            assert host == "93.184.216.34"
            assert port == 443
            return await asyncio.open_connection("127.0.0.1", echo_port)

        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver(resolver_calls),
            connector=connector,
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            password = policy.derive_password(instance.id)
            request = (
                "CONNECT example.com:443 HTTP/1.1\r\n"
                "Host: example.com:443\r\n"
                f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                "\r\n"
            )
            writer.write(request.encode("ascii"))
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 200 Connection Established")

            writer.write(b"PING")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), timeout=3) == b"PING"
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.aclose()
            echo_server.close()
            await echo_server.wait_closed()

    asyncio.run(scenario())
    assert resolver_calls == [("example.com", 443)]
    assert connector_calls == [("93.184.216.34", 443)]


@pytest.mark.parametrize("idle_count", [1, 2])
def test_idle_unauthenticated_socket_does_not_occupy_authorized_tunnel_capacity(
    database, idle_count
):
    policy, instance = setup_policy(database)
    connector_calls: list[tuple[str, int]] = []

    async def scenario():
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        async def connector(host: str, port: int):
            connector_calls.append((host, port))
            return await asyncio.open_connection("127.0.0.1", echo_port)

        entered = asyncio.Queue()

        class ObservedProxy(ComputerEgressProxy):
            async def _read_connect(self, reader):
                entered.put_nowait(None)
                return await super()._read_connect(reader)

        proxy = ObservedProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver([]),
            connector=connector,
            max_connections=1,
            connection_seconds=5,
        )
        await proxy.start()
        idle_writers = []
        authorized_writer = None
        try:
            for _ in range(idle_count):
                _idle_reader, idle_writer = await asyncio.open_connection(
                    "127.0.0.1", proxy.bound_port
                )
                idle_writers.append(idle_writer)
                await asyncio.wait_for(entered.get(), timeout=1)
            authorized_reader, authorized_writer = await asyncio.open_connection(
                "127.0.0.1", proxy.bound_port
            )
            password = policy.derive_password(instance.id)
            authorized_writer.write(
                (
                    "CONNECT example.com:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await authorized_writer.drain()
            response = await read_headers(authorized_reader)
            assert response.startswith(b"HTTP/1.1 200 Connection Established")
        finally:
            for idle_writer in idle_writers:
                idle_writer.close()
                await idle_writer.wait_closed()
            if authorized_writer is not None:
                authorized_writer.close()
                await authorized_writer.wait_closed()
            await proxy.aclose()
            echo_server.close()
            await echo_server.wait_closed()

    asyncio.run(scenario())
    assert connector_calls == [("93.184.216.34", 443)]


def test_silent_header_times_out_before_tunnel_lifetime(database):
    policy, _instance = setup_policy(database)

    async def scenario():
        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver([]),
            max_connections=1,
            connection_seconds=2,
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.bound_port)
            try:
                response = await read_headers(reader)
                assert response.startswith(b"HTTP/1.1 408 Request Timeout")
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await proxy.aclose()

    asyncio.run(scenario())


def test_ungranted_target_is_denied_before_dns_or_connect(database):
    policy, instance = setup_policy(database)
    resolver_calls: list[tuple[str, int]] = []
    connector_calls: list[tuple[str, int]] = []

    async def connector(host: str, port: int):
        connector_calls.append((host, port))
        raise AssertionError("connector must not run")

    async def scenario():
        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver(resolver_calls),
            connector=connector,
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            password = policy.derive_password(instance.id)
            writer.write(
                (
                    "CONNECT example.org:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 403 Forbidden")
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.aclose()

    asyncio.run(scenario())
    assert resolver_calls == []
    assert connector_calls == []


def test_proxy_requires_auth_and_connect_method(database):
    policy, _instance = setup_policy(database)

    async def scenario():
        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver([]),
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 407 Proxy Authentication Required")
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            writer.write(b"GET https://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 405 Method Not Allowed")
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.aclose()

    asyncio.run(scenario())


def test_wrong_agent_proxy_secret_is_denied_without_dns(database):
    policy, instance = setup_policy(database)
    resolver_calls: list[tuple[str, int]] = []

    async def scenario():
        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver(resolver_calls),
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            writer.write(
                (
                    "CONNECT example.com:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {auth_header(instance.id, '0' * 64)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 403 Forbidden")
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.aclose()

    asyncio.run(scenario())
    assert resolver_calls == []


def test_resolver_private_address_is_rejected_before_connect(database):
    policy, instance = setup_policy(database)
    connector_calls: list[tuple[str, int]] = []

    async def connector(host: str, port: int):
        connector_calls.append((host, port))
        raise AssertionError("connector must not run")

    async def scenario():
        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver([], address="127.0.0.1"),
            connector=connector,
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            password = policy.derive_password(instance.id)
            writer.write(
                (
                    "CONNECT example.com:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 502 Bad Gateway")
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.aclose()

    asyncio.run(scenario())
    assert connector_calls == []


def test_live_tunnel_revalidates_permission_before_forwarding(database):
    policy, instance = setup_policy(database)
    resolver_calls: list[tuple[str, int]] = []
    received: list[bytes] = []

    async def scenario():
        async def echo(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                while True:
                    data = await reader.read(4)
                    if not data:
                        return
                    received.append(data)
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        async def connector(host: str, port: int):
            assert host == "93.184.216.34"
            assert port == 443
            return await asyncio.open_connection("127.0.0.1", echo_port)

        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver(resolver_calls),
            connector=connector,
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                proxy.bound_port,
            )
            password = policy.derive_password(instance.id)
            writer.write(
                (
                    "CONNECT example.com:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 200 Connection Established")

            writer.write(b"PING")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), timeout=3) == b"PING"

            with database.transaction() as session:
                row = session.get(AgentComputerPermission, instance.id)
                assert row is not None
                row.browser = False
                row.updated_at = utcnow()

            writer.write(b"PONG")
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), timeout=3) == b""
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.aclose()
            echo_server.close()
            await echo_server.wait_closed()

    asyncio.run(scenario())
    assert resolver_calls == [("example.com", 443)]
    assert received == [b"PING"]


def test_proxy_close_terminates_active_tunnel(database):
    policy, instance = setup_policy(database)

    async def scenario():
        accepted = asyncio.Event()
        release = asyncio.Event()

        async def echo(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            accepted.set()
            try:
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        async def connector(host: str, port: int):
            assert host == "93.184.216.34"
            assert port == 443
            return await asyncio.open_connection("127.0.0.1", echo_port)

        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver([]),
            connector=connector,
        )
        await proxy.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.bound_port)
        try:
            password = policy.derive_password(instance.id)
            writer.write(
                (
                    "CONNECT example.com:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            response = await read_headers(reader)
            assert response.startswith(b"HTTP/1.1 200 Connection Established")
            await asyncio.wait_for(accepted.wait(), timeout=3)

            await asyncio.wait_for(proxy.aclose(), timeout=3)
            assert await asyncio.wait_for(reader.read(), timeout=3) == b""
            assert not proxy._clients
        finally:
            release.set()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (TimeoutError, ConnectionError, OSError):
                pass
            echo_server.close()
            await echo_server.wait_closed()

    asyncio.run(scenario())


def test_revoked_waiting_connect_does_not_resolve_or_open_upstream(database):
    policy, instance = setup_policy(database)
    resolver_calls: list[tuple[str, int]] = []
    connector_calls: list[tuple[str, int]] = []

    async def scenario():
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        async def connector(host: str, port: int):
            connector_calls.append((host, port))
            return await asyncio.open_connection("127.0.0.1", echo_port)

        second_authorized = asyncio.Event()
        authorize = policy.authorize_credentials
        authorization_count = 0

        def tracked_authorize(*args, **kwargs):
            nonlocal authorization_count
            result = authorize(*args, **kwargs)
            authorization_count += 1
            if authorization_count == 2:
                second_authorized.set()
            return result

        policy.authorize_credentials = tracked_authorize
        proxy = ComputerEgressProxy(
            policy,
            host="127.0.0.1",
            port=0,
            resolver_factory=lambda: FakeResolver(resolver_calls),
            connector=connector,
            max_connections=1,
        )
        await proxy.start()
        first_writer = None
        second_writer = None
        try:
            password = policy.derive_password(instance.id)
            request = (
                "CONNECT example.com:443 HTTP/1.1\r\n"
                f"Proxy-Authorization: {auth_header(instance.id, password)}\r\n"
                "\r\n"
            ).encode("ascii")

            first_reader, first_writer = await asyncio.open_connection(
                "127.0.0.1", proxy.bound_port
            )
            first_writer.write(request)
            await first_writer.drain()
            assert (await read_headers(first_reader)).startswith(
                b"HTTP/1.1 200 Connection Established"
            )

            second_reader, second_writer = await asyncio.open_connection(
                "127.0.0.1", proxy.bound_port
            )
            second_writer.write(request)
            await second_writer.drain()
            await asyncio.wait_for(second_authorized.wait(), timeout=3)

            with database.transaction() as session:
                row = session.get(AgentComputerPermission, instance.id)
                assert row is not None
                row.browser = False
                row.updated_at = utcnow()

            first_writer.close()
            await first_writer.wait_closed()
            assert (await read_headers(second_reader)).startswith(b"HTTP/1.1 403 Forbidden")
            assert resolver_calls == [("example.com", 443)]
            assert connector_calls == [("93.184.216.34", 443)]
        finally:
            for writer in (first_writer, second_writer):
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
            await proxy.aclose()
            echo_server.close()
            await echo_server.wait_closed()

    asyncio.run(scenario())
