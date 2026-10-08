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
    package_name: Literal["rea-agents"] = "rea-agents"
    package_version: Literal["5.0.0"] = "5.0.0"
    mcp_name: Literal["io.github.morluto/rea"] = "io.github.morluto/rea"
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
