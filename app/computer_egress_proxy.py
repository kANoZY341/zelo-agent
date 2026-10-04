"""Bounded HTTP CONNECT proxy for persistent Agent computer egress.

This module is inert until an explicit later deployment gate starts it. It owns
no authorization rules: every CONNECT delegates to ComputerEgressPolicy and an
owned public-address resolver before opening an upstream socket.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from typing import Protocol
from urllib.parse import urlsplit

from app.api_contracts import public_address
from app.computer_egress import ComputerEgressError, ComputerEgressPolicy
from app.computer_egress_dns import ComputerEgressDnsError, ComputerEgressResolver

_REQUEST_BYTES = 8192
_MAX_HEADER_COUNT = 32
_DEFAULT_CONNECTION_SECONDS = 60.0
_DEFAULT_DIRECTION_BYTES = 32 * 1024 * 1024


class EgressProxyError(ValueError):
    """Sanitized proxy parsing/runtime denial."""


class Resolver(Protocol):
    async def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]: ...
    async def aclose(self) -> None: ...


ResolverFactory = Callable[[], Resolver]
Connector = Callable[
    [str, int],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


def _authority(value: str) -> tuple[str, int]:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise EgressProxyError("egress_proxy_target_invalid")
    try:
        parsed = urlsplit("//" + value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise EgressProxyError("egress_proxy_target_invalid") from None
    if (
        host is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not host.isascii()
        or host != host.lower()
    ):
        raise EgressProxyError("egress_proxy_target_invalid")
    canonical = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    if value != canonical:
        raise EgressProxyError("egress_proxy_target_not_canonical")
    return host, port


def _basic_proxy_auth(value: str | None) -> tuple[str, str]:
    if not value or not value.startswith("Basic ") or len(value) > 512:
        raise EgressProxyError("egress_proxy_auth_required")
    encoded = value[6:].strip()
    try:
        raw = base64.b64decode(encoded, validate=True)
        decoded = raw.decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise EgressProxyError("egress_proxy_auth_invalid") from None
    if ":" not in decoded:
        raise EgressProxyError("egress_proxy_auth_invalid")
    username, password = decoded.split(":", 1)
    if (
        not username
        or len(username) > 64
        or len(password) != 64
        or not username.isascii()
        or not password.isascii()
    ):
        raise EgressProxyError("egress_proxy_auth_invalid")
    return username, password


async def _default_connector(
    host: str,
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host=host, port=port)


class ComputerEgressProxy:
    """Single-process bounded CONNECT tunnel server."""

    def __init__(
        self,
        policy: ComputerEgressPolicy,
        *,
        host: str,
        port: int,
        resolver_factory: ResolverFactory = ComputerEgressResolver,
        connector: Connector = _default_connector,
        connection_seconds: float = _DEFAULT_CONNECTION_SECONDS,
        direction_bytes: int = _DEFAULT_DIRECTION_BYTES,
        max_connections: int = 32,
    ) -> None:
        if (
            not isinstance(host, str)
            or not host
            or not 0 <= port <= 65535
            or not 1.0 <= connection_seconds <= 300.0
            or not 1024 <= direction_bytes <= 128 * 1024 * 1024
            or not 1 <= max_connections <= 128
        ):
            raise ValueError("computer_egress_proxy_configuration_invalid")
        self.policy = policy
        self.host = host
        self.port = port
        self.resolver_factory = resolver_factory
        self.connector = connector
        self.connection_seconds = float(connection_seconds)
        self.direction_bytes = direction_bytes
        self._semaphore = asyncio.Semaphore(max_connections)
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.Task] = set()
        self._closing = False

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None or self._closing:
            raise RuntimeError("computer_egress_proxy_state_invalid")
        self._server = await asyncio.start_server(
            self._client,
            host=self.host,
            port=self.port,
            limit=_REQUEST_BYTES + 1,
            start_serving=True,
        )

    async def aclose(self) -> None:
        self._closing = True
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        current = asyncio.current_task()
        clients = tuple(task for task in self._clients if task is not current and not task.done())
        for task in clients:
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        except (TimeoutError, ConnectionError, OSError):
            transport = writer.transport
            if transport is not None:
                transport.abort()

    @staticmethod
    async def _reply(
        writer: asyncio.StreamWriter,
        status: int,
        reason: str,
        *,
        proxy_authenticate: bool = False,
    ) -> None:
        headers = [
            f"HTTP/1.1 {status} {reason}",
            "Connection: close",
            "Content-Length: 0",
        ]
        if proxy_authenticate:
            headers.append('Proxy-Authenticate: Basic realm="Zelo Agent Computer"')
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        await writer.drain()

    async def _read_connect(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, int, str, str]:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            raise EgressProxyError("egress_proxy_request_invalid") from None
        if len(raw) > _REQUEST_BYTES:
            raise EgressProxyError("egress_proxy_request_too_large")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            raise EgressProxyError("egress_proxy_request_invalid") from None
        lines = text[:-4].split("\r\n")
        if not lines or len(lines) > _MAX_HEADER_COUNT + 1:
            raise EgressProxyError("egress_proxy_request_invalid")
        request = lines[0].split(" ")
        if len(request) != 3 or request[2] != "HTTP/1.1":
            raise EgressProxyError("egress_proxy_request_invalid")
        if request[0] != "CONNECT":
            raise EgressProxyError("egress_proxy_method_denied")
        host, port = _authority(request[1])

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                raise EgressProxyError("egress_proxy_request_invalid")
            name, value = line.split(":", 1)
            name = name.strip().lower()
            value = value.strip()
            if (
                not name
                or name in headers
                or any(character in name + value for character in "\r\n\x00")
            ):
                raise EgressProxyError("egress_proxy_request_invalid")
            headers[name] = value
        username, password = _basic_proxy_auth(headers.get("proxy-authorization"))
        return host, port, username, password

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        authorization,
    ) -> None:
        total = 0
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            total += len(chunk)
            if total > self.direction_bytes:
                raise EgressProxyError("egress_proxy_byte_budget_exceeded")
            if not self.policy.matches(authorization):
                raise EgressProxyError("egress_proxy_authorization_revoked")
            writer.write(chunk)
            await writer.drain()

    async def _client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        owner = asyncio.current_task()
        if owner is not None:
            self._clients.add(owner)
        upstream_writer: asyncio.StreamWriter | None = None
        resolver: Resolver | None = None
        acquired = False
        try:
            async with asyncio.timeout(self.connection_seconds):
                try:
                    await asyncio.wait_for(self._semaphore.acquire(), timeout=1.0)
                    acquired = True
                except TimeoutError:
                    await self._reply(writer, 503, "Busy")
                    return

                try:
                    host, port, agent_id, password = await self._read_connect(reader)
                except EgressProxyError as error:
                    if error.args[0] == "egress_proxy_method_denied":
                        await self._reply(writer, 405, "Method Not Allowed")
                    elif error.args[0] in {
                        "egress_proxy_auth_required",
                        "egress_proxy_auth_invalid",
                    }:
                        await self._reply(
                            writer,
                            407,
                            "Proxy Authentication Required",
                            proxy_authenticate=True,
                        )
                    else:
                        await self._reply(writer, 400, "Bad Request")
                    return

                try:
                    authorization = self.policy.authorize_credentials(
                        agent_id,
                        password,
                        host=host,
                        port=port,
                    )
                except ComputerEgressError:
                    await self._reply(writer, 403, "Forbidden")
                    return

                resolver = self.resolver_factory()
                deadline = asyncio.get_running_loop().time() + min(
                    self.connection_seconds,
                    30.0,
                )
                try:
                    addresses = await resolver.resolve(host, port, deadline)
                except ComputerEgressDnsError:
                    await self._reply(writer, 502, "Bad Gateway")
                    return
                if not addresses:
                    await self._reply(writer, 502, "Bad Gateway")
                    return
                try:
                    public_addresses = tuple(public_address(item) for item in addresses)
                except (TypeError, ValueError):
                    await self._reply(writer, 502, "Bad Gateway")
                    return
                if (
                    len(public_addresses) != len(addresses)
                    or len(public_addresses) > 16
                    or len(set(public_addresses)) != len(public_addresses)
                ):
                    await self._reply(writer, 502, "Bad Gateway")
                    return

                # Policy identity and DNS target describe the same trusted authority.
                endpoint = authorization.endpoint.endpoint
                if endpoint.host != host or endpoint.port != port:
                    await self._reply(writer, 403, "Forbidden")
                    return

                if not self.policy.matches(authorization):
                    await self._reply(writer, 403, "Forbidden")
                    return

                upstream_reader, upstream_writer = await self.connector(
                    public_addresses[0],
                    port,
                )
                writer.write(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: Zelo\r\n\r\n")
                await writer.drain()

                first = asyncio.create_task(self._pump(reader, upstream_writer, authorization))
                second = asyncio.create_task(self._pump(upstream_reader, writer, authorization))
                pumps = {first, second}
                try:
                    done, pending = await asyncio.wait(
                        pumps,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
                finally:
                    for task in pumps:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)
        except TimeoutError:
            pass
        except (ConnectionError, OSError, EgressProxyError):
            pass
        finally:
            if resolver is not None:
                try:
                    await resolver.aclose()
                except Exception:
                    pass
            if upstream_writer is not None:
                await self._close_writer(upstream_writer)
            await self._close_writer(writer)
            if acquired:
                self._semaphore.release()
            if owner is not None:
                self._clients.discard(owner)
