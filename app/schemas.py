"""Validated API and worker contracts. No executable or opaque serialized state."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.policy import State


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TextPayload(StrictModel):
    text: str = Field(max_length=65536)


class TaskInput(StrictModel):
    payload: TextPayload
    timeout_seconds: float = Field(default=10.0, ge=0.01, le=60, allow_inf_nan=False)


class EchoInput(TaskInput):
    kind: Literal["echo"]


class TextStatsInput(TaskInput):
    kind: Literal["text_stats"]


class ApprovalDemoInput(TaskInput):
    kind: Literal["approval_demo"]


class AIPromptPayload(StrictModel):
    prompt: str = Field(min_length=1, max_length=65536)
    profile: Literal["general", "reasoning"]


class AIPromptInput(TaskInput):
    kind: Literal["ai_prompt"]
    payload: AIPromptPayload


class ToolCallPayload(StrictModel):
    tool: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallInput(TaskInput):
    kind: Literal["tool_call"]
    payload: ToolCallPayload


TaskCreate = Annotated[
    EchoInput | TextStatsInput | ApprovalDemoInput | AIPromptInput | ToolCallInput,
    Field(discriminator="kind"),
]


class EchoResult(StrictModel):
    text: str = Field(max_length=65536)


class TextStatsResult(StrictModel):
    characters: int = Field(ge=0)
    words: int = Field(ge=0)
    lines: int = Field(ge=0)


class ApprovalDemoResult(StrictModel):
    text: str = Field(max_length=65536)
    approved: Literal[True] = True


class AIPromptResult(StrictModel):
    text: str = Field(max_length=65536)
    provider: str
    alias: str
    actual_model: str
    usage: dict[str, int | float | None] = Field(default_factory=dict)
    timing: dict[str, int | float | None] = Field(default_factory=dict)
    completed_at: datetime


class AttemptResponse(StrictModel):
    id: str
    number: int
    started_at: datetime
    ended_at: datetime | None
    outcome: State
    result: dict[str, Any] | None
    error: str | None
    cancellation_requested_at: datetime | None


class ApprovalResponse(StrictModel):
    id: str
    task_id: str
    action_version: int
    action_hash: str
    status: Literal["pending", "approved", "rejected", "expired", "invalidated", "cancelled"]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class TaskResponse(StrictModel):
    id: str
    kind: Literal["echo", "text_stats", "approval_demo", "ai_prompt", "tool_call"]
    payload: TextPayload | AIPromptPayload | ToolCallPayload
    timeout_seconds: float
    status: State
    action_version: int
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool
    result: dict[str, Any] | None
    error: str | None
    attempts: list[AttemptResponse] = Field(default_factory=list)
    approvals: list[ApprovalResponse] = Field(default_factory=list)
    selected_alias: str | None = None
    selected_model: str | None = None
    config_version: str | None = None


class TaskPage(StrictModel):
    items: list[TaskResponse]
    limit: int
    offset: int
    total: int


class ApprovalDecision(StrictModel):
    decision: Literal["approve", "reject"]


class AuditResponse(StrictModel):
    id: str
    task_id: str
    attempt_id: str | None
    approval_id: str | None
    event: str
    created_at: datetime
    details: dict[str, Any]
