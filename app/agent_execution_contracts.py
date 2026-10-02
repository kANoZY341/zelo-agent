"""Immutable trusted execution identity for persisted v0.14 agent work."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_factory import AgentSpec

Uuid = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class AgentExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DelegationExecutionBinding(AgentExecutionContract):
    schema_version: Literal["zelo-agent-delegation-execution-v1"] = (
        "zelo-agent-delegation-execution-v1"
    )
    delegation_id: Uuid
    parent_instance_id: Uuid
    child_instance_id: Uuid
    campaign_id: Uuid
    budget_reservation_id: Uuid
    idempotency_key: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective: str = Field(min_length=1, max_length=2048)
    allowed_task_kinds: tuple[str, ...] = Field(min_length=1, max_length=16)
    required_capabilities: tuple[str, ...] = Field(min_length=1, max_length=32)
    depth: int = Field(ge=1, le=4)
    max_depth: int = Field(ge=1, le=4)
    budget_resource: str = Field(min_length=1, max_length=64)
    budget_units: int = Field(ge=1)

    @model_validator(mode="after")
    def _canonicalize(self):
        for field in ("allowed_task_kinds", "required_capabilities"):
            value = getattr(self, field)
            if value != tuple(sorted(set(value))):
                raise ValueError(f"{field}_not_canonical")
        if self.depth > self.max_depth:
            raise ValueError("delegation_depth_invalid")
        return self


class AgentExecutionBinding(AgentExecutionContract):
    schema_version: Literal["zelo-agent-execution-binding-v1"] = "zelo-agent-execution-binding-v1"
    agent_instance_id: Uuid
    project_id: Uuid
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec: AgentSpec
    task_kind: Literal["browser_run", "api_run", "developer_run", "python_run", "research_run"]
    delegation: DelegationExecutionBinding | None = None

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.task_kind not in self.spec.allowed_task_kinds:
            raise ValueError("agent_task_kind_not_allowed")
        if self.delegation is not None:
            if self.delegation.child_instance_id != self.agent_instance_id:
                raise ValueError("delegation_child_mismatch")
            if self.task_kind not in self.delegation.allowed_task_kinds:
                raise ValueError("delegation_task_kind_not_allowed")
            if not set(self.delegation.required_capabilities).issubset(self.spec.capabilities):
                raise ValueError("delegation_capability_mismatch")
        return self
