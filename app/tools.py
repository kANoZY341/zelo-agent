"""Zelo-owned typed read-only tool runtime."""

import os
import platform
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.config import ROOT, Settings


class ToolRisk(StrEnum):
    SAFE_READ = "safe_read"
    SENSITIVE_READ = "sensitive_read"
    WRITE = "write"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class ToolRuntimeError(Exception):
    """Expected, non-sensitive tool failure."""


class ToolPolicyDenied(ToolRuntimeError):
    pass


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemInfoInput(ToolInput):
    pass


class SystemInfoResult(BaseModel):
    platform: str
    python_version: str
    zelo_version: str
    logical_cpu_count: int


class RepoStatusInput(ToolInput):
    pass


class RepoStatusResult(BaseModel):
    branch: str
    clean: bool
    changes: list[str]
    head_short_sha: str


class WorkspacePathInput(ToolInput):
    path: str = Field(default="", max_length=512)


class WorkspaceFileInput(ToolInput):
    path: str = Field(min_length=1, max_length=512)


class WorkspaceEntry(BaseModel):
    path: str
    kind: str


class ListWorkspaceFilesResult(BaseModel):
    path: str
    entries: list[WorkspaceEntry]
    truncated: bool


class ReadWorkspaceFileResult(BaseModel):
    path: str
    text: str
    truncated: bool


class Tool[InputT: ToolInput, ResultT: BaseModel]:
    name: str
    description: str
    input_model: type[InputT]
    result_model: type[ResultT]
    risk: ToolRisk
    approval_required: bool
    timeout_seconds: float | None

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_model: type[InputT],
        result_model: type[ResultT],
        risk: ToolRisk = ToolRisk.SAFE_READ,
        approval_required: bool = False,
        timeout_seconds: float | None = 5.0,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.result_model = result_model
        self.risk = risk
        self.approval_required = approval_required
        self.timeout_seconds = timeout_seconds

    def run(self, arguments: InputT, settings: Settings) -> ResultT:
        raise NotImplementedError

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk,
            "approval_required": self.approval_required,
            "timeout_seconds": self.timeout_seconds,
            "input_schema": self.input_model.model_json_schema(),
        }


class SystemInfoTool(Tool[SystemInfoInput, SystemInfoResult]):
    def __init__(self) -> None:
        super().__init__(
            name="system_info",
            description="Return a small allowlisted set of local runtime information.",
            input_model=SystemInfoInput,
            result_model=SystemInfoResult,
        )

    def run(self, arguments: SystemInfoInput, settings: Settings) -> SystemInfoResult:
        return SystemInfoResult(
            platform=platform.system().lower(),
            python_version=platform.python_version(),
            zelo_version=__version__,
            logical_cpu_count=os.cpu_count() or 1,
        )


class RepoStatusTool(Tool[RepoStatusInput, RepoStatusResult]):
    def __init__(self) -> None:
        super().__init__(
            name="repo_status",
            description="Return read-only status for the Zelo repository.",
            input_model=RepoStatusInput,
            result_model=RepoStatusResult,
            timeout_seconds=5.0,
        )

    @staticmethod
    def _git(*args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError):
            raise ToolRuntimeError("repository_status_unavailable") from None
        return completed.stdout.strip()

    def run(self, arguments: RepoStatusInput, settings: Settings) -> RepoStatusResult:
        branch = self._git("branch", "--show-current")
        status = self._git("status", "--short")
        changes = [line for line in status.splitlines() if line]
        return RepoStatusResult(
            branch=branch or "detached",
            clean=not changes,
            changes=changes,
            head_short_sha=self._git("rev-parse", "--short=12", "HEAD"),
        )


def _workspace_path(root: Path, relative: str, *, allow_empty: bool) -> Path:
    if not allow_empty and not relative:
        raise ToolRuntimeError("workspace_path_required")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ToolRuntimeError("invalid_workspace_path")
    candidate_path = root / candidate
    if candidate_path.is_symlink():
        raise ToolRuntimeError("workspace_symlink_not_allowed")
    try:
        root_resolved = root.resolve(strict=True)
        resolved = candidate_path.resolve(strict=False)
    except OSError:
        raise ToolRuntimeError("workspace_path_unavailable") from None
    if not resolved.is_relative_to(root_resolved):
        raise ToolRuntimeError("workspace_path_outside_root")
    return resolved


