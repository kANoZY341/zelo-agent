"""Bounded self-inspection and proposal contracts for Zelo self-improvement."""

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import __version__
from app.config import ROOT
from app.developer_process import DeveloperError, run_sync
from app.developer_sandbox import git_argv

INSPECTION_SCHEMA = "zelo-self-inspection-v1"
PROPOSAL_SCHEMA = "zelo-improvement-proposal-v1"
EVALUATION_SCHEMA = "zelo-improvement-evaluation-v1"

Sha1 = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CapabilityName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]

_FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "runtime",
        "secrets",
        "workspaces",
    }
)
_FORBIDDEN_NAMES = frozenset({".env", "auth.json"})
_TEXT_SUFFIXES = frozenset({".ini", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"})
_TEXT_ROOTS = frozenset({"app", "docs", "migrations", "scripts", "tests"})
_TEXT_TOP_LEVEL = frozenset(
    {
        ".env.example",
        "AGENTS.md",
        "README.md",
        "alembic.ini",
        "pyproject.toml",
        "requirements-dev.txt",
        "requirements.txt",
    }
)


class SelfImprovementError(Exception):
    """Only fixed self-improvement diagnostic codes cross runtime boundaries."""


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _repo_path(value: str) -> str:
    if (
        not value
        or len(value) > 4096
        or "\\" in value
        or "\x00" in value
        or any(character in value for character in "\r\n")
    ):
        raise ValueError("repository_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or "." in path.parts or ".." in path.parts:
        raise ValueError("repository_path_invalid")
    if any(part in _FORBIDDEN_PARTS for part in path.parts) or path.name in _FORBIDDEN_NAMES:
        raise ValueError("repository_path_forbidden")
    return value


class InspectedFile(Contract):
    path: str = Field(min_length=1, max_length=4096)
    sha256: Sha256
    text: str = Field(max_length=65536)

    @model_validator(mode="after")
    def _validate_path_and_digest(self):
        _repo_path(self.path)
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("inspected_file_digest_mismatch")
        return self


class RepositoryInspection(Contract):
    schema_version: Literal["zelo-self-inspection-v1"] = INSPECTION_SCHEMA
    base_sha: Sha1
    branch: str = Field(min_length=1, max_length=256)
    zelo_version: str = Field(min_length=1, max_length=64)
    clean: Literal[True] = True
    tracked_paths: tuple[str, ...] = Field(max_length=4096)
    files: tuple[InspectedFile, ...] = Field(max_length=128)
    content_truncated: bool
    total_content_bytes: int = Field(ge=0, le=1048576)
    fingerprint: Sha256

    @model_validator(mode="after")
    def _validate_snapshot(self):
        if any(character in self.branch for character in "\r\n\x00"):
            raise ValueError("inspection_branch_invalid")
        if self.tracked_paths != tuple(sorted(set(self.tracked_paths))):
            raise ValueError("inspection_paths_not_canonical")
        for path in self.tracked_paths:
            _repo_path(path)
        file_paths = tuple(item.path for item in self.files)
        if file_paths != tuple(sorted(set(file_paths))):
            raise ValueError("inspection_files_not_canonical")
        if not set(file_paths).issubset(self.tracked_paths):
            raise ValueError("inspection_file_not_tracked")
        if sum(len(item.text.encode("utf-8")) for item in self.files) != self.total_content_bytes:
            raise ValueError("inspection_content_size_mismatch")
        expected = _fingerprint(self.model_dump(mode="json", exclude={"fingerprint"}))
        if self.fingerprint != expected:
            raise ValueError("inspection_fingerprint_mismatch")
        return self

    @classmethod
    def create(cls, **values) -> "RepositoryInspection":
        payload = dict(values)
        canonical = dict(payload)
        canonical["files"] = [
            item.model_dump(mode="json") if isinstance(item, InspectedFile) else item
            for item in payload.get("files", ())
        ]
        payload["fingerprint"] = _fingerprint(canonical)
        return cls.model_validate(payload)


class ImprovementProposalDraft(Contract):
    schema_version: Literal["zelo-improvement-proposal-v1"] = PROPOSAL_SCHEMA
    title: str = Field(min_length=1, max_length=200)
    problem: str = Field(min_length=1, max_length=4096)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=16)
    expected_benefit: str = Field(min_length=1, max_length=4096)
    implementation_instruction: str = Field(min_length=1, max_length=8192)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=16)
    risk: Literal["low", "medium", "high"]
    required_capabilities: tuple[CapabilityName, ...] = Field(min_length=1, max_length=16)
    scope_paths: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _validate_canonical_fields(self):
        for name in ("evidence", "acceptance_criteria", "required_capabilities", "scope_paths"):
            value = getattr(self, name)
            if value != tuple(sorted(set(value))):
                raise ValueError(f"{name}_not_canonical")
        for path in (*self.evidence, *self.scope_paths):
            _repo_path(path)
        return self


class ImprovementProposal(Contract):
    schema_version: Literal["zelo-improvement-proposal-v1"] = PROPOSAL_SCHEMA
    goal: str = Field(min_length=1, max_length=1024)
    draft: ImprovementProposalDraft
    base_sha: Sha1
    inspection_fingerprint: Sha256
    planner_config_version: str = Field(min_length=1, max_length=128)
    proposal_fingerprint: Sha256

    @model_validator(mode="after")
    def _validate_fingerprint(self):
        expected = _fingerprint(self.model_dump(mode="json", exclude={"proposal_fingerprint"}))
        if self.proposal_fingerprint != expected:
            raise ValueError("proposal_fingerprint_mismatch")
        return self

    @classmethod
    def freeze(
        cls,
        draft: ImprovementProposalDraft,
        inspection: RepositoryInspection,
        *,
        goal: str,
        planner_config_version: str,
    ) -> "ImprovementProposal":
        if not goal or goal.strip() != goal or len(goal) > 1024:
            raise ValueError("improvement_goal_invalid")
        payload = {
            "schema_version": PROPOSAL_SCHEMA,
            "goal": goal,
            "draft": draft.model_dump(mode="json"),
            "base_sha": inspection.base_sha,
            "inspection_fingerprint": inspection.fingerprint,
            "planner_config_version": planner_config_version,
        }
        payload["proposal_fingerprint"] = _fingerprint(payload)
        return cls.model_validate(payload)


class ImprovementEvaluation(Contract):
    schema_version: Literal["zelo-improvement-evaluation-v1"] = EVALUATION_SCHEMA
    proposal_fingerprint: Sha256
    developer_task_id: str = Field(min_length=1, max_length=64)
    developer_attempt_id: str = Field(min_length=1, max_length=64)
    validation_passed: bool
    reviewer_high_or_critical: bool
    status: Literal["accepted_candidate", "rejected_candidate", "inconclusive"]

    @model_validator(mode="after")
    def _fail_closed(self):
        if self.status == "accepted_candidate" and (
            not self.validation_passed or self.reviewer_high_or_critical
        ):
            raise ValueError("improvement_evaluation_invalid")
        return self


def _git(repo: Path, *arguments: str) -> bytes:
    try:
        result = run_sync(git_argv(*arguments), cwd=repo, timeout=5)
    except DeveloperError:
        raise SelfImprovementError("inspection_git_failed") from None
    if result.exit_code != 0:
        raise SelfImprovementError("inspection_git_failed")
    return result.stdout


def _decode_paths(data: bytes) -> tuple[str, ...]:
    try:
        values = tuple(item.decode("utf-8") for item in data.split(b"\x00") if item)
    except UnicodeDecodeError:
        raise SelfImprovementError("inspection_path_not_utf8") from None
    try:
        return tuple(_repo_path(value) for value in values)
    except ValueError:
        raise SelfImprovementError("inspection_path_invalid") from None


def _ignored_untracked_path(value: str) -> bool:
    if (
        not value
        or len(value) > 4096
        or "\\" in value
        or "\x00" in value
        or any(character in value for character in "\r\n")
    ):
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or "." in path.parts or ".." in path.parts:
        return False
    return (
        path.parts[:2] == ("docs", "reviews")
        or (path.parts and path.parts[0] in {"runtime", "secrets", "workspaces", ".venv"})
        or path.name in _FORBIDDEN_NAMES
    )


def _repository_status_clean(data: bytes) -> bool:
    try:
        entries = [item.decode("utf-8") for item in data.split(b"\x00") if item]
    except UnicodeDecodeError:
        raise SelfImprovementError("inspection_path_not_utf8") from None

    for entry in entries:
        if len(entry) < 4 or entry[2] != " ":
            return False
        status, path = entry[:2], entry[3:]
        if status == "??" and _ignored_untracked_path(path):
            continue
        return False
    return True


def _content_candidate(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.name in _TEXT_TOP_LEVEL and len(path.parts) == 1:
        return True
    if not path.parts or path.parts[0] not in _TEXT_ROOTS:
        return False
    if len(path.parts) >= 2 and path.parts[:2] == ("docs", "reviews"):
        return False
    return path.suffix.lower() in _TEXT_SUFFIXES


def _read_regular_utf8(repo: Path, relative: str, limit: int) -> str | None:
    parts = PurePosixPath(relative).parts
    root_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_fd = root_fd
    opened: list[int] = []
    try:
        for part in parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            opened.append(child)
            parent_fd = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        chunks = bytearray()
        while len(chunks) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > limit:
            return None
        try:
            return bytes(chunks).decode("utf-8")
        except UnicodeDecodeError:
            return None
    except OSError:
        raise SelfImprovementError("inspection_file_unavailable") from None
    finally:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
        os.close(root_fd)


class RepositoryInspector:
    """Build a deterministic, bounded, read-only snapshot of Zelo source."""

    def __init__(
        self,
        repo: Path = ROOT,
        *,
        max_tracked_paths: int = 4096,
        max_content_files: int = 24,
        max_file_bytes: int = 4096,
        max_total_bytes: int = 12288,
    ) -> None:
        if (
            not 1 <= max_tracked_paths <= 4096
            or not 1 <= max_content_files <= 128
            or not 1024 <= max_file_bytes <= 65536
            or not 4096 <= max_total_bytes <= 1048576
        ):
            raise ValueError("inspection_limits_invalid")
        self.repo = repo.resolve()
        self.max_tracked_paths = max_tracked_paths
        self.max_content_files = max_content_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    def inspect(self) -> RepositoryInspection:
        try:
            if not (self.repo / ".git").is_dir():
                raise SelfImprovementError("inspection_repository_unavailable")

            status_before = _git(
                self.repo,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            )
            if not _repository_status_clean(status_before):
                raise SelfImprovementError("inspection_repository_dirty")

            base_sha = _git(self.repo, "rev-parse", "HEAD").decode("ascii").strip()
            branch = _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").decode("utf-8").strip()
            if not base_sha or not branch:
                raise SelfImprovementError("inspection_repository_identity_invalid")

            tracked = _decode_paths(_git(self.repo, "ls-files", "-z"))
            if len(tracked) > self.max_tracked_paths:
                raise SelfImprovementError("inspection_tracked_path_limit")
            tracked = tuple(sorted(tracked))

            files: list[InspectedFile] = []
            total = 0
            truncated = False
            candidates = tuple(path for path in tracked if _content_candidate(path))
            for relative in candidates:
                if len(files) >= self.max_content_files:
                    truncated = True
                    break
                text = _read_regular_utf8(self.repo, relative, self.max_file_bytes)
                if text is None:
                    continue
                encoded = text.encode("utf-8")
                if total + len(encoded) > self.max_total_bytes:
                    truncated = True
                    continue
                files.append(
                    InspectedFile(
                        path=relative,
                        sha256=hashlib.sha256(encoded).hexdigest(),
                        text=text,
                    )
                )
                total += len(encoded)

            status_after = _git(
                self.repo,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            )
            head_after = _git(self.repo, "rev-parse", "HEAD").decode("ascii").strip()
            if not _repository_status_clean(status_after) or head_after != base_sha:
                raise SelfImprovementError("inspection_repository_changed")

            return RepositoryInspection.create(
                schema_version=INSPECTION_SCHEMA,
                base_sha=base_sha,
                branch=branch,
                zelo_version=__version__,
                clean=True,
                tracked_paths=tracked,
                files=tuple(files),
                content_truncated=truncated,
                total_content_bytes=total,
            )
        except UnicodeDecodeError:
            raise SelfImprovementError("inspection_repository_identity_invalid") from None
