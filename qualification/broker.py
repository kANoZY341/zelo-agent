"""Qualification-only page evidence fetcher; no renderer or execution authority."""

import asyncio
import hashlib
import ipaddress
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

import httpx

from app.api_contracts import ApiEndpointBinding, ApiEndpointRegistry, public_address
from app.schemas import ApiPayload
from qualification.dns import (
    MAX_ADDRESSES,
    OwnedResolver,
    QualificationError,
    _consume_exception,
    settle,
)


@dataclass(frozen=True)
class Policy:
    version: str = "zelo-page-evidence-v1"
    attempts: int = 4
    response_bytes: int = 262144
    aggregate_bytes: int = 524288
    seconds: float = 30.0

    def __post_init__(self) -> None:
        for value, maximum in (
            (self.attempts, 4),
            (self.response_bytes, 262144),
            (self.aggregate_bytes, 524288),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid_policy_budget")
        if type(self.seconds) not in (int, float) or not 0 < self.seconds <= 30:
            raise ValueError("invalid_policy_deadline")
        if self.version != "zelo-page-evidence-v1":
            raise ValueError("unsupported_policy_version")

    @property
    def digest(self) -> str:
        semantics = {
            **asdict(self),
            "method": "GET",
            "query": False,
            "encoding": "identity",
            "mime": ["text/html", "text/plain"],
            "charset": "utf-8",
            "redirects": False,
            "retries": 0,
            "trust_env": False,
            "address_policy": "ApiEndpointRegistry/public_address-v1",
            "dns": "owned-child-fqdn-v1",
            "address_count": MAX_ADDRESSES,
            "headers": ["Accept", "Accept-Encoding", "Host", "User-Agent"],
            "user_agent": "Zelo-Qualification/1",
            "instruction_trust": "none",
            "admission": "single-flight-no-refund",
            "cleanup_grace_seconds": 1.0,
        }
        return hashlib.sha256(
            json.dumps(
                semantics,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class PageEvidence:
    binding: ApiEndpointBinding
    policy_digest: str
    source_url: str
    body: bytes
    text: str
    media_type: str
    instruction_trust: Literal["none"] = "none"


class Resolver(Protocol):
    async def resolve(self, host: str, port: int, deadline: float) -> tuple[str, ...]: ...
    async def aclose(self) -> None: ...


class PageSession:
    """One event-loop session. Trusted constructor binds authority; fetch accepts data only.

    Transport/resolver fixtures must honor cancellation and bounded chunk production.
    The default qualification path uses a fresh verified TLS transport per fetch.
    """

    def __init__(
        self,
        endpoints: ApiEndpointRegistry | None = None,
        *,
        bindings: tuple[ApiEndpointBinding, ...] = (),
        policy: Policy | None = None,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        endpoints = endpoints or ApiEndpointRegistry()
        policy = policy or Policy()
        if any(not endpoints.matches(item) for item in bindings):
            raise QualificationError("binding_inactive")
        self._bindings = {item.endpoint.endpoint_id: item for item in bindings}
        self._policy = policy
        self._resolver = resolver or OwnedResolver()
        self._transport = transport
        self._deadline = asyncio.get_running_loop().time() + policy.seconds
        self._attempts = 0
        self._bytes = 0
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self._poisoned = False
        self._active: asyncio.Task | None = None
        self._cleanup: asyncio.Task | None = None

    async def fetch(self, endpoint_id: str, path: str) -> PageEvidence:
        # No await before admission: floods consume attempts without creating waiter queues.
        if self._closed or self._poisoned or self._attempts >= self._policy.attempts:
            raise QualificationError("session_unavailable")
        self._attempts += 1
        if self._active is not None:
            raise QualificationError("session_busy")
        if asyncio.get_running_loop().time() >= self._deadline:
            self._closed = True
            raise QualificationError("session_expired")
        self._active = asyncio.current_task()
        transport = None
        response = None
        try:
            payload = ApiPayload(endpoint_id=endpoint_id, path=path, query={})
            binding = self._bindings.get(payload.endpoint_id)
            if binding is None:
                raise QualificationError("endpoint_not_bound")
            endpoint = binding.endpoint
            async with asyncio.timeout_at(self._deadline):
                try:
                    ipaddress.ip_address(endpoint.host)
                except ValueError:
                    addresses = await self._resolver.resolve(
                        endpoint.host,
                        endpoint.port,
                        self._deadline,
                    )
                else:
                    addresses = (endpoint.host,)
                if not 1 <= len(addresses) <= MAX_ADDRESSES:
                    raise QualificationError("address_count_invalid")
                addresses = tuple(public_address(item) for item in addresses)
                ip = addresses[0]
                authority = f"[{ip}]" if ":" in ip else ip
                target = f"https://{authority}:{endpoint.port}{path}"
                host = endpoint.origin.removeprefix("https://")
                transport = self._transport or httpx.AsyncHTTPTransport(
                    verify=True,
                    trust_env=False,
                    retries=0,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
                )
                remaining = self._deadline - asyncio.get_running_loop().time()
                request = httpx.Request(
                    "GET",
                    target,
                    headers={
                        "Host": host,
                        "Accept": "text/html, text/plain",
                        "Accept-Encoding": "identity",
                        "User-Agent": "Zelo-Qualification/1",
                    },
                    extensions={
                        "sni_hostname": endpoint.host,
                        "timeout": dict.fromkeys(("connect", "read", "write", "pool"), remaining),
                    },
                )
                # Direct transport has no client cookie/auth defaults, redirects or proxy lookup.
                response = await transport.handle_async_request(request)
                media = self._validate_response(response)
                body = bytearray()
                async for chunk in response.aiter_raw():
                    self._bytes += len(chunk)  # Charge even the chunk that fails a bound.
                    if (
                        len(body) + len(chunk) > self._policy.response_bytes
                        or self._bytes > self._policy.aggregate_bytes
                    ):
                        self._poisoned = True
                        raise QualificationError("byte_budget_exceeded")
                    body.extend(chunk)
                try:
                    text = body.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise QualificationError("invalid_utf8") from None
                return PageEvidence(
                    binding,
                    self._policy.digest,
                    endpoint.origin + path,
                    bytes(body),
                    text,
                    media,
                )
        finally:

            async def cleanup() -> None:
                try:
                    if response is not None:
                        await response.aclose()
                finally:
                    if transport is not None:
                        await transport.aclose()

            self._cleanup = asyncio.create_task(cleanup())
            if not await settle(self._cleanup):
                self._poisoned = True
                self._cleanup.add_done_callback(_consume_exception)
            self._active = None
            if asyncio.current_task().cancelling():
                self._closed = True
                raise asyncio.CancelledError
            if self._poisoned:
                raise QualificationError("session_poisoned")

    @staticmethod
    def _validate_response(response: httpx.Response) -> str:
        if not 200 <= response.status_code < 300:
            raise QualificationError("status_denied")
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise QualificationError("encoding_denied")
        # Deliberately reject every disposition, including ambiguous duplicates.
        if "content-disposition" in response.headers:
            raise QualificationError("disposition_denied")
        match = re.fullmatch(
            r'(text/html|text/plain)(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?',
            response.headers.get("content-type", "").strip().lower(),
        )
        if match is None:
            raise QualificationError("media_denied")
        return match[1]

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            owner = self._active
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
                    try:
                        if self._cleanup is not None:
                            await self._cleanup
                    finally:
                        await self._resolver.aclose()

            self._close_task = asyncio.create_task(close_owned())
            self._close_task.add_done_callback(_consume_exception)
        if not await settle(self._close_task):
            self._poisoned = True
            raise QualificationError("close_cleanup_unproven")
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
