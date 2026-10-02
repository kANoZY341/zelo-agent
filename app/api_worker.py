"""Bounded read-only API worker; all networking stays inside trusted Core."""

import asyncio
import hashlib
import ipaddress
import json
import math
import re
import socket
import zlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.api_contracts import (
    API_LOCAL_SUFFIXES,
    API_PATH_LIMIT,
    API_QUERY_LIMIT,
    ApiEndpointRegistry,
    ApiExecutionSpec,
    public_address,
)
from app.developer_contracts import WorkerSelectionSnapshot
from app.schemas import ApiPayload, ApiRunResult

API_POLICY_SCHEMA = "zelo-api-read-v1"
API_RESPONSE_LIMIT = 262_144
API_USER_AGENT = "Zelo-API-Worker/0.15"
ApiEventSink = Callable[[str, dict[str, Any]], None]
ApiResolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


class ApiRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def api_policy_semantics() -> dict[str, object]:
    # Bump schema for implementation changes not represented by these values.
    return {
        "schema": API_POLICY_SCHEMA,
        "methods": ("GET",),
        "https_only": True,
        "follow_redirects": False,
        "trust_environment": False,
        "credentials": False,
        "headers": {
            "Accept": "application/json",
            "User-Agent": API_USER_AGENT,
            "Host": "canonical-endpoint-authority",
        },
        "dns": "execution-time-all-answers-global-unicast-first-address-only",
        "ip_exclusions": "non-global,multicast,reserved,scoped,mapped,6to4,teredo,site-local",
        "local_suffixes": API_LOCAL_SUFFIXES,
        "connection": "pinned-ip-verified-tls-sni-endpoint-host-http1-no-retries",
        "response_content_types": ("application/json", "application/*+json"),
        "response_bytes": API_RESPONSE_LIMIT,
        "wire_bytes": API_RESPONSE_LIMIT,
        "decoding": "identity-or-single-gzip-or-zlib-deflate-bounded-during-inflation",
        "json": "utf8-required-nonempty-unique-keys-finite-numbers-depth-64",
        "query_bytes": API_QUERY_LIMIT,
        "query": "32-string-pairs-key-1..128-value-0..2048-no-cr-lf-nul",
        "path_bytes": API_PATH_LIMIT,
        "path": "ascii-pchar-no-percent-backslash-dot-segments-double-slash",
        "timeout": "total-dns-and-request-0.1..30-seconds",
        "endpoint_registry": "immutable-canonical-https-origins-max-32-v1",
    }


