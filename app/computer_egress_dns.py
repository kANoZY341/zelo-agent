"""Core-owned DNS resolution for persistent Agent computer egress."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import sys
from collections.abc import Awaitable, Callable

from app.api_contracts import ApiEndpointSpec, public_address

MAX_ADDRESSES = 16
MAX_OUTPUT = 4096
CLEANUP_GRACE = 1.0

CHILD_CODE = """
import json, socket, sys
rows = socket.getaddrinfo(sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM)
addresses = sorted({row[4][0] for row in rows})
if not 1 <= len(addresses) <= 16:
    sys.exit(2)
data = json.dumps(addresses, separators=(',', ':')).encode('ascii')
if len(data) > 4096:
    sys.exit(2)
sys.stdout.buffer.write(data)
"""


class ComputerEgressDnsError(ValueError):
    """Sanitized fail-closed egress DNS denial."""


async def settle(task: asyncio.Task, grace: float = CLEANUP_GRACE) -> bool:
    deadline = asyncio.get_running_loop().time() + grace
    while not task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        try:
            await asyncio.wait_for(asyncio.shield(task), remaining)
        except asyncio.CancelledError:
            continue
        except Exception:
            return False
    return not task.cancelled() and task.exception() is None


Spawn = Callable[..., Awaitable[asyncio.subprocess.Process]]


class ComputerEgressResolver:
    """Single event-loop owner for canonical public-address DNS resolution."""

    def __init__(self, *, spawn: Spawn = asyncio.create_subprocess_exec) -> None:
        self._spawn = spawn
        self._busy = False
        self._closed = False
        self._poisoned = False
        self._owner: asyncio.Task | None = None
        self._cleanup: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None

    async def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        if (
            not math.isfinite(deadline)
            or self._closed
            or self._poisoned
            or self._busy
            or loop.time() >= deadline
        ):
            raise ComputerEgressDnsError("computer_egress_dns_admission_denied")
        deadline = min(deadline, loop.time() + 30.0)
        self._busy = True
        self._owner = asyncio.current_task()
        creation: asyncio.Task | None = None
        process: asyncio.subprocess.Process | None = None
        try:
            bracketed = f"[{host}]" if ":" in host else host
            origin = f"https://{bracketed}" + (f":{port}" if port != 443 else "")
            try:
                spec = ApiEndpointSpec(endpoint_id="egress_dns", origin=origin)
            except ValueError:
                raise ComputerEgressDnsError("computer_egress_dns_host_invalid") from None
            if spec.host != host or spec.port != port:
                raise ComputerEgressDnsError("computer_egress_dns_host_invalid")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                try:
                    return (public_address(host),)
                except ValueError:
                    raise ComputerEgressDnsError("computer_egress_dns_result_invalid") from None

            async with asyncio.timeout_at(deadline):
                creation = asyncio.create_task(
                    self._spawn(
                        sys.executable,
                        "-I",
                        "-S",
                        "-c",
                        CHILD_CODE,
                        host + ".",
                        str(port),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        env={},
                        close_fds=True,
                        limit=MAX_OUTPUT + 1,
                    )
                )
                process = await asyncio.shield(creation)
                if process.stdout is None:
                    raise ComputerEgressDnsError("computer_egress_dns_child_invalid")
                data = bytearray()
                while True:
                    chunk = await process.stdout.read(min(1024, MAX_OUTPUT + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > MAX_OUTPUT:
                        raise ComputerEgressDnsError("computer_egress_dns_output_limit")
                if await process.wait() != 0:
                    raise ComputerEgressDnsError("computer_egress_dns_child_failed")
                try:
                    values = json.loads(data)
                    if (
                        not isinstance(values, list)
                        or not 1 <= len(values) <= MAX_ADDRESSES
                        or any(type(item) is not str for item in values)
                        or len(set(values)) != len(values)
                    ):
                        raise ValueError
                    return tuple(public_address(item) for item in values)
                except (ValueError, TypeError):
                    raise ComputerEgressDnsError("computer_egress_dns_result_invalid") from None
        finally:
            if creation is not None:

                async def reap() -> None:
                    child = process if process is not None else await creation
                    if child.returncode is None:
                        try:
                            child.kill()
                        except ProcessLookupError:
                            pass
                    if child.stdout is not None:
                        while await child.stdout.read(1024):
                            pass
                    await child.wait()

                self._cleanup = asyncio.create_task(reap())
                if not await settle(self._cleanup):
                    self._poisoned = True
                    self._cleanup.add_done_callback(_consume_exception)
            self._busy = False
            self._owner = None
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if self._poisoned:
                raise ComputerEgressDnsError("computer_egress_dns_cleanup_unproven")

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            owner = self._owner
            caller = asyncio.current_task()

            async def close_owned() -> None:
                try:
                    if owner is not None and owner is not caller:
                        owner.cancel()
                        try:
                            await owner
                        except asyncio.CancelledError:
                            pass
                finally:
                    if self._cleanup is not None:
                        await self._cleanup

            self._close_task = asyncio.create_task(close_owned())
            self._close_task.add_done_callback(_consume_exception)
        if not await settle(self._close_task):
            self._poisoned = True
            raise ComputerEgressDnsError("computer_egress_dns_close_cleanup_unproven")
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError


def _consume_exception(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()
