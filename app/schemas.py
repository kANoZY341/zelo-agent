"""Validated API and worker contracts. No executable or opaque serialized state."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.agent_execution_contracts import AgentExecutionBinding
from app.agent_factory import AgentComputerSpec, AgentSpec
from app.api_contracts import API_PATH_LIMIT, API_QUERY_LIMIT, ApiExecutionSpec
from app.browser_contracts import BrowserExecutionSpec
from app.community_review import OcrCandidateReviewEvidence, normalize_ocr_report
from app.computer_contracts import ComputerAction, ComputerExecutionSpec
from app.developer_contracts import (
    DeveloperRequest,
    ReviewerReport,
    ValidationResult,
    WorkerSelectionSnapshot,
)
from app.policy import State
from app.promotion_contracts import MergeRequest, PromotionRequest, ReleaseRequest
from app.shell_contracts import ShellExecutionSpec
from app.skill_learning import SkillLearningEvidenceRef, SkillProcedureRevision


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


class SkillLearningProposalPayload(StrictModel):
    revision: SkillProcedureRevision
    evidence_refs: tuple[SkillLearningEvidenceRef, ...] = Field(min_length=1, max_length=16)


class SkillLearningProposalRunInput(TaskInput):
    """Ask Core to persist one bounded proposal for this bound Agent."""

    kind: Literal["skill_learning_proposal_run"]
    payload: SkillLearningProposalPayload
    timeout_seconds: float = Field(default=10.0, ge=0.01, le=10, allow_inf_nan=False)


class PythonPayload(StrictModel):
    code: str = Field(min_length=1, max_length=65536)
    stdin: str = Field(default="", max_length=65536)


class PythonRunInput(TaskInput):
    kind: Literal["python_run"]
    payload: PythonPayload
    timeout_seconds: float = Field(default=10.0, ge=0.01, le=60.0, allow_inf_nan=False)


class ShellPayload(StrictModel):
    script: str = Field(min_length=1, max_length=65536, strict=True)

    @model_validator(mode="after")
    def _script_bound(self):
        if "\0" in self.script or len(self.script.encode("utf-8")) > 65536:
            raise ValueError("shell_script_invalid")
        return self


class ShellRunInput(TaskInput):
    kind: Literal["shell_run"]
    payload: ShellPayload
    timeout_seconds: float = Field(default=2.0, ge=0.01, le=2, strict=True, allow_inf_nan=False)


class ShellRunResult(StrictModel):
    stdout: str = Field(max_length=65536, strict=True)
    stderr: str = Field(max_length=16384, strict=True)
    exit_code: int = Field(strict=True)
    duration_seconds: float = Field(ge=0, strict=True, allow_inf_nan=False)
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction_trust: Literal["none"] = "none"
    completed_at: datetime

    @model_validator(mode="after")
    def _text_byte_bounds(self):
        if len(self.stdout.encode()) > 65536 or len(self.stderr.encode()) > 16384:
            raise ValueError("shell_output_limit")
        return self


class ApiPayload(StrictModel):
    endpoint_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    path: str = Field(
        min_length=1, max_length=API_PATH_LIMIT, pattern=r"^/[A-Za-z0-9/._~!$&'()*+,;=:@-]*$"
    )
    query: dict[str, str] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def _validate_request(self):
        if (
            not self.path.startswith("/")
            or "//" in self.path
            or any(character in self.path for character in "\\?#\r\n\x00")
            or any(part in {".", ".."} for part in self.path.split("/"))
        ):
            raise ValueError("api_path_invalid")
        for key, value in self.query.items():
            if (
                not 1 <= len(key) <= 128
                or len(value) > 2048
                or any(character in key + value for character in "\r\n\x00")
            ):
                raise ValueError("api_query_invalid")
        if len(str(httpx.QueryParams(self.query)).encode("ascii")) > API_QUERY_LIMIT:
            raise ValueError("api_query_too_large")
        return self


class ApiRunInput(TaskInput):
    kind: Literal["api_run"]
    payload: ApiPayload
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=30.0, allow_inf_nan=False)


class BrowserPayload(StrictModel):
    endpoint_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    path: str = Field(min_length=1, max_length=API_PATH_LIMIT)

    @model_validator(mode="after")
    def _validate_path(self):
        ApiPayload(endpoint_id=self.endpoint_id, path=self.path)
        return self


class BrowserRunInput(TaskInput):
    kind: Literal["browser_run"]
    payload: BrowserPayload
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=30, allow_inf_nan=False)


class ComputerPayload(StrictModel):
    @model_serializer(mode="wrap")
    def _preserve_legacy_request_identity(self, handler):
        data = handler(self)
        if self.parent_space_activation_id is None:
            data.pop("parent_space_activation_id", None)
        return data

    action: ComputerAction
    parent_space_activation_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    # Exact Core-approved Space confirmation; only read-only file operations may set this.
    space_activation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    endpoint_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    path: str = Field(default="", max_length=API_PATH_LIMIT)

    @model_validator(mode="after")
    def _validate_path(self):
        if self.parent_space_activation_id is not None and (
            self.space_activation_id is None
            or self.action not in ("files_read", "files_list")
            or self.parent_space_activation_id == self.space_activation_id
        ):
            raise ValueError("computer_parent_space_binding_invalid")
        if self.space_activation_id is not None and self.action not in ("files_read", "files_list"):
            raise ValueError("computer_space_activation_files_read_only")
        if self.action == "browser_navigate":
            if self.endpoint_id is None:
                raise ValueError("computer_navigation_endpoint_required")
            ApiPayload(endpoint_id=self.endpoint_id, path=self.path)
            return self

        if self.endpoint_id is not None:
            raise ValueError("computer_read_endpoint_forbidden")
        if len(self.path) > 1024:
            raise ValueError("computer_path_invalid")
        if (
            self.path.startswith("/")
            or chr(92) in self.path
            or chr(0) in self.path
            or any(part == ".." for part in self.path.split("/"))
        ):
            raise ValueError("computer_path_invalid")
        if self.action == "files_read" and not self.path:
            raise ValueError("computer_file_path_required")
        if self.action.startswith("browser_") and self.path:
            raise ValueError("computer_browser_path_forbidden")
        return self


class ComputerRunInput(TaskInput):
    kind: Literal["computer_run"]
    payload: ComputerPayload
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=30.0, allow_inf_nan=False)


class ComputerSnapshotElement(StrictModel):
    ref: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=500)
    value: str | None = Field(default=None, max_length=500)
    disabled: bool | None = None
    checked: bool | None = None


class ComputerBrowserSnapshotResult(StrictModel):
    snapshot_id: int = Field(ge=0)
    url: str = Field(max_length=2048)
    title: str = Field(max_length=512)
    elements: list[ComputerSnapshotElement] = Field(max_length=200)
    truncated: bool
    challenge_present: bool


class ComputerBrowserReadResult(StrictModel):
    url: str = Field(max_length=2048)
    title: str = Field(max_length=512)
    text: str = Field(max_length=6000)
    truncated: bool
    challenge_present: bool


class ComputerBrowserNavigateResult(StrictModel):
    endpoint_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    url: str = Field(max_length=2048)
    title: str = Field(max_length=512)
    text: str = Field(max_length=6000)
    truncated: bool
    elapsed_ms: int = Field(ge=0, le=300_000)
    challenge_present: bool


class ComputerFileEntry(StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    kind: Literal["file", "folder"]
    bytes: int | None = Field(default=None, ge=0, le=1_000_000_000_000)


class ComputerFilesListResult(StrictModel):
    path: str = Field(max_length=1024)
    entries: list[ComputerFileEntry] = Field(max_length=500)
    truncated: bool


class ComputerFilesReadResult(StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    text: str = Field(max_length=64000)
    truncated: bool
    bytes: int = Field(ge=0, le=1_000_000_000_000)


class ComputerRunResult(StrictModel):
    action: ComputerAction
    agent_instance_id: TaskId
    browser_snapshot: ComputerBrowserSnapshotResult | None = None
    browser_read: ComputerBrowserReadResult | None = None
    browser_navigate: ComputerBrowserNavigateResult | None = None
    files_list: ComputerFilesListResult | None = None
    files_read: ComputerFilesReadResult | None = None
    completed_at: datetime

    @model_validator(mode="after")
    def _one_result(self):
        expected = {
            "browser_snapshot": "browser_snapshot",
            "browser_read": "browser_read",
            "browser_navigate": "browser_navigate",
            "files_list": "files_list",
            "files_read": "files_read",
        }[self.action]
        populated = [
            field
            for field in (
                "browser_snapshot",
                "browser_read",
                "browser_navigate",
                "files_list",
                "files_read",
            )
            if getattr(self, field) is not None
        ]
        if populated != [expected]:
            raise ValueError("computer_result_shape_invalid")
        return self


class BrowserRunResult(StrictModel):
    endpoint_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    text: str = Field(max_length=65536)
    truncated: bool
    nodes: int = Field(ge=0, le=4096)
    source_origin: str = Field(max_length=512)
    source_path: str = Field(max_length=2048)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    broker_policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction_trust: Literal["none"] = "none"
    completed_at: datetime

    @model_validator(mode="after")
    def _byte_bound(self):
        if len(self.text.encode("utf-8")) > 65536:
            raise ValueError("browser_text_limit")
        return self


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


AgentExecutableTask = Annotated[
    ResearchRunInput
    | SkillLearningProposalRunInput
    | PythonRunInput
    | ShellRunInput
    | ApiRunInput
    | BrowserRunInput
    | ComputerRunInput
    | DeveloperRunInput,
    Field(discriminator="kind"),
]


class AgentTaskCreate(StrictModel):
    task: AgentExecutableTask
    delegation_id: TaskId | None = None


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
    | SkillLearningProposalRunInput
    | PythonRunInput
    | ShellRunInput
    | ApiRunInput
    | BrowserRunInput
    | ComputerRunInput
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


class SkillLearningProposalRunResult(StrictModel):
    proposal_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    proposal_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["proposed", "accepted", "rejected"]
    created: bool


class SkillLearningCandidateResponse(StrictModel):
    proposal_id: TaskId
    review_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created: bool
    runtime_authority: Literal[False] = False
    promotion_started: Literal[False] = False


class PythonRunResult(StrictModel):
    stdout: str = Field(max_length=65536)
    stderr: str = Field(max_length=16384)
    exit_code: int
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    completed_at: datetime


class ApiRunResult(StrictModel):
    endpoint_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    status_code: int = Field(ge=100, le=599)
    body: Any
    response_bytes: int = Field(ge=0, le=262144)
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
    ocr_evidence: OcrCandidateReviewEvidence | None = None

    @model_serializer(mode="wrap")
    def _legacy_result_shape(self, handler):
        value = handler(self)
        if self.ocr_evidence is None:
            value.pop("ocr_evidence", None)
        return value

    @model_validator(mode="after")
    def _ocr_result_matches_candidate(self):
        if self.ocr_evidence is not None:
            expected = normalize_ocr_report(
                self.report.summary, self.ocr_evidence.line_evidence.comments
            )
            if (
                self.ocr_evidence.candidate_content_sha256 != self.candidate_content_sha256
                or self.ocr_evidence.summary_sha256
                != sha256(self.report.summary.encode("utf-8")).hexdigest()
                or self.report != expected
            ):
                raise ValueError("ocr_review_result_identity_mismatch")
        return self


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

    @model_validator(mode="after")
    def _ocr_candidate_matches_result(self):
        if (
            self.independent_review is not None
            and self.independent_review.ocr_evidence is not None
            and (
                self.independent_review.candidate_content_sha256 != self.candidate_content_sha256
                or self.independent_review.ocr_evidence.line_evidence.base_sha != self.base_sha
            )
        ):
            raise ValueError("ocr_developer_result_identity_mismatch")
        return self


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
        "python_run",
        "shell_run",
        "api_run",
        "browser_run",
        "computer_run",
        "self_improvement_run",
        "developer_run",
        "promotion_run",
        "merge_run",
        "release_run",
        "skill_learning_proposal_run",
    ]
    payload: (
        TextPayload
        | AIPromptPayload
        | ToolCallPayload
        | AgentRunPayload
        | ResearchPayload
        | SkillLearningProposalPayload
        | PythonPayload
        | ShellPayload
        | ApiPayload
        | BrowserPayload
        | ComputerPayload
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
    research_spec: WorkerSelectionSnapshot | None = None
    python_spec: WorkerSelectionSnapshot | None = None
    api_spec: ApiExecutionSpec | None = None
    browser_spec: BrowserExecutionSpec | None = None
    computer_spec: ComputerExecutionSpec | None = None
    shell_spec: ShellExecutionSpec | None = None
    promotion_spec: PromotionRequest | None = None
    merge_spec: MergeRequest | None = None
    release_spec: ReleaseRequest | None = None
    agent_execution_spec: AgentExecutionBinding | None = None


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
        "python_run",
        "shell_run",
        "api_run",
        "browser_run",
        "computer_run",
        "self_improvement_run",
        "developer_run",
        "promotion_run",
        "merge_run",
        "release_run",
        "skill_learning_proposal_run",
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


AgentInstanceStatus = Literal["active", "paused", "retired"]
AgentInstanceEventName = Literal["created", "paused", "resumed", "retired"]
AgentInstanceTransitionAction = Literal["pause", "resume", "retire"]


class AgentInstanceCreate(StrictModel):
    agent_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    project_id: TaskId


class AgentInstanceTransition(StrictModel):
    action: AgentInstanceTransitionAction


class AgentInstanceResponse(StrictModel):
    id: TaskId
    agent_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    agent_version: int = Field(ge=1)
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_id: TaskId
    spec: AgentSpec
    status: AgentInstanceStatus
    created_at: datetime
    updated_at: datetime


class AgentInstancePage(StrictModel):
    items: list[AgentInstanceResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class AgentLearnedSkillItem(StrictModel):
    proposal_id: TaskId
    source_skill_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    source_skill_version: int = Field(ge=1)
    procedure_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    proposed_version: int = Field(ge=1)
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_revision_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["proposed", "accepted", "rejected"]
    evidence_count: int = Field(ge=1, le=16)
    review_id: TaskId | None = None
    review_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    promoted: bool = False
    promoted_revision_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    promotion_merge_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    active_revision_sha256: Literal[None] = None
    runtime_authority: Literal[False] = False
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _promotion_proof_shape(self):
        proof = (self.promoted_revision_sha256, self.promotion_merge_commit_sha)
        if self.promoted and any(value is None for value in proof):
            raise ValueError("learned_skill_promotion_proof_incomplete")
        if not self.promoted and any(value is not None for value in proof):
            raise ValueError("learned_skill_promotion_proof_without_status")
        return self


class AgentLearnedSkillPage(StrictModel):
    items: list[AgentLearnedSkillItem]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class AgentComputerPermissionPatch(StrictModel):
    enabled: bool | None = None
    browser: bool | None = None
    files: bool | None = None
    shell: bool | None = None

    @model_validator(mode="after")
    def _require_change(self):
        if all(value is None for value in (self.enabled, self.browser, self.files, self.shell)):
            raise ValueError("computer_permission_patch_empty")
        return self


class AgentComputerPermissionsResponse(StrictModel):
    enabled: bool
    browser: bool
    files: bool
    shell: bool
    updated_at: datetime


class AgentComputerControlResponse(StrictModel):
    holder: Literal["bot", "human"]
    requested: bool
    transitioning: bool
    resume_snapshot_required: bool
    request_id: str | None = None
    request_status: str | None = None


class AgentComputerStatusResponse(StrictModel):
    agent_instance_id: TaskId
    configured: bool
    state: Literal["not_configured", "stopped", "running", "unavailable"]
    spec: AgentComputerSpec
    permissions: AgentComputerPermissionsResponse
    control: AgentComputerControlResponse | None = None
    error: str | None = Field(default=None, max_length=256)


class AgentComputerEventResponse(StrictModel):
    id: TaskId
    agent_instance_id: TaskId
    sequence: int = Field(ge=1)
    event: str = Field(min_length=1, max_length=64)
    actor: Literal["owner", "system"]
    outcome: Literal["succeeded", "failed"]
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentComputerEventPage(StrictModel):
    items: list[AgentComputerEventResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class AgentInstanceEventResponse(StrictModel):
    id: TaskId
    agent_instance_id: TaskId
    sequence: int = Field(ge=1)
    event: AgentInstanceEventName
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentInstanceEventPage(StrictModel):
    items: list[AgentInstanceEventResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


AgentDelegationStatus = Literal["active", "revoked"]


class AgentDelegationCreate(StrictModel):
    child_instance_id: TaskId
    campaign_id: TaskId
    budget_reservation_id: TaskId
    idempotency_key: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=2048)
    allowed_task_kinds: list[str] = Field(min_length=1, max_length=16)
    required_capabilities: list[str] = Field(min_length=1, max_length=32)
    max_depth: int = Field(default=1, ge=1, le=4)
    budget_resource: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.:-]{0,63}$",
    )
    budget_units: int = Field(ge=1, le=1_000_000_000_000_000)

    @model_validator(mode="after")
    def validate_canonical_lists(self) -> "AgentDelegationCreate":
        for field in ("allowed_task_kinds", "required_capabilities"):
            value = getattr(self, field)
            if value != sorted(set(value)):
                raise ValueError(f"{field} must be sorted and unique")
        if not self.objective.strip():
            raise ValueError("objective cannot be blank")
        return self


class AgentDelegationRevoke(StrictModel):
    reason: str = Field(min_length=1, max_length=512)


class AgentDelegationResponse(StrictModel):
    id: TaskId
    parent_instance_id: TaskId
    child_instance_id: TaskId
    campaign_id: TaskId
    budget_reservation_id: TaskId
    idempotency_key: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective: str = Field(min_length=1, max_length=2048)
    allowed_task_kinds: list[str] = Field(min_length=1, max_length=16)
    required_capabilities: list[str] = Field(min_length=1, max_length=32)
    depth: int = Field(ge=1, le=4)
    max_depth: int = Field(ge=1, le=4)
    budget_resource: str = Field(min_length=1, max_length=64)
    budget_units: int = Field(ge=1)
    status: AgentDelegationStatus
    created_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = Field(default=None, max_length=512)


class AgentDelegationPage(StrictModel):
    items: list[AgentDelegationResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class AgentChildCreate(StrictModel):
    child_agent_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    unit_id: TaskId
    campaign_id: TaskId
    budget_reservation_id: TaskId
    idempotency_key: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=2048)
    allowed_task_kinds: list[str] = Field(min_length=1, max_length=16)
    required_capabilities: list[str] = Field(min_length=1, max_length=32)
    max_depth: int = Field(default=4, ge=1, le=4)
    budget_resource: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.:-]{0,63}$",
    )
    budget_units: int = Field(ge=1, le=1_000_000_000_000_000)

    @model_validator(mode="after")
    def validate_child_creation(self) -> "AgentChildCreate":
        for field in ("allowed_task_kinds", "required_capabilities"):
            value = getattr(self, field)
            if value != sorted(set(value)):
                raise ValueError(f"{field} must be sorted and unique")
        if not self.objective.strip():
            raise ValueError("objective cannot be blank")
        return self


OrganizationUnitType = Literal["division", "department", "team"]
OrganizationUnitStatus = Literal["active", "retired"]
OrganizationLevel = Literal["vp", "director", "manager", "specialist"]
OrganizationAssignmentStatus = Literal["active", "ended"]


class OrganizationUnitCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    unit_type: OrganizationUnitType
    parent_unit_id: TaskId | None = None


class OrganizationUnitResponse(StrictModel):
    id: TaskId
    name: str = Field(min_length=1, max_length=160)
    unit_type: OrganizationUnitType
    parent_unit_id: TaskId | None = None
    status: OrganizationUnitStatus
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None = None


class OrganizationUnitPage(StrictModel):
    items: list[OrganizationUnitResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class OrganizationAssignmentCreate(StrictModel):
    agent_instance_id: TaskId
    unit_id: TaskId
    level: OrganizationLevel
    reports_to_assignment_id: TaskId | None = None


class OrganizationAssignmentEnd(StrictModel):
    reason: str = Field(min_length=1, max_length=512)


class OrganizationAssignmentResponse(StrictModel):
    id: TaskId
    agent_instance_id: TaskId
    unit_id: TaskId
    level: OrganizationLevel
    reports_to_assignment_id: TaskId | None = None
    reports_to_principal: Literal["zelo_ceo"] | None = None
    status: OrganizationAssignmentStatus
    created_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = Field(default=None, max_length=512)


class OrganizationAssignmentPage(StrictModel):
    items: list[OrganizationAssignmentResponse]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class AgentChildCreationResponse(StrictModel):
    id: TaskId
    parent_instance_id: TaskId
    parent_assignment_id: TaskId
    child_instance: AgentInstanceResponse
    child_assignment: OrganizationAssignmentResponse
    delegation: AgentDelegationResponse
    idempotency_key: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class OrganizationPrincipal(StrictModel):
    id: Literal["human_managing_director", "zelo_ceo"]
    role: Literal["managing_director", "ceo"]
    kind: Literal["human", "system"]
    reports_to: Literal["human_managing_director"] | None = None


class OrganizationSnapshot(StrictModel):
    managing_director: OrganizationPrincipal
    ceo: OrganizationPrincipal
    units: list[OrganizationUnitResponse]
    assignments: list[OrganizationAssignmentResponse]


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


class AgentMemoryContextRequest(StrictModel):
    query: str = Field(min_length=1, max_length=2048)
    limit: int = Field(default=6, ge=1, le=20)


class AgentMemoryContextResponse(StrictModel):
    instance: AgentInstanceResponse
    scopes: list[MemoryScope] = Field(min_length=2, max_length=2)
    retrieval: MemoryRetrievalResponse


class ModelPerformanceEvidence(StrictModel):
    source_kind: Literal["mind_turn", "task_attempt"]
    source_id: str = Field(min_length=1, max_length=128)
    task_id: TaskId | None = None
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
    other_outcomes: int = Field(ge=0)
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
    worker_config_version: str | None = Field(default=None, max_length=256)
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
    other_outcomes: int = Field(ge=0)
    usage_observations: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_creation_input_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
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
    returned_model_observations: int = Field(ge=0)
    returned_worker_observations: int = Field(ge=0)


class PerformanceCostEvidence(StrictModel):
    schema: Literal["zelo-performance-evidence-v1"]
    generated_at: datetime
    record_limit: int = Field(ge=1, le=500)
    summary_scope: Literal["returned_evidence_only"] = "returned_evidence_only"
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
