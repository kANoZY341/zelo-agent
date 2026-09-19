"""Persisted approval specification and replaceable developer worker contracts."""

from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Limits(Contract):
    overall_seconds: float = Field(gt=0, le=3600)
    worker_seconds: float = Field(gt=0, le=1800)
    validation_seconds: float = Field(gt=0, le=1800)
    git_seconds: float = Field(gt=0, le=60)
    stdout_bytes: int = Field(gt=0, le=1048576)
    stderr_bytes: int = Field(gt=0, le=65536)
    line_bytes: int = Field(gt=0, le=65536)
    events: int = Field(gt=0, le=4096)
    diff_bytes: int = Field(gt=0, le=131072)
    stat_bytes: int = 8192
    changed_files: int = Field(gt=0, le=1000)


class WorkerIdentity(Contract):
    implementation: str
    config_version: str
    cli_version: str
    capability_profile: str
    executable: str
    executable_sha256: str
    code_mode_host: str
    code_mode_host_sha256: str
    auth_location: str


class DeveloperRequest(Contract):
    instruction: str = Field(min_length=1, max_length=65536)
    source_repository: str
    workspace_root: str
    validation_identity: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    worker: WorkerIdentity
    validation_profile: Literal["zelo_core"] = "zelo_core"
    validation_version: str = "trusted-venv-content-manifest-v3"
    sandbox_version: str
    limits: Limits


class WorkerResult(Contract):
    exit_code: int
    summary: str = Field(max_length=4096)
    event_count: int
    usage: dict[str, int] = Field(default_factory=dict)
    nonfatal_error_items: int = Field(default=0, ge=0, le=4096)
    started_at: datetime
    completed_at: datetime
    duration_seconds: float


class ValidationResult(Contract):
    identifier: str
    exit_code: int
    output: str = Field(max_length=32768)
    duration_seconds: float
    completed_at: datetime
    error: str | None = None


class DeveloperWorker(Protocol):
    def identity(self) -> WorkerIdentity: ...

    def bind(self, approved: WorkerIdentity) -> AbstractContextManager["DeveloperWorker"]: ...

    async def preflight(self) -> WorkerIdentity: ...

    async def run(self, request: DeveloperRequest, repo: Path) -> WorkerResult: ...