def api_policy_version() -> str:
    encoded = json.dumps(
        api_policy_semantics(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _invalid_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _parse_body(data: bytes) -> Any:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
            parse_float=_finite_float,
        )
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 64:
                raise ValueError("JSON nesting too deep")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        # Reject unpaired surrogate escapes before result persistence/serialization.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise ApiRuntimeError("api_response_invalid") from None


async def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    # Numeric endpoints need no DNS. A final dot disables resolver search suffixes.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        dns_host = host + "."
    else:
        return (host,)
    try:
        rows = await asyncio.to_thread(
            socket.getaddrinfo,
            dns_host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError:
        raise ApiRuntimeError("api_target_unavailable") from None
    return tuple(sorted({row[4][0] for row in rows}))


def _pinned_url(spec: ApiExecutionSpec, address: str, path: str) -> tuple[str, str]:
    endpoint = spec.endpoint.endpoint
    address_host = f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
    port_suffix = "" if endpoint.port == 443 else f":{endpoint.port}"
    return f"https://{address_host}{port_suffix}{path}", urlsplit(endpoint.origin).netloc


async def _read_response(response: httpx.Response) -> bytes:
    if 300 <= response.status_code < 400:
        raise ApiRuntimeError("api_redirect_denied")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"application/(?:json|[a-z0-9!#$&^_.+-]+\+json)", media_type):
        raise ApiRuntimeError("api_content_type_denied")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        if not re.fullmatch(r"[0-9]{1,10}", content_length):
            raise ApiRuntimeError("api_response_invalid")
        if int(content_length) > API_RESPONSE_LIMIT:
            raise ApiRuntimeError("api_response_too_large")
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip", "deflate"}:
        raise ApiRuntimeError("api_content_encoding_denied")
    decoder = (
        None
        if encoding == "identity"
        else zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
    )
    data = bytearray()
    wire_bytes = 0
    try:
        async for chunk in response.aiter_raw():
            wire_bytes += len(chunk)
            if wire_bytes > API_RESPONSE_LIMIT:
                raise ApiRuntimeError("api_response_too_large")
            decoded = (
                chunk
                if decoder is None
                else decoder.decompress(chunk, API_RESPONSE_LIMIT + 1 - len(data))
            )
            if len(data) + len(decoded) > API_RESPONSE_LIMIT:
                raise ApiRuntimeError("api_response_too_large")
            data.extend(decoded)
        if decoder is not None and (not decoder.eof or decoder.unused_data):
            raise ApiRuntimeError("api_response_invalid")
    except zlib.error:
        raise ApiRuntimeError("api_response_invalid") from None
    return bytes(data)


class ApiRuntime:
    """Execute one GET against an exact trusted binding; injectable seams are operator-only."""

    def __init__(
        self,
        endpoints: ApiEndpointRegistry,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: ApiResolver | None = None,
    ) -> None:
        self.endpoints = endpoints
        self.transport = transport
        self.resolver = resolver or _system_resolver

    def bind(self, worker_selection: WorkerSelectionSnapshot, endpoint_id: str) -> ApiExecutionSpec:
        try:
            endpoint = self.endpoints.bind(endpoint_id)
        except ValueError:
            raise ApiRuntimeError("api_endpoint_not_registered") from None
        return ApiExecutionSpec(
            endpoint=endpoint,
            worker_selection=worker_selection,
            policy_version=api_policy_version(),
        )

    def matches(self, spec: ApiExecutionSpec) -> bool:
        return self.endpoints.matches(spec.endpoint) and spec.policy_version == api_policy_version()

    async def run(
        self,
        *,
        spec: ApiExecutionSpec,
        path: str,
        query: dict[str, str],
        timeout_seconds: float,
        emit: ApiEventSink,
    ) -> dict[str, Any]:
        if (
            not self.matches(spec)
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0.1 <= timeout_seconds <= 30.0
        ):
            raise ApiRuntimeError("api_execution_spec_invalid")
        endpoint = spec.endpoint.endpoint
        try:
            payload = ApiPayload(endpoint_id=endpoint.endpoint_id, path=path, query=query)
        except ValueError:
            raise ApiRuntimeError("api_input_invalid") from None
        emit("api_execution_started", {"endpoint_id": endpoint.endpoint_id})
        try:
            async with asyncio.timeout(timeout_seconds):
                addresses = await self.resolver(endpoint.host, endpoint.port)
                if not addresses:
                    raise ApiRuntimeError("api_target_unavailable")
                try:
                    addresses = tuple(public_address(address) for address in addresses)
                except ValueError:
                    raise ApiRuntimeError("api_target_not_public") from None
                target, host_header = _pinned_url(spec, addresses[0], payload.path)
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=httpx.Timeout(float(timeout_seconds)),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    # A Request avoids client defaults, cookie merging and Accept-Encoding.
                    request = httpx.Request(
                        "GET",
                        target,
                        params=payload.query,
                        headers={
                            "Accept": "application/json",
                            "Host": host_header,
                            "User-Agent": API_USER_AGENT,
                        },
                        extensions={"sni_hostname": endpoint.host},
                    )
                    response = await client.send(request, stream=True, follow_redirects=False)
                    try:
                        data = await _read_response(response)
                    finally:
                        await response.aclose()
        except (TimeoutError, httpx.TimeoutException):
            raise ApiRuntimeError("api_timeout") from None
        except (httpx.HTTPError, OSError):
            raise ApiRuntimeError("api_target_unavailable") from None
        body = _parse_body(data)
        emit(
            "api_execution_completed",
            {
                "endpoint_id": endpoint.endpoint_id,
                "status_code": response.status_code,
                "response_bytes": len(data),
            },
        )
        return ApiRunResult(
            endpoint_id=endpoint.endpoint_id,
            status_code=response.status_code,
            body=body,
            response_bytes=len(data),
            completed_at=datetime.now(UTC),
        ).model_dump(mode="json")
