"""Versioned specialist and department contracts for the v0.14 Agent Factory."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_SPEC_SCHEMA = "zelo-agent-spec-v1"
DEPARTMENT_SPEC_SCHEMA = "zelo-department-spec-v1"
AGENT_REGISTRY_SCHEMA = "zelo-agent-registry-v1"

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
MemoryNamespace = Annotated[
    str,
    Field(min_length=7, max_length=128, pattern=r"^agent/[a-z][a-z0-9_-]*$"),
]
Lifecycle = Literal["draft", "active", "retired"]


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerRequirement(AgentContract):
    """Worker-neutral requirement resolved later by Zelo's Supervisor."""

    role: Identifier
    capabilities: tuple[Identifier, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _canonicalize(self):
        if self.capabilities != tuple(sorted(set(self.capabilities))):
            raise ValueError("worker_capabilities_not_canonical")
        return self


class AgentSpec(AgentContract):
    """Trusted versioned specialist template; never model-authored runtime input."""

    schema_version: Literal["zelo-agent-spec-v1"] = AGENT_SPEC_SCHEMA
    agent_id: Identifier
    version: int = Field(default=1, ge=1, le=1_000_000)
    role: Identifier
    objective: str = Field(min_length=1, max_length=1024)
    capabilities: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    allowed_task_kinds: tuple[Identifier, ...] = Field(default=(), max_length=32)
    allowed_tools: tuple[Identifier, ...] = Field(default=(), max_length=64)
    permissions: tuple[Identifier, ...] = Field(default=(), max_length=32)
    procedures: tuple[Identifier, ...] = Field(default=(), max_length=32)
    memory_namespace: MemoryNamespace
    worker_requirement: WorkerRequirement | None = None
    review_required: bool = False
    lifecycle: Lifecycle = "draft"
    max_active_instances: int = Field(default=1, ge=1, le=64)
    allow_subagents: bool = False
    max_subagents: int = Field(default=0, ge=0, le=16)

    @model_validator(mode="after")
    def _validate_canonical_identity(self):
        for field in (
            "capabilities",
            "allowed_task_kinds",
            "allowed_tools",
            "permissions",
            "procedures",
        ):
            value = getattr(self, field)
            if value != tuple(sorted(set(value))):
                raise ValueError(f"{field}_not_canonical")

        if self.memory_namespace != f"agent/{self.agent_id}":
            raise ValueError("memory_namespace_mismatch")
        if self.allow_subagents and self.max_subagents < 1:
            raise ValueError("subagent_limit_required")
        if not self.allow_subagents and self.max_subagents != 0:
            raise ValueError("subagent_limit_without_authority")
        return self


class DepartmentSpec(AgentContract):
    """Trusted department grouping with explicit manager/producer assignments."""

    schema_version: Literal["zelo-department-spec-v1"] = DEPARTMENT_SPEC_SCHEMA
    department_id: Identifier
    version: int = Field(default=1, ge=1, le=1_000_000)
    objective: str = Field(min_length=1, max_length=1024)
    manager_agent_id: Identifier | None = None
    producer_agent_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    lifecycle: Lifecycle = "draft"

    @model_validator(mode="after")
    def _validate_assignments(self):
        if self.producer_agent_ids != tuple(sorted(set(self.producer_agent_ids))):
            raise ValueError("producer_agent_ids_not_canonical")
        if self.manager_agent_id in self.producer_agent_ids:
            raise ValueError("manager_cannot_be_producer")
        return self


class AgentBinding(AgentContract):
    schema_version: Literal["zelo-agent-binding-v1"] = "zelo-agent-binding-v1"
    spec: AgentSpec
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class DepartmentBinding(AgentContract):
    schema_version: Literal["zelo-department-binding-v1"] = "zelo-department-binding-v1"
    spec: DepartmentSpec
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _registry_version(
    agents: Mapping[str, AgentSpec],
    departments: Mapping[str, DepartmentSpec],
) -> str:
    payload = {
        "schema": AGENT_REGISTRY_SCHEMA,
        "agents": [agents[item].model_dump(mode="json") for item in sorted(agents)],
        "departments": [
            departments[item].model_dump(mode="json") for item in sorted(departments)
        ],
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class AgentRegistry:
    """Immutable trusted catalog of agent and department templates."""

    def __init__(
        self,
        agents: Iterable[AgentSpec] = (),
        departments: Iterable[DepartmentSpec] = (),
    ) -> None:
        trusted_agents: dict[str, AgentSpec] = {}
        for spec in agents:
            trusted = AgentSpec.model_validate(spec.model_dump(mode="json"))
            if trusted.agent_id in trusted_agents:
                raise ValueError("duplicate_agent_id")
            trusted_agents[trusted.agent_id] = trusted

        trusted_departments: dict[str, DepartmentSpec] = {}
        for spec in departments:
            trusted = DepartmentSpec.model_validate(spec.model_dump(mode="json"))
            if trusted.department_id in trusted_departments:
                raise ValueError("duplicate_department_id")
            assigned = set(trusted.producer_agent_ids)
            if trusted.manager_agent_id is not None:
                assigned.add(trusted.manager_agent_id)
            if missing := assigned.difference(trusted_agents):
                raise ValueError(f"department_agent_not_registered:{sorted(missing)[0]}")
            trusted_departments[trusted.department_id] = trusted

        self._agents: Mapping[str, AgentSpec] = MappingProxyType(trusted_agents)
        self._departments: Mapping[str, DepartmentSpec] = MappingProxyType(trusted_departments)
        self._version = _registry_version(self._agents, self._departments)

    @property
    def version(self) -> str:
        return self._version

    def list_agents(self) -> tuple[AgentSpec, ...]:
        return tuple(self._agents[item] for item in sorted(self._agents))

    def list_departments(self) -> tuple[DepartmentSpec, ...]:
        return tuple(self._departments[item] for item in sorted(self._departments))

    def get_agent(self, agent_id: str) -> AgentSpec | None:
        return self._agents.get(agent_id)

    def get_department(self, department_id: str) -> DepartmentSpec | None:
        return self._departments.get(department_id)

    def validate_activation(
        self,
        *,
        available_task_kinds: Iterable[str],
        available_tools: Iterable[str],
    ) -> None:
        task_kinds = set(available_task_kinds)
        tools = set(available_tools)

        for spec in self._agents.values():
            if spec.lifecycle != "active":
                continue
            if missing := set(spec.allowed_task_kinds).difference(task_kinds):
                raise ValueError(f"agent_task_kind_unavailable:{spec.agent_id}:{sorted(missing)[0]}")
            if missing := set(spec.allowed_tools).difference(tools):
                raise ValueError(f"agent_tool_unavailable:{spec.agent_id}:{sorted(missing)[0]}")

        for spec in self._departments.values():
            if spec.lifecycle != "active":
                continue
            assigned = set(spec.producer_agent_ids)
            if spec.manager_agent_id is not None:
                assigned.add(spec.manager_agent_id)
            for agent_id in assigned:
                agent = self._agents[agent_id]
                if agent.lifecycle != "active":
                    raise ValueError(
                        f"department_agent_not_active:{spec.department_id}:{agent_id}"
                    )


class AgentFactory:
    """Bind exact active registry snapshots without granting execution authority."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def bind_agent(self, agent_id: str) -> AgentBinding:
        spec = self.registry.get_agent(agent_id)
        if spec is None:
            raise ValueError("agent_not_registered")
        if spec.lifecycle != "active":
            raise ValueError("agent_not_active")
        return AgentBinding(spec=spec, registry_version=self.registry.version)

    def bind_department(self, department_id: str) -> DepartmentBinding:
        spec = self.registry.get_department(department_id)
        if spec is None:
            raise ValueError("department_not_registered")
        if spec.lifecycle != "active":
            raise ValueError("department_not_active")
        return DepartmentBinding(spec=spec, registry_version=self.registry.version)

    def matches_agent(self, binding: AgentBinding) -> bool:
        current = self.registry.get_agent(binding.spec.agent_id)
        return (
            current is not None
            and current.lifecycle == "active"
            and binding.registry_version == self.registry.version
            and current == binding.spec
        )

    def matches_department(self, binding: DepartmentBinding) -> bool:
        current = self.registry.get_department(binding.spec.department_id)
        return (
            current is not None
            and current.lifecycle == "active"
            and binding.registry_version == self.registry.version
            and current == binding.spec
        )


def coding_agent_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="coding",
        role="coding",
        objective="Plan, implement, and review bounded software changes through Zelo-owned runtimes.",
        capabilities=("code_change", "code_planning", "code_review"),
        allowed_task_kinds=("developer_run",),
        allowed_tools=("list_workspace_files", "read_workspace_file", "repo_status"),
        permissions=("delegate_developer", "workspace_read"),
        procedures=("implementation", "review"),
        memory_namespace="agent/coding",
        worker_requirement=WorkerRequirement(
            role="developer",
            capabilities=("zelo_core_development",),
        ),
        review_required=True,
        lifecycle="active",
    )


def research_agent_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="research",
        role="research",
        objective="Produce bounded evidence-grounded research through Zelo-owned retrieval tools.",
        capabilities=("evidence_synthesis", "research"),
        allowed_task_kinds=("research_run",),
        allowed_tools=("list_workspace_files", "read_workspace_file", "repo_status"),
        permissions=("workspace_read",),
        procedures=("evidence_retrieval",),
        memory_namespace="agent/research",
        lifecycle="active",
    )


def default_agent_registry() -> AgentRegistry:
    agents = (coding_agent_spec(), research_agent_spec())
    departments = (
        DepartmentSpec(
            department_id="engineering",
            objective="Coordinate bounded software engineering work under Zelo authority.",
            producer_agent_ids=("coding",),
            lifecycle="active",
        ),
        DepartmentSpec(
            department_id="research",
            objective="Coordinate bounded evidence gathering and synthesis under Zelo authority.",
            producer_agent_ids=("research",),
            lifecycle="active",
        ),
    )
    return AgentRegistry(agents, departments)
