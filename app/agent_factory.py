"""Trusted specialist-agent descriptors, registry, and factory bindings."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_DESCRIPTOR_SCHEMA = "zelo-agent-descriptor-v1"
AGENT_BINDING_SCHEMA = "zelo-agent-binding-v1"
AGENT_REGISTRY_SCHEMA = "zelo-agent-registry-v1"

AgentName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
MemoryNamespace = Annotated[
    str,
    Field(min_length=7, max_length=128, pattern=r"^agent/[a-z][a-z0-9_-]*$"),
]


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentDescriptor(AgentContract):
    """Trusted Zelo-owned specialist identity and upper-bound authority metadata."""

    schema_version: Literal["zelo-agent-descriptor-v1"] = AGENT_DESCRIPTOR_SCHEMA
    agent_id: AgentName
    role: AgentName
    capabilities: tuple[AgentName, ...] = Field(min_length=1, max_length=32)
    allowed_task_kinds: tuple[AgentName, ...] = Field(default=(), max_length=32)
    allowed_tools: tuple[AgentName, ...] = Field(default=(), max_length=64)
    memory_namespace: MemoryNamespace
    delegation_role: AgentName | None = None
    delegation_capabilities: tuple[AgentName, ...] = Field(default=(), max_length=32)
    enabled: bool = True
    version: int = Field(default=1, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def _validate_canonical_identity(self):
        for field in (
            "capabilities",
            "allowed_task_kinds",
            "allowed_tools",
            "delegation_capabilities",
        ):
            value = getattr(self, field)
            if value != tuple(sorted(set(value))):
                raise ValueError(f"{field}_not_canonical")

        if self.memory_namespace != f"agent/{self.agent_id}":
            raise ValueError("memory_namespace_mismatch")

        if self.delegation_role is None:
            if self.delegation_capabilities:
                raise ValueError("delegation_capabilities_without_role")
        elif not self.delegation_capabilities:
            raise ValueError("delegation_role_without_capabilities")

        return self


class AgentBinding(AgentContract):
    """Exact trusted registry snapshot returned by AgentFactory."""

    schema_version: Literal["zelo-agent-binding-v1"] = AGENT_BINDING_SCHEMA
    descriptor: AgentDescriptor
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _registry_version(agents: Mapping[str, AgentDescriptor]) -> str:
    payload = {
        "schema": AGENT_REGISTRY_SCHEMA,
        "agents": [agents[agent_id].model_dump(mode="json") for agent_id in sorted(agents)],
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class AgentRegistry:
    """Immutable process-local registry populated only by trusted assembly."""

    def __init__(self, descriptors: Iterable[AgentDescriptor] = ()) -> None:
        agents: dict[str, AgentDescriptor] = {}
        for descriptor in descriptors:
            trusted = AgentDescriptor.model_validate(descriptor.model_dump(mode="json"))
            if trusted.agent_id in agents:
                raise ValueError("duplicate_agent_id")
            agents[trusted.agent_id] = trusted

        self._version = _registry_version(agents)
        self._agents: Mapping[str, AgentDescriptor] = MappingProxyType(agents)

    @property
    def version(self) -> str:
        return self._version

    def list(self) -> tuple[AgentDescriptor, ...]:
        return tuple(self._agents[agent_id] for agent_id in sorted(self._agents))

    def get(self, agent_id: str) -> AgentDescriptor | None:
        return self._agents.get(agent_id)


class AgentFactory:
    """Resolve enabled trusted specialists without widening their authority."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def bind(self, agent_id: str) -> AgentBinding:
        descriptor = self.registry.get(agent_id)
        if descriptor is None:
            raise ValueError("agent_not_registered")
        if not descriptor.enabled:
            raise ValueError("agent_disabled")
        return AgentBinding(
            descriptor=descriptor,
            registry_version=self.registry.version,
        )

    def matches(self, binding: AgentBinding) -> bool:
        current = self.registry.get(binding.descriptor.agent_id)
        return (
            current is not None
            and current.enabled
            and binding.registry_version == self.registry.version
            and current == binding.descriptor
        )
