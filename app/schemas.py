"""Validated API and worker contracts. No executable or opaque serialized state."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.developer_contracts import DeveloperRequest, ReviewerReport, ValidationResult
from app.policy import State
from app.promotion_contracts import MergeRequest, PromotionRequest, ReleaseRequest


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


class AgentRunPayload(StrictModel):
    prompt: str = Field(min_length=1, max_length=65536)
    profile: Literal["general"] = "general"


class AgentRunInput(TaskInput):
    kind: Literal["agent_run"]
    payload: AgentRunPayload
    timeout_seconds: float = Field(default=120.0, ge=0.01, le=600, allow_inf_nan=False)


class SelfImprovementPayload(StrictModel):
    goal: str = Field(min_length=1, max_length=1024)


class SelfImprovementRunInput(TaskInput):
    kind: Literal["self_improvement_run"]
    payload: SelfImprovementPayload
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0, allow_inf_nan=False)


class DeveloperPayload(StrictModel):
    instruction: str = Field(min_length=1, max_length=65536)
    validation_profile: Literal["zelo_core"] = "zelo_core"


class DeveloperRunInput(TaskInput):
    kind: Literal["developer_run"]
    payload: DeveloperPayload
    timeout_seconds: float = Field(default=900.0, ge=1.0, le=3600, allow_inf_nan=False)


class PromotionPayload(StrictModel):
    developer_task_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    developer_attempt_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    target_base_branch: Literal["main"] = "main"
    branch: str = Field(
        min_length=16,
        max_length=96,
        pattern=r"^zelo/promotion/[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    commit_title: str = Field(min_length=1, max_length=200)
    commit_message: str = Field(default="", max_length=4096)
    pr_title: str = Field(min_length=1, max_length=256)
    pr_body: str = Field(default="", max_length=16384)


class PromotionRunInput(TaskInput):
    kind: Literal["promotion_run"]
    payload: PromotionPayload
    timeout_seconds: float = Field(default=300.0, ge=1.0, le=900.0, allow_inf_nan=False)


class MergePayload(StrictModel):
    promotion_task_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )


class MergeRunInput(TaskInput):
    kind: Literal["merge_run"]
    payload: MergePayload
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=300.0, allow_inf_nan=False)


class ReleasePayload(StrictModel):
    merge_task_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    tag: str = Field(
        min_length=6,
        max_length=64,
        pattern=r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$",
    )


class ReleaseRunInput(TaskInput):
    kind: Literal["release_run"]
    payload: ReleasePayload
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=300.0, allow_inf_nan=False)


class ToolCallPayload(StrictModel):
    tool: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallInput(TaskInput):
    kind: Literal["tool_call"]
    payload: ToolCallPayload


TaskCreate = Annotated[
    EchoInput
    | TextStatsInput
    | ApprovalDemoInput
    | AIPromptInput
    | ToolCallInput
    | AgentRunInput
    | SelfImprovementRunInput
    | DeveloperRunInput
    | PromotionRunInput
    | MergeRunInput
    | ReleaseRunInput,
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


class AgentRunResult(StrictModel):
    final_response: str = Field(min_length=1, max_length=16384)
    alias: Literal["local_general"]
    actual_model: str
    model_steps: int = Field(ge=1)
    tool_calls: int = Field(ge=0)
    tools_used: list[str]
    usage: list[dict[str, int | None]]
    completed_at: datetime


class IndependentReviewResult(StrictModel):
    worker_id: str
    implementation: str
    worker_version: str
    report: ReviewerReport
    usage: dict[str, int]
    event_count: int = Field(ge=0, le=4096)
    started_at: datetime
    completed_at: datetime
    duration_seconds: float


class DeveloperRunResult(StrictModel):
    worker: str
    worker_version: str
    worker_config_version: str
    base_sha: str
    worktree: str
    worker_exit_code: int | None
    worker_summary: str = Field(max_length=4096)
    usage: dict[str, int]
    nonfatal_error_items: int = Field(ge=0, le=4096)
    changed_files: list[str] = Field(max_length=1000)
    ignored_generated_files: list[dict[str, str | int]] = Field(max_length=1000)
    git_status: str = Field(max_length=1048576)
    diff_stat: str = Field(max_length=8192)
    diff: str = Field(max_length=131072)
    candidate_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    head: str | None
    head_changed: bool
    validation: list[ValidationResult] = Field(max_length=4)
    validation_passed: bool
    independent_review: IndependentReviewResult | None = None
    started_at: datetime
    worker_started_at: datetime | None
    worker_completed_at: datetime | None
    validation_completed_at: datetime | None
    completed_at: datetime
    duration_seconds: float
    error: str | None
    protocol_diagnostic: str | None
    boundary_error: str | None
    original_error: str | None
    cancelled: bool


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
    kind: Literal[
        "echo",
        "text_stats",
        "approval_demo",
        "ai_prompt",
        "tool_call",
        "agent_run",
        "self_improvement_run",
        "developer_run",
        "promotion_run",
        "merge_run",
        "release_run",
    ]
    payload: (
        TextPayload
        | AIPromptPayload
        | ToolCallPayload
        | AgentRunPayload
        | SelfImprovementPayload
        | DeveloperPayload
        | PromotionPayload
        | MergePayload
        | ReleasePayload
    )
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
    developer_spec: DeveloperRequest | None = None
    promotion_spec: PromotionRequest | None = None
    merge_spec: MergeRequest | None = None
    release_spec: ReleaseRequest | None = None


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
