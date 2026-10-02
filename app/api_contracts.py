"""Trusted endpoint and execution identity contracts for the v0.15 API Worker."""

import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.developer_contracts import WorkerSelectionSnapshot

API_QUERY_LIMIT = 8_192
API_PATH_LIMIT = 2_048
API_LOCAL_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "internal",
    "lan",
    "home",
    "home.arpa",
    "invalid",
    "test",
    "onion",
)

API_ENDPOINT_SCHEMA = "zelo-api-endpoint-v1"
API_ENDPOINT_REGISTRY_SCHEMA = "zelo-api-endpoint-registry-v1"
API_EXECUTION_SCHEMA = "zelo-api-execution-v1"
_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def public_address(value: str) -> str:
    """Require canonical global unicast, excluding transition and scoped IPv6."""
    address = ipaddress.ip_address(value)
    if (
        str(address) != value
        or "%" in value
        or not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or (
            address.version == 6
            and (
                address.ipv4_mapped is not None
                or address.sixtofour is not None
                or address.teredo is not None
                or address.is_site_local
            )
        )
    ):
        raise ValueError("api_target_not_public")
    return str(address)


class ApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_origin(value: str) -> tuple[str, int, str]:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ValueError("api_endpoint_origin_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port or 443
    except ValueError:
        raise ValueError("api_endpoint_origin_invalid") from None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise ValueError("api_endpoint_origin_invalid")
    if not host.isascii() or host != host.lower() or host.endswith("."):
        raise ValueError("api_endpoint_host_not_canonical")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (
            len(host) > 253
            or not _HOST_RE.fullmatch(host)
            or host.rsplit(".", 1)[-1].isdigit()
            or any(host == suffix or host.endswith("." + suffix) for suffix in API_LOCAL_SUFFIXES)
        ):
            raise ValueError("api_endpoint_host_invalid") from None
        canonical_host = host
    else:
        public_address(host)
        canonical_host = f"[{host}]" if address.version == 6 else host
    canonical = f"https://{canonical_host}" + (f":{port}" if port != 443 else "")
    if value != canonical:
        raise ValueError("api_endpoint_origin_not_canonical")
    return host, port, canonical


class ApiEndpointSpec(ApiContract):
    schema_version: Literal["zelo-api-endpoint-v1"] = API_ENDPOINT_SCHEMA
    endpoint_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    origin: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_origin(self):
        _canonical_origin(self.origin)
        return self

    @property
    def host(self) -> str:
        return _canonical_origin(self.origin)[0]

    @property
    def port(self) -> int:
        return _canonical_origin(self.origin)[1]


class ApiEndpointBinding(ApiContract):
    registry_schema: Literal["zelo-api-endpoint-registry-v1"] = API_ENDPOINT_REGISTRY_SCHEMA
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint: ApiEndpointSpec


class ApiExecutionSpec(ApiContract):
    schema_version: Literal["zelo-api-execution-v1"] = API_EXECUTION_SCHEMA
    endpoint: ApiEndpointBinding
    worker_selection: WorkerSelectionSnapshot
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class ApiEndpointRegistry:
    """Immutable trusted endpoint allowlist; configuration, not task input."""

    _endpoints: Mapping[str, ApiEndpointSpec]
    _version: str

    def __init__(self, endpoints: Iterable[ApiEndpointSpec] = ()) -> None:
        trusted: dict[str, ApiEndpointSpec] = {}
        for spec in endpoints:
            item = ApiEndpointSpec.model_validate(spec.model_dump(mode="json"))
            if item.endpoint_id in trusted:
                raise ValueError("duplicate_api_endpoint_id")
            trusted[item.endpoint_id] = item
            if len(trusted) > 32:
                raise ValueError("too_many_api_endpoints")
        object.__setattr__(self, "_endpoints", MappingProxyType(trusted))
        object.__setattr__(
            self,
            "_version",
            _digest(
                {
                    "schema": API_ENDPOINT_REGISTRY_SCHEMA,
                    "endpoints": [
                        trusted[item].model_dump(mode="json") for item in sorted(trusted)
                    ],
                }
            ),
        )

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> "ApiEndpointRegistry":
        specs: list[ApiEndpointSpec] = []
        for entry in entries:
            if not isinstance(entry, str) or entry.strip() != entry or "=" not in entry:
                raise ValueError("api_endpoint_entry_invalid")

            endpoint_id, origin = entry.split("=", 1)
            specs.append(ApiEndpointSpec(endpoint_id=endpoint_id, origin=origin))
            if len(specs) > 32:
                raise ValueError("too_many_api_endpoints")
        return cls(specs)

    @property
    def version(self) -> str:
        return self._version

    def list(self) -> tuple[ApiEndpointSpec, ...]:
        return tuple(self._endpoints[item] for item in sorted(self._endpoints))

    def get(self, endpoint_id: str) -> ApiEndpointSpec | None:
        return self._endpoints.get(endpoint_id)

    def bind(self, endpoint_id: str) -> ApiEndpointBinding:
        endpoint = self.get(endpoint_id)
        if endpoint is None:
            raise ValueError("api_endpoint_not_registered")
        return ApiEndpointBinding(
            registry_version=self.version,
            endpoint=endpoint,
        )

    def matches(self, binding: ApiEndpointBinding) -> bool:
        current = self.get(binding.endpoint.endpoint_id)
        return (
            binding.registry_version == self.version
            and current is not None
            and current == binding.endpoint
        )
