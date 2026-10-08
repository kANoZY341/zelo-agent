"""Inert REA integration candidate, outside live Core authority registries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.agent_factory import AgentSpec, reverse_engineering_agent_spec
from app.capabilities import CapabilitySpec, rea_reverse_engineering_capability_spec
from app.skills import SkillSpec, rea_investigation_skill_spec


class ReaCandidateSnapshot(BaseModel):
    """Operator-visible candidate metadata with no execution or installation rights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["zelo-rea-candidate-v1"] = "zelo-rea-candidate-v1"
    integration_id: Literal["rea"] = "rea"
    lifecycle: Literal["draft"] = "draft"
    source_tag: Literal["rea-agents-6.0.0"] = "rea-agents-6.0.0"
    source_commit: Literal["6fee42689ae6e95a0182e0d4d0f55644e73e72be"] = (
        "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
    )
    source_path: Literal["third_party/rea"] = "third_party/rea"
    tools_available: Literal[139] = 139
    tool_grants: tuple[str, ...] = ()
    execution_enabled: Literal[False] = False
    agent: AgentSpec
    capability: CapabilitySpec
    skill: SkillSpec


def rea_candidate_snapshot() -> ReaCandidateSnapshot:
    """Show the REA intent without registering an agent, worker, tool or MCP process."""

    return ReaCandidateSnapshot(
        agent=reverse_engineering_agent_spec(),
        capability=rea_reverse_engineering_capability_spec(),
        skill=rea_investigation_skill_spec(),
    )
