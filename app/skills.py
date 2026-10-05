"""Trusted non-authoritative skill vocabulary for v0.15."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_factory import AgentSpec
from app.capabilities import CapabilityId, CapabilityRegistry

SKILL_SPEC_SCHEMA = "zelo-skill-spec-v1"
SKILL_REGISTRY_SCHEMA = "zelo-skill-registry-v1"

SkillId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
SkillName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
SkillLifecycle = Literal["draft", "active", "retired"]


class SkillContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillSpec(SkillContract):
    """Trusted skill metadata; describes compatibility but grants no authority."""

    schema_version: Literal["zelo-skill-spec-v1"] = SKILL_SPEC_SCHEMA
    skill_id: SkillId
    version: int = Field(default=1, ge=1, le=1_000_000)
    description: str = Field(min_length=1, max_length=512)
    required_capabilities: tuple[CapabilityId, ...] = Field(min_length=1, max_length=32)
    procedures: tuple[SkillName, ...] = Field(min_length=1, max_length=32)
    task_kinds: tuple[SkillName, ...] = Field(default=(), max_length=32)
    tools: tuple[SkillName, ...] = Field(default=(), max_length=64)
    lifecycle: SkillLifecycle = "draft"

    @model_validator(mode="after")
    def _canonicalize(self):
        for field_name in ("required_capabilities", "procedures", "task_kinds", "tools"):
            value = getattr(self, field_name)
            if value != tuple(sorted(set(value))):
                raise ValueError(f"{field_name}_not_canonical")
        return self


class SkillRegistrySnapshot(SkillContract):
    registry_schema: Literal["zelo-skill-registry-v1"] = SKILL_REGISTRY_SCHEMA
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    skills: tuple[SkillSpec, ...]


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class SkillRegistry:
    """Immutable skill catalog assembled only by trusted Core configuration."""

    def __init__(self, skills: Iterable[SkillSpec] = ()) -> None:
        trusted: dict[str, SkillSpec] = {}
        for spec in skills:
            item = SkillSpec.model_validate(spec.model_dump(mode="json"))
            if item.skill_id in trusted:
                raise ValueError("duplicate_skill_id")
            trusted[item.skill_id] = item
        self._skills: Mapping[str, SkillSpec] = MappingProxyType(trusted)
        self._version = _canonical_digest(
            {
                "schema": SKILL_REGISTRY_SCHEMA,
                "skills": [trusted[item].model_dump(mode="json") for item in sorted(trusted)],
            }
        )

    @property
    def version(self) -> str:
        return self._version

    def list(self) -> tuple[SkillSpec, ...]:
        return tuple(self._skills[item] for item in sorted(self._skills))

    def get(self, skill_id: str) -> SkillSpec | None:
        return self._skills.get(skill_id)

    def validate(
        self,
        capability_registry: CapabilityRegistry,
        *,
        available_task_kinds: Iterable[str],
        available_tools: Iterable[str],
    ) -> None:
        task_kinds = set(available_task_kinds)
        tools = set(available_tools)
        for spec in self._skills.values():
            if spec.lifecycle != "active":
                continue
            capability_registry.validate(spec.required_capabilities, subject="agent")
            if missing := set(spec.task_kinds).difference(task_kinds):
                raise ValueError(
                    f"skill_task_kind_unavailable:{spec.skill_id}:{sorted(missing)[0]}"
                )
            if missing := set(spec.tools).difference(tools):
                raise ValueError(f"skill_tool_unavailable:{spec.skill_id}:{sorted(missing)[0]}")

    def validate_agents(self, agents: Iterable[AgentSpec]) -> None:
        active_skills = tuple(spec for spec in self._skills.values() if spec.lifecycle == "active")
        active_by_id = {skill.skill_id: skill for skill in active_skills}
        for agent in agents:
            if agent.lifecycle != "active":
                continue
            for skill_id in agent.skill_ids:
                skill = active_by_id.get(skill_id)
                if skill is None:
                    raise ValueError(f"agent_skill_unavailable:{agent.agent_id}:{skill_id}")
                if not self._compatible(agent, skill):
                    raise ValueError(f"agent_skill_incompatible:{agent.agent_id}:{skill_id}")
            for procedure in agent.procedures:
                if not any(self._supports(agent, procedure, skill) for skill in active_skills):
                    raise ValueError(
                        f"agent_procedure_skill_unavailable:{agent.agent_id}:{procedure}"
                    )

    @staticmethod
    def _compatible(agent: AgentSpec, skill: SkillSpec) -> bool:
        return (
            set(skill.required_capabilities).issubset(agent.capabilities)
            and set(skill.task_kinds).issubset(agent.allowed_task_kinds)
            and set(skill.tools).issubset(agent.allowed_tools)
        )

    @classmethod
    def _supports(cls, agent: AgentSpec, procedure: str, skill: SkillSpec) -> bool:
        return procedure in skill.procedures and cls._compatible(agent, skill)

    def snapshot(self) -> SkillRegistrySnapshot:
        return SkillRegistrySnapshot(registry_version=self.version, skills=self.list())


def default_skill_registry() -> SkillRegistry:
    """Describe procedures used by the current trusted default agents."""

    shared_tools = ("list_workspace_files", "read_workspace_file", "repo_status")
    return SkillRegistry(
        (
            SkillSpec(
                skill_id="computer_observation",
                description=(
                    "Inspect bounded browser and workspace evidence from an authorized "
                    "persistent Agent computer without mutating it."
                ),
                required_capabilities=("computer_read",),
                procedures=("computer_observation",),
                task_kinds=("computer_run",),
                lifecycle="active",
            ),
            SkillSpec(
                skill_id="core_implementation",
                description="Plan and implement bounded Zelo Core software changes.",
                required_capabilities=("code_change", "code_planning"),
                procedures=("implementation",),
                task_kinds=("developer_run",),
                tools=shared_tools,
                lifecycle="active",
            ),
            SkillSpec(
                skill_id="core_review",
                description="Perform bounded independent review of Zelo Core software changes.",
                required_capabilities=("code_review",),
                procedures=("review",),
                task_kinds=("developer_run",),
                tools=shared_tools,
                lifecycle="active",
            ),
            SkillSpec(
                skill_id="evidence_retrieval",
                description="Gather and synthesize bounded evidence through read-only retrieval.",
                required_capabilities=("evidence_synthesis", "research"),
                procedures=("evidence_retrieval",),
                task_kinds=("research_run",),
                tools=shared_tools,
                lifecycle="active",
            ),
            SkillSpec(
                skill_id="api_read",
                description="Read bounded JSON from trusted configured HTTPS APIs.",
                required_capabilities=("api_read",),
                procedures=("api_read",),
                task_kinds=("api_run",),
                lifecycle="active",
            ),
            SkillSpec(
                skill_id="shell_execution",
                description="Run bounded built-in Shell text compute in a closed no-network guest.",
                required_capabilities=("shell_execution",),
                procedures=("shell_execution",),
                task_kinds=("shell_run",),
                lifecycle="active",
            ),
            SkillSpec(
                skill_id="python_execution",
                description="Run bounded Python computations in isolated no-network compute.",
                required_capabilities=("python_execution",),
                procedures=("python_execution",),
                task_kinds=("python_run",),
                lifecycle="active",
            ),
        )
    )
