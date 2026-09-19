"""Frozen contracts for reviewed developer output and future promotion actions."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.developer_contracts import ValidationResult, WorkerIdentity

SHA256 = r"^[0-9a-f]{64}$"
GIT_SHA = r"^[0-9a-f]{40}$"


class PromotionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateEntry(PromotionContract):
    path: str = Field(min_length=1, max_length=4096)
    operation: Literal["add", "modify", "delete"]
    object_type: Literal["file", "symlink"]
    mode: Literal["100644", "100755", "120000"]
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=SHA256)

    @model_validator(mode="after")
    def validate_presence(self):
        if self.object_type == "symlink" and self.mode != "120000":
            raise ValueError("Symlinks require Git mode 120000")
        if self.object_type == "file" and self.mode == "120000":
            raise ValueError("Regular files cannot use symlink mode")
        if self.operation == "delete":
            if self.size is not None or self.sha256 is not None:
                raise ValueError("Deleted entries cannot carry present-object data")
        elif self.size is None or self.sha256 is None:
            raise ValueError("Present entries require size and digest")
        return self


class IgnoredArtifactSummary(PromotionContract):
    path: str = Field(min_length=1, max_length=4096)
    object_type: Literal["file", "symlink", "special"]
    size: int = Field(ge=0)


class PromotionReview(PromotionContract):
    review_version: Literal["promotion-review-v1"] = "promotion-review-v1"
    developer_task_id: str = Field(min_length=36, max_length=36)
    developer_attempt_id: str = Field(min_length=36, max_length=36)
    source_repository: str = Field(min_length=1, max_length=4096)
    workspace: str = Field(min_length=1, max_length=4096)
    base_sha: str = Field(pattern=GIT_SHA)
    head_sha: str = Field(pattern=GIT_SHA)
    worker_identity: WorkerIdentity
    validation_identity: str = Field(pattern=SHA256)
    validation_profile: Literal["zelo_core"]
    validation_version: str = Field(min_length=1, max_length=128)
    sandbox_version: str = Field(min_length=1, max_length=128)
    validation_passed: Literal[True]
    changed_file_count: int = Field(ge=1, le=1000)
    candidate_manifest: list[CandidateEntry] = Field(min_length=1, max_length=1000)
    ignored_artifact_summary: list[IgnoredArtifactSummary] = Field(
        default_factory=list, max_length=1000
    )
    review_fingerprint: str = Field(pattern=SHA256)

    @model_validator(mode="after")
    def validate_count(self):
        if self.changed_file_count != len(self.candidate_manifest):
            raise ValueError("changed_file_count does not match candidate_manifest")
        return self


class PromotionPublisherIdentity(PromotionContract):
    gateway_version: Literal["github-promotion-v1"]
    repository: str = Field(min_length=3, max_length=256, pattern=r"^[^/\s]+/[^/\s]+$")
    host: Literal["github.com"] = "github.com"
    gh_executable: str = Field(min_length=1, max_length=4096)
    gh_executable_sha256: str = Field(pattern=SHA256)
    gh_config_dir: str = Field(min_length=1, max_length=4096)
    timeout_seconds: float = Field(ge=1.0, le=120.0, allow_inf_nan=False)


class PromotionRequest(PromotionContract):
    """Approval-bound external action specification."""

    review: PromotionReview
    publisher: PromotionPublisherIdentity
    repository: str = Field(min_length=3, max_length=256, pattern=r"^[^/\s]+/[^/\s]+$")
    target_base_branch: Literal["main"] = "main"
    branch: str = Field(
        min_length=16,
        max_length=96,
        pattern=r"^zelo/promotion/[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    commit_title: str = Field(min_length=1, max_length=200)
    commit_message: str = Field(default="", max_length=4096)
    commit_author_name: str = Field(min_length=1, max_length=128)
    commit_author_email: str = Field(min_length=3, max_length=254)
    commit_timestamp: datetime
    pr_title: str = Field(min_length=1, max_length=256)
    pr_body: str = Field(default="", max_length=16384)

    @field_validator(
        "commit_title",
        "commit_message",
        "commit_author_name",
        "commit_author_email",
        "pr_title",
        "pr_body",
    )
    @classmethod
    def reject_nul_and_cr(cls, value: str) -> str:
        if "\x00" in value or "\r" in value:
            raise ValueError("Promotion metadata contains forbidden control characters")
        return value

    @field_validator("commit_author_name")
    @classmethod
    def validate_author_name(cls, value: str) -> str:
        if "\n" in value or "<" in value or ">" in value:
            raise ValueError("Invalid promotion commit author name")
        return value

    @field_validator("commit_author_email")
    @classmethod
    def validate_author_email(cls, value: str) -> str:
        if (
            "\n" in value
            or value.count("@") != 1
            or any(character.isspace() for character in value)
            or "<" in value
            or ">" in value
        ):
            raise ValueError("Invalid promotion commit author email")
        return value

    @field_validator("commit_timestamp")
    @classmethod
    def validate_commit_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Promotion commit timestamp must be timezone-aware")
        return value.astimezone(UTC).replace(microsecond=0)


class PromotionCommitResult(PromotionContract):
    base_sha: str = Field(pattern=GIT_SHA)
    tree_sha: str = Field(pattern=GIT_SHA)
    commit_sha: str = Field(pattern=GIT_SHA)
    branch: str = Field(
        min_length=16,
        max_length=96,
        pattern=r"^zelo/promotion/[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    review_fingerprint: str = Field(pattern=SHA256)
    validation: list[ValidationResult] = Field(min_length=4, max_length=4)


class PromotionPublishResult(PromotionContract):
    repository: str = Field(min_length=3, max_length=256, pattern=r"^[^/\s]+/[^/\s]+$")
    base_branch: Literal["main"]
    base_sha: str = Field(pattern=GIT_SHA)
    branch: str = Field(
        min_length=16,
        max_length=96,
        pattern=r"^zelo/promotion/[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    commit_sha: str = Field(pattern=GIT_SHA)
    pr_number: int = Field(ge=1)
    pr_url: str = Field(min_length=1, max_length=2048)
    pr_head_sha: str = Field(pattern=GIT_SHA)
    pr_base_sha: str = Field(pattern=GIT_SHA)
