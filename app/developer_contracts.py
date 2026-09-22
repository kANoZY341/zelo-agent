"""Persisted approval specification and replaceable developer worker contracts."""

from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


WorkerName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WorkerArtifactIdentity(Contract):
    """One approval-bound worker artifact, named by the adapter."""

    name: WorkerName
    path: str = Field(min_length=1, max_length=4096)
    sha256: Sha256


class WorkerCredentialLocation(Contract):
    """Credential location identity only; credential contents never enter the contract."""

    name: WorkerName
    path: str = Field(min_length=1, max_length=4096)


class WorkerIdentity(Contract):
    """Canonical worker-neutral identity persisted in developer approvals.

    v0.9 stored Codex-specific top-level fields. The before-validator accepts that
    historical representation and upgrades it to the canonical artifact/location
    form without retaining vendor-specific fields in the v0.10 model.
    """

    schema_version: Literal["zelo-worker-identity-v1"] = "zelo-worker-identity-v1"
    implementation: str = Field(min_length=1, max_length=64)
    config_version: str = Field(min_length=1, max_length=256)
    cli_version: str = Field(min_length=1, max_length=256)
    capability_profile: str = Field(min_length=1, max_length=256)
    artifacts: tuple[WorkerArtifactIdentity, ...] = Field(min_length=1, max_length=16)
    credential_locations: tuple[WorkerCredentialLocation, ...] = Field(default=(), max_length=16)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_v09_identity(cls, value):
        if not isinstance(value, dict):
            return value
        legacy = {
            "executable",
            "executable_sha256",
            "code_mode_host",
            "code_mode_host_sha256",
            "auth_location",
        }
        present = legacy.intersection(value)
        if not present:
            return value
        if present != legacy or "artifacts" in value or "credential_locations" in value:
            raise ValueError("worker_identity_legacy_invalid")
        upgraded = dict(value)
        executable = upgraded.pop("executable")
        executable_sha256 = upgraded.pop("executable_sha256")
        code_mode_host = upgraded.pop("code_mode_host")
        code_mode_host_sha256 = upgraded.pop("code_mode_host_sha256")
        auth_location = upgraded.pop("auth_location")
        upgraded["artifacts"] = (
            {
                "name": "code_mode_host",
                "path": code_mode_host,
                "sha256": code_mode_host_sha256,
            },
            {
                "name": "executable",
                "path": executable,
                "sha256": executable_sha256,
            },
        )
        upgraded["credential_locations"] = ({"name": "auth", "path": auth_location},)
        return upgraded

    @model_validator(mode="after")
    def _canonicalize(self):
        artifact_names = tuple(item.name for item in self.artifacts)
        if artifact_names != tuple(sorted(set(artifact_names))):
            raise ValueError("worker_artifacts_not_canonical")
        credential_names = tuple(item.name for item in self.credential_locations)
        if credential_names != tuple(sorted(set(credential_names))):
            raise ValueError("worker_credentials_not_canonical")
        return self

    def artifact(self, name: str) -> WorkerArtifactIdentity:
        for artifact in self.artifacts:
            if artifact.name == name:
                return artifact
        raise ValueError("worker_artifact_missing")

    def credential_location(self, name: str) -> WorkerCredentialLocation:
        for location in self.credential_locations:
            if location.name == name:
                return location
        raise ValueError("worker_credential_location_missing")


class WorkerDescriptor(Contract):
    """Trusted routing metadata with authority and concurrency enforced by v0.9."""

    worker_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    role: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    capabilities: tuple[WorkerName, ...] = Field(min_length=1, max_length=32)
    authority: tuple[WorkerName, ...] = Field(min_length=1, max_length=16)
    implementation: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    priority: int = Field(ge=0, le=1000)
    max_concurrency: int = Field(gt=0, le=32)

    @model_validator(mode="after")
    def _canonicalize(self):
        for field in ("capabilities", "authority"):
            value = getattr(self, field)
            if tuple(value) != tuple(sorted(set(value))):
                raise ValueError(f"{field}_not_canonical")
        return self


class WorkerSelectionSnapshot(Contract):
    descriptor: WorkerDescriptor
    registry_version: str = Field(min_length=1, max_length=64)
    routing_policy: str = Field(min_length=1, max_length=64)
    requested_role: WorkerName = "developer"
    requested_capabilities: tuple[WorkerName, ...] = Field(
        default=("zelo_core_development",), min_length=1, max_length=32
    )
    excluded_worker_ids: tuple[WorkerName, ...] = Field(default=(), max_length=32)
    authority_profile: str = Field(min_length=1, max_length=64)
    authority_policy_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_request_binding(self):
        capabilities = self.requested_capabilities
        if capabilities != tuple(sorted(set(capabilities))):
            raise ValueError("worker_selection_capabilities_not_canonical")
        excluded = self.excluded_worker_ids
        if excluded != tuple(sorted(set(excluded))):
            raise ValueError("worker_selection_exclusions_not_canonical")
        if (
            self.descriptor.worker_id in excluded
            or self.descriptor.role != self.requested_role
            or not set(capabilities).issubset(self.descriptor.capabilities)
        ):
            raise ValueError("worker_selection_request_mismatch")
        return self


class ReviewerBinding(Contract):
    worker: WorkerIdentity
    worker_selection: WorkerSelectionSnapshot


class ReviewFinding(Contract):
    severity: Literal["low", "medium", "high", "critical"]
    message: str = Field(min_length=1, max_length=2048)
    path: str | None = Field(default=None, max_length=4096)


class ReviewerReport(Contract):
    summary: str = Field(min_length=1, max_length=4096)
    findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=64)


class DeveloperRequest(Contract):
    instruction: str = Field(min_length=1, max_length=65536)
    source_repository: str
    workspace_root: str
    validation_identity: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    worker: WorkerIdentity
    worker_selection: WorkerSelectionSnapshot
    reviewer: ReviewerBinding | None = None
    validation_profile: Literal["zelo_core"] = "zelo_core"
    validation_version: str = "trusted-venv-content-manifest-v3"
    sandbox_version: str
    limits: Limits

class ReviewerExecutionRequest(Contract):
    instruction: str = Field(min_length=1, max_length=65536)
    source_repository: str
    workspace_root: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    worker: WorkerIdentity
    worker_selection: WorkerSelectionSnapshot
    sandbox_version: str
    limits: Limits

    @model_validator(mode="after")
    def _validate_reviewer_execution(self):
        if (
            self.worker_selection.requested_role != "reviewer"
            or self.worker_selection.descriptor.role != "reviewer"
            or self.worker_selection.authority_profile != "workspace-read-no-network-v1"
            or self.worker.implementation != self.worker_selection.descriptor.implementation
        ):
            raise ValueError("reviewer_execution_invalid")
        return self


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

    async def run(
        self, request: DeveloperRequest | ReviewerExecutionRequest, repo: Path
    ) -> WorkerResult: ...
