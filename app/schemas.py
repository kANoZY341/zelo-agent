"""Validated API and worker contracts. No executable or opaque serialized state."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.developer_contracts import DeveloperRequest, ReviewerReport, ValidationResult
from app.policy import State
from app.promotion_contracts import MergeRequest, PromotionRequest, ReleaseRequest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TextPayload(StrictModel):
    text: str = Field(max_length=65536)


TaskId = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class TaskInput(StrictModel):
    payload: TextPayload
    timeout_seconds: float = Field(default=10.0, ge=0.01, le=60, allow_inf_nan=False)
    campaign_id: TaskId | None = None
    depends_on_task_ids: list[TaskId] = Field(default_factory=list, max_length=32)


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


class ResearchPayload(StrictModel):
    query: str = Field(min_length=1, max_length=16384)


class ResearchRunInput(TaskInput):
    kind: Literal["research_run"]
    payload: ResearchPayload
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
    | ResearchRunInput
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


class ResearchSource(StrictModel):
    id: str = Field(pattern=r"^S[1-9][0-9]*$")
    tool: Literal["repo_status", "list_workspace_files", "read_workspace_file"]
    locator: str = Field(min_length=1, max_length=522)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool


class ResearchRunResult(StrictModel):
    answer: str = Field(min_length=1, max_length=16384)
    sources: list[ResearchSource] = Field(min_length=1, max_length=16)
    alias: Literal["local_general"]
    actual_model: str
    model_steps: int = Field(ge=1)
    tool_calls: int = Field(ge=1)
    usage: list[dict[str, int | None]]
    completed_at: datetime


class IndependentReviewResult(StrictModel):
    worker_id: str
    implementation: str
    worker_version: str
    candidate_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    campaign_id: TaskId | None = None
    depends_on_task_ids: list[TaskId] = Field(default_factory=list, max_length=32)
    kind: Literal[
        "echo",
        "text_stats",
        "approval_demo",
        "ai_prompt",
        "tool_call",
        "agent_run",
        "research_run",
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
        | ResearchPayload
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


class TaskProjection(StrictModel):
    id: str
    campaign_id: TaskId | None = None
    depends_on_task_ids: list[TaskId] = Field(default_factory=list, max_length=32)
    kind: Literal[
        "echo",
        "text_stats",
        "approval_demo",
        "ai_prompt",
        "tool_call",
        "agent_run",
        "research_run",
        "self_improvement_run",
        "developer_run",
        "promotion_run",
        "merge_run",
        "release_run",
    ]
    status: State
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool
    result: dict[str, Any] | None
    result_truncated: bool = False
    error: str | None
    attempt_count: int = Field(ge=0)
    latest_attempt_outcome: State | None = None
    approval_count: int = Field(ge=0)
    latest_approval_id: str | None = None
    latest_approval_status: (
        Literal["pending", "approved", "rejected", "expired", "invalidated", "cancelled"] | None
    ) = None
    selected_alias: str | None = None
    selected_model: str | None = None


ProjectStatus = Literal["active", "paused", "completed", "archived"]
GoalStatus = Literal["active", "completed", "cancelled"]
GoalText = Annotated[str, Field(min_length=1, max_length=512)]


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=4096)


class ProjectResponse(StrictModel):
    id: str
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(max_length=4096)
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectPage(StrictModel):
    items: list[ProjectResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


MemoryScopeKind = Literal["personal", "project", "agent"]
MemoryKind = Literal["fact", "preference", "decision", "experience"]
MemoryProvenanceKind = Literal[
    "operator",
    "conversation_turn",
    "task",
    "project",
    "agent",
    "import",
]
MemoryReplacementKind = Literal["correction", "supersession"]
MemoryStatus = Literal["active", "expired", "corrected", "superseded"]


class MemoryScope(StrictModel):
    kind: MemoryScopeKind
    id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_shape(self) -> "MemoryScope":
        if self.kind == "personal" and self.id is not None:
            raise ValueError("personal memory scope cannot set id")
        if self.kind != "personal" and self.id is None:
            raise ValueError(f"{self.kind} memory scope requires id")
        return self


class MemoryProvenance(StrictModel):
    kind: MemoryProvenanceKind
    ref: str | None = Field(default=None, min_length=1, max_length=256)
    note: str = Field(default="", max_length=1024)


class MemoryCreate(StrictModel):
    scope: MemoryScope
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=8192)
    provenance: MemoryProvenance
    expires_at: datetime | None = None
    replaces_id: TaskId | None = None
    replacement_kind: MemoryReplacementKind | None = None

    @model_validator(mode="after")
    def validate_replacement(self) -> "MemoryCreate":
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must include a timezone")
        if (self.replaces_id is None) != (self.replacement_kind is None):
            raise ValueError("replaces_id and replacement_kind must be set together")
        return self


class MemoryResponse(StrictModel):
    id: TaskId
    scope: MemoryScope
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=8192)
    provenance: MemoryProvenance
    expires_at: datetime | None = None
    replaces_id: TaskId | None = None
    replacement_kind: MemoryReplacementKind | None = None
    replaced_by_id: TaskId | None = None
    status: MemoryStatus
    created_at: datetime


class MemoryPage(StrictModel):
    items: list[MemoryResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class MemoryRetrieveRequest(StrictModel):
    query: str = Field(min_length=1, max_length=2048)
    scopes: list[MemoryScope] = Field(
        default_factory=lambda: [MemoryScope(kind="personal")],
        min_length=1,
        max_length=8,
    )
    limit: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def validate_scopes(self) -> "MemoryRetrieveRequest":
        keys = [
            (scope.kind, scope.id.strip() if scope.id is not None else None)
            for scope in self.scopes
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("memory retrieval scopes must be unique")
        return self


class MemoryHit(StrictModel):
    id: TaskId
    scope: MemoryScope
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=1400)
    content_truncated: bool
    provenance: MemoryProvenance
    score: int = Field(ge=1)
    matched_terms: list[str] = Field(max_length=32)
    created_at: datetime


class MemoryRetrievalResponse(StrictModel):
    query: str = Field(min_length=1, max_length=2048)
    items: list[MemoryHit]
    candidate_count: int = Field(ge=0)
    limit: int = Field(ge=1, le=20)
    retrieval: Literal["bounded_lexical_v1"] = "bounded_lexical_v1"


class ModelPerformanceEvidence(StrictModel):
    source_kind: Literal["mind_turn", "task_attempt"]
    source_id: str = Field(min_length=1, max_length=128)
    task_kind: str | None = Field(default=None, max_length=32)
    attempt_number: int | None = Field(default=None, ge=1)
    alias: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=256)
    outcome: str = Field(min_length=1, max_length=32)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_status: Literal["reported", "partial", "unreported"]
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    observed_at: datetime


class ModelPerformanceSummary(StrictModel):
    alias: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=256)
    observations: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    usage_observations: int = Field(ge=0)
    duration_observations: int = Field(ge=0)
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)


class WorkerPerformanceEvidence(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    task_id: TaskId
    attempt_number: int = Field(ge=1)
    role: Literal["developer", "reviewer"]
    worker: str = Field(min_length=1, max_length=128)
    worker_version: str = Field(min_length=1, max_length=256)
    worker_config_version: str | None = Field(default=None, max_length=128)
    outcome: str = Field(min_length=1, max_length=32)
    usage: dict[str, int] = Field(default_factory=dict)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    validation_passed: bool | None = None
    observed_at: datetime


class WorkerPerformanceSummary(StrictModel):
    role: Literal["developer", "reviewer"]
    worker: str = Field(min_length=1, max_length=128)
    worker_version: str = Field(min_length=1, max_length=256)
    observations: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    usage_observations: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    duration_observations: int = Field(ge=0)
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    validation_passes: int = Field(ge=0)
    validation_failures: int = Field(ge=0)


class ReportedCostEvidence(StrictModel):
    status: Literal["not_reported_by_current_sources"]
    currency: str | None = Field(default=None, max_length=16)
    amount_micros: int | None = Field(default=None, ge=0)
    note: str = Field(min_length=1, max_length=512)


class PerformanceTotals(StrictModel):
    model_observations: int = Field(ge=0)
    worker_observations: int = Field(ge=0)


class PerformanceCostEvidence(StrictModel):
    schema: Literal["zelo-performance-evidence-v1"]
    generated_at: datetime
    record_limit: int = Field(ge=1, le=500)
    totals: PerformanceTotals
    model_evidence: list[ModelPerformanceEvidence]
    model_summaries: list[ModelPerformanceSummary]
    worker_evidence: list[WorkerPerformanceEvidence]
    worker_summaries: list[WorkerPerformanceSummary]
    reported_cost: ReportedCostEvidence


class GoalSpec(StrictModel):
    objective: str = Field(min_length=1, max_length=2048)
    success_criteria: list[GoalText] = Field(default_factory=list, max_length=16)
    constraints: list[GoalText] = Field(default_factory=list, max_length=16)


class GoalCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    spec: GoalSpec


class GoalResponse(StrictModel):
    id: str
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    spec: GoalSpec
    status: GoalStatus
    created_at: datetime
    updated_at: datetime


class GoalPage(StrictModel):
    items: list[GoalResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


CampaignStatus = Literal["active", "paused", "completed", "cancelled"]


class CampaignCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4096)


class CampaignResponse(StrictModel):
    id: str
    goal_id: str
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(max_length=4096)
    status: CampaignStatus
    task_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class CampaignPage(StrictModel):
    items: list[CampaignResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CampaignEventResponse(StrictModel):
    id: str
    campaign_id: str
    sequence: int = Field(ge=1)
    task_id: TaskId | None = None
    event: str = Field(min_length=1, max_length=64)
    created_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class CampaignEventPage(StrictModel):
    items: list[CampaignEventResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CampaignCheckpointCreate(StrictModel):
    state: dict[str, Any]


class CampaignCheckpointResponse(StrictModel):
    id: str
    campaign_id: str
    version: int = Field(ge=1)
    state: dict[str, Any]
    created_at: datetime


class CampaignCheckpointPage(StrictModel):
    items: list[CampaignCheckpointResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


ControlName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.:-]{0,63}$"),
]
ControlOwner = Annotated[str, Field(min_length=1, max_length=128)]
ControlUnits = Annotated[int, Field(ge=0, le=1_000_000_000_000_000)]


class CampaignLeaseAcquire(StrictModel):
    holder_id: ControlOwner
    ttl_seconds: int = Field(default=60, ge=1, le=86400)


class CampaignLeaseRenew(StrictModel):
    holder_id: ControlOwner
    lease_token: TaskId
    generation: int = Field(ge=1)
    ttl_seconds: int = Field(default=60, ge=1, le=86400)


class CampaignLeaseRelease(StrictModel):
    holder_id: ControlOwner
    lease_token: TaskId
    generation: int = Field(ge=1)


class CampaignLeaseGrantResponse(StrictModel):
    campaign_id: TaskId
    scope: ControlName
    generation: int = Field(ge=1)
    holder_id: ControlOwner
    lease_token: TaskId
    acquired_at: datetime
    expires_at: datetime
    updated_at: datetime


class CampaignLeaseStatusResponse(StrictModel):
    campaign_id: TaskId
    scope: ControlName
    generation: int = Field(ge=0)
    status: Literal["free", "held", "expired"]
    holder_id: ControlOwner | None = None
    acquired_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime


class CampaignBudgetSet(StrictModel):
    limit_units: ControlUnits


class CampaignBudgetResponse(StrictModel):
    campaign_id: TaskId
    resource: ControlName
    limit_units: ControlUnits
    spent_units: ControlUnits
    reserved_units: ControlUnits
    available_units: ControlUnits
    updated_at: datetime


class CampaignBudgetPage(StrictModel):
    items: list[CampaignBudgetResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CampaignBudgetReserve(StrictModel):
    resource: ControlName
    owner_id: ControlOwner
    idempotency_key: str = Field(min_length=1, max_length=128)
    units: int = Field(ge=1, le=1_000_000_000_000_000)
    ttl_seconds: int = Field(default=3600, ge=1, le=86400)


class CampaignBudgetCommit(StrictModel):
    actual_units: ControlUnits


class CampaignBudgetReservationResponse(StrictModel):
    id: TaskId
    campaign_id: TaskId
    resource: ControlName
    owner_id: ControlOwner
    units: int = Field(ge=1)
    committed_units: ControlUnits
    status: Literal["reserved", "committed", "released", "expired"]
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class CampaignBudgetReservationPage(StrictModel):
    items: list[CampaignBudgetReservationResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CampaignRecoveryResponse(StrictModel):
    campaign_id: TaskId | None = None
    recovered_leases: int = Field(ge=0)
    expired_budget_reservations: int = Field(ge=0)


CampaignScheduleStatus = Literal["active", "completed", "cancelled"]
CampaignScheduleMode = Literal["cadence", "continuation"]
CampaignScheduleRunStatus = Literal["running", "succeeded", "failed", "interrupted"]


class CampaignScheduleCreate(StrictModel):
    conversation_id: TaskId
    prompt: str = Field(min_length=1, max_length=16384)
    run_at: datetime
    interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    max_runs: int = Field(default=1, ge=1, le=100)
    mode: CampaignScheduleMode = "cadence"

    @model_validator(mode="after")
    def validate_cadence(self) -> "CampaignScheduleCreate":
        if self.run_at.tzinfo is None or self.run_at.utcoffset() is None:
            raise ValueError("run_at must include a timezone")
        if self.max_runs == 1 and self.interval_seconds is not None:
            raise ValueError("one-shot schedules cannot set interval_seconds")
        if self.max_runs > 1 and self.interval_seconds is None:
            raise ValueError("recurring schedules require interval_seconds")
        if self.mode == "continuation" and self.max_runs == 1:
            raise ValueError("continuation schedules require max_runs greater than 1")
        return self


class CampaignScheduleResponse(StrictModel):
    id: TaskId
    campaign_id: TaskId
    conversation_id: TaskId
    prompt: str = Field(min_length=1, max_length=16384)
    status: CampaignScheduleStatus
    mode: CampaignScheduleMode = "cadence"
    next_run_at: datetime | None = None
    interval_seconds: int | None = None
    max_runs: int = Field(ge=1, le=100)
    run_count: int = Field(ge=0, le=100)
    last_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class CampaignSchedulePage(StrictModel):
    items: list[CampaignScheduleResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class CampaignScheduleRunResponse(StrictModel):
    id: TaskId
    schedule_id: TaskId
    sequence: int = Field(ge=1)
    status: CampaignScheduleRunStatus
    lease_holder_id: str = Field(min_length=1, max_length=128)
    lease_generation: int = Field(ge=1)
    budget_reservation_id: TaskId | None = None
    conversation_turn_id: TaskId | None = None
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class CampaignScheduleRunPage(StrictModel):
    items: list[CampaignScheduleRunResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class ConversationCreate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)


class ConversationTurnCreate(StrictModel):
    text: str = Field(min_length=1, max_length=16384)


class ConversationTurnResponse(StrictModel):
    id: str
    number: int = Field(ge=1)
    user_content: str = Field(min_length=1, max_length=16384)
    assistant_content: str = Field(min_length=1, max_length=16384)
    selected_alias: str
    selected_model: str
    usage: dict[str, int | None] = Field(default_factory=dict)
    core_task: TaskProjection | None = None
    created_at: datetime


class ConversationResponse(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=120)
    created_at: datetime
    updated_at: datetime
    turns: list[ConversationTurnResponse] = Field(default_factory=list)
    turn_limit: int = Field(ge=1, le=100)
    turn_offset: int = Field(ge=0)
    turn_total: int = Field(ge=0)


class ConversationSummary(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=120)
    created_at: datetime
    updated_at: datetime
    turn_count: int = Field(ge=0)


class ConversationPage(StrictModel):
    items: list[ConversationSummary]
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
