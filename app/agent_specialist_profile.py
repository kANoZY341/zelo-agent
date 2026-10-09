"""OpenDots-inspired Specialist Dot profile sourced only from Zelo-owned Core.

No imported Dot service, skill promotion, Space/Tool/Computer grant or prompts
from repository contents are made active by reading this inspection view.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import AgentInstance, Project
from app.policy import DomainError
from app.skills import SkillRegistry
from app.storage import SessionStore


class ProfileTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str
    origin: Literal["trusted_agent_spec"] = "trusted_agent_spec"
    verified_for_task_execution: Literal[False] = False
    reason: Literal["requires_task_claim_and_tool_gateway"] = "requires_task_claim_and_tool_gateway"


class ProfileSpace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    space_id: str
    origin: Literal["trusted_agent_spec"] = "trusted_agent_spec"
    effective_access_verified: Literal[False] = False
    reason: Literal["space_runtime_enforcement_not_qualified"] = (
        "space_runtime_enforcement_not_qualified"
    )


class ProfileSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    skill_id: str
    version: int | None
    lifecycle: Literal["draft", "active", "retired", "not_registered"]
    declared_eligible: bool
    origin: Literal["core_skill_registry"] = "core_skill_registry"
    reason: Literal[
        "compatible_active_catalog_entry",
        "skill_not_registered",
        "skill_not_active",
        "agent_skill_incompatible",
    ]
    effective_execution_granted: Literal[False] = False


class AgentSpecialistProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-opendots-specialist-profile-v1"] = (
        "zelo-opendots-specialist-profile-v1"
    )
    agent_instance_id: str
    agent_id: str
    project_id: str
    agent_version: int
    registry_version: str
    lifecycle: Literal["active", "paused", "retired"]
    registry_binding_current: bool
    project_active: bool
    inspection_state: Literal["current", "instance_inactive", "project_inactive", "stale"]
    authority_source: Literal["zelo_agent_factory_registry"] = "zelo_agent_factory_registry"
    spec_instructions: str = Field(max_length=16384)
    instruction_origin: Literal["trusted_agent_spec", "unavailable"]
    memory_namespace: str
    configured_skill_learning: str
    qualified_skill_learning_activation: Literal[False] = False
    skill_declarations: tuple[ProfileSkill, ...] = Field(max_length=64)
    declared_spaces: tuple[ProfileSpace, ...] = Field(max_length=64)
    declared_tools: tuple[ProfileTool, ...] = Field(max_length=64)
    effective_space_access: tuple[()] = ()
    effective_worker_or_tool_grants: tuple[()] = ()
    upstream_dot_runtime_loaded: Literal[False] = False
    operation_or_permission_mutated: Literal[False] = False


class AgentSpecialistProfileService:
    """Render immutable AgentSpec metadata without interpreting it as authority."""

    def __init__(
        self, database: SessionStore, factory: AgentFactory, skills: SkillRegistry
    ) -> None:
        self.database = database
        self.factory = factory
        self.skills = skills

    def get(self, instance_id: str) -> AgentSpecialistProfile:
        with self.database.read() as session:
            instance = session.get(AgentInstance, instance_id)
            if instance is None:
                raise DomainError("Agent instance not found", 404)
            project = session.get(Project, instance.project_id)
            try:
                spec = AgentSpec.model_validate(instance.spec)
            except ValueError:
                raise DomainError("Agent instance spec is invalid", 409) from None
            binding = AgentBinding(spec=spec, registry_version=instance.registry_version)
            bound = (
                self.factory.matches_agent(binding)
                and spec.agent_id == instance.agent_id
                and spec.version == instance.agent_version
            )
            project_active = project is not None and project.status == "active"
            if not bound:
                state = "stale"
            elif instance.status != "active":
                state = "instance_inactive"
            elif not project_active:
                state = "project_inactive"
            else:
                state = "current"

            # Do not disclose/inject mutable stale snapshots as trusted agent
            # instructions or tools. They remain entirely hidden on stale binding.
            trusted = bound
            profiles: list[ProfileSkill] = []
            if trusted:
                for skill_id in spec.skill_ids:
                    skill = self.skills.get(skill_id)
                    if skill is None:
                        lifecycle = "not_registered"
                        reason = "skill_not_registered"
                    elif skill.lifecycle != "active":
                        lifecycle = skill.lifecycle
                        reason = "skill_not_active"
                    elif not SkillRegistry._compatible(spec, skill):
                        lifecycle = skill.lifecycle
                        reason = "agent_skill_incompatible"
                    else:
                        lifecycle = skill.lifecycle
                        reason = "compatible_active_catalog_entry"
                    profiles.append(
                        ProfileSkill(
                            skill_id=skill_id,
                            version=skill.version if skill is not None else None,
                            lifecycle=lifecycle,
                            declared_eligible=reason == "compatible_active_catalog_entry",
                            reason=reason,
                        )
                    )
            return AgentSpecialistProfile(
                agent_instance_id=instance.id,
                agent_id=instance.agent_id,
                project_id=instance.project_id,
                agent_version=instance.agent_version,
                registry_version=instance.registry_version,
                lifecycle=instance.status,
                registry_binding_current=bound,
                project_active=project_active,
                inspection_state=state,
                spec_instructions=spec.instructions if trusted else "",
                instruction_origin="trusted_agent_spec" if trusted else "unavailable",
                memory_namespace=spec.memory_namespace if trusted else "",
                configured_skill_learning=spec.skill_learning if trusted else "disabled",
                skill_declarations=tuple(profiles),
                declared_spaces=(
                    tuple(ProfileSpace(space_id=item) for item in spec.space_permissions)
                    if trusted
                    else ()
                ),
                declared_tools=(
                    tuple(ProfileTool(name=item) for item in spec.allowed_tools) if trusted else ()
                ),
            )