class ListWorkspaceFilesTool(Tool[WorkspacePathInput, ListWorkspaceFilesResult]):
    def __init__(self) -> None:
        super().__init__(
            name="list_workspace_files",
            description="List workspace paths without reading file contents.",
            input_model=WorkspacePathInput,
            result_model=ListWorkspaceFilesResult,
        )

    def run(self, arguments: WorkspacePathInput, settings: Settings) -> ListWorkspaceFilesResult:
        root = settings.workspace_root
        target = _workspace_path(root, arguments.path, allow_empty=True)
        if not target.exists() or not target.is_dir():
            raise ToolRuntimeError("workspace_directory_unavailable")
        entries: list[WorkspaceEntry] = []
        truncated = False
        base_depth = len(target.relative_to(root.resolve()).parts)
        for path in sorted(target.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            if path.is_symlink():
                resolved = path.resolve(strict=False)
                if not resolved.is_relative_to(root.resolve()):
                    raise ToolRuntimeError("workspace_symlink_escape")
                continue
            if len(path.relative_to(root).parts) - base_depth > settings.workspace_max_depth:
                continue
            if len(entries) >= settings.workspace_max_entries:
                truncated = True
                break
            entries.append(
                WorkspaceEntry(
                    path=path.relative_to(root).as_posix(),
                    kind="directory" if path.is_dir() else "file",
                )
            )
        return ListWorkspaceFilesResult(
            path=target.relative_to(root.resolve()).as_posix() or ".",
            entries=entries,
            truncated=truncated,
        )


class ReadWorkspaceFileTool(Tool[WorkspaceFileInput, ReadWorkspaceFileResult]):
    def __init__(self) -> None:
        super().__init__(
            name="read_workspace_file",
            description="Read bounded UTF-8 text from a workspace file.",
            input_model=WorkspaceFileInput,
            result_model=ReadWorkspaceFileResult,
        )

    def run(self, arguments: WorkspaceFileInput, settings: Settings) -> ReadWorkspaceFileResult:
        root = settings.workspace_root
        target = _workspace_path(root, arguments.path, allow_empty=False)
        if not target.exists() or not target.is_file():
            raise ToolRuntimeError("workspace_file_unavailable")
        if (
            target.name in {".env", ".env.example"}
            or any(part in {"secrets", "runtime"} for part in target.parts)
            or target.suffix in {".sqlite", ".sqlite3", ".db"}
        ):
            raise ToolRuntimeError("workspace_file_not_allowed")
        try:
            size = target.stat().st_size
            if size > settings.workspace_max_file_size:
                raise ToolRuntimeError("workspace_file_too_large")
            data = target.read_bytes()
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolRuntimeError("workspace_file_not_utf8") from None
        except OSError:
            raise ToolRuntimeError("workspace_file_unavailable") from None
        return ReadWorkspaceFileResult(
            path=target.relative_to(root.resolve()).as_posix(),
            text=text,
            truncated=False,
        )


class ToolRegistry:
    def __init__(self, tools: tuple[Tool[Any, Any], ...] = ()) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool[Any, Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any]:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolRuntimeError("unknown_tool") from None

    def metadata(self) -> list[dict[str, Any]]:
        return [self._tools[name].metadata() for name in sorted(self._tools)]


class ToolPolicy:
    def evaluate(self, tool: Tool[Any, Any]) -> None:
        if tool.risk is not ToolRisk.SAFE_READ:
            raise ToolPolicyDenied("tool_policy_denied")
        if tool.approval_required:
            raise ToolPolicyDenied("tool_approval_unsupported")


class ToolGateway:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry or ToolRegistry(
            (
                SystemInfoTool(),
                RepoStatusTool(),
                ListWorkspaceFilesTool(),
                ReadWorkspaceFileTool(),
            )
        )
        self.policy = policy or ToolPolicy()

    def validate(self, name: str, arguments: dict[str, Any]) -> ToolInput:
        tool = self.registry.get(name)
        try:
            return tool.input_model.model_validate(arguments)
        except ValueError:
            raise ToolRuntimeError("invalid_tool_arguments") from None

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.get(name)
        self.policy.evaluate(tool)
        typed_arguments = self.validate(name, arguments)
        result = tool.run(typed_arguments, self.settings)
        return tool.result_model.model_validate(result).model_dump(mode="json")
