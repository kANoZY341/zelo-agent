"""Zelo-owned typed read-only tool runtime."""

import os
import platform
import stat
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


def _workspace_parts(relative: str, *, allow_empty: bool) -> tuple[str, ...]:
    candidate = Path(relative)
    if (
        "\x00" in relative
        or candidate.is_absolute()
        or any(part == ".." for part in candidate.parts)
    ):
        raise ToolRuntimeError("invalid_workspace_path")
    parts = candidate.parts
    if not allow_empty and not parts:
        raise ToolRuntimeError("workspace_path_required")
    return parts


def _open_workspace_directory(root: Path, parts: tuple[str, ...]) -> int:
    """Walk only directory descriptors anchored under the trusted workspaces root."""

    anchor = ROOT / "workspaces"
    if not root.is_absolute() or not root.is_relative_to(anchor):
        raise ToolRuntimeError("workspace_path_outside_root")
    root_parts = root.relative_to(anchor).parts
    if any(component in {".", ".."} for component in root_parts):
        raise ToolRuntimeError("workspace_path_outside_root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd: int | None = None
    try:
        fd = os.open(anchor, flags)
        for component in (*root_parts, *parts):
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except (OSError, ValueError):
        if fd is not None:
            os.close(fd)
        raise ToolRuntimeError("workspace_directory_unavailable") from None


def _private_workspace_component(component: str) -> bool:
    return (
        component in {"secrets", "runtime", ".git"}
        or component == ".env"
        or component.startswith(".env.")
        or Path(component).suffix in {".sqlite", ".sqlite3", ".db"}
    )


def _private_workspace_path(root: Path, parts: tuple[str, ...]) -> bool:
    return any(_private_workspace_component(part) for part in (*root.parts, *parts))


class ListWorkspaceFilesTool(Tool[WorkspacePathInput, ListWorkspaceFilesResult]):
    def __init__(self) -> None:
        super().__init__(
            name="list_workspace_files",
            description="List workspace paths without reading file contents.",
            input_model=WorkspacePathInput,
            result_model=ListWorkspaceFilesResult,
        )

    def run(self, arguments: WorkspacePathInput, settings: Settings) -> ListWorkspaceFilesResult:
        parts = _workspace_parts(arguments.path, allow_empty=True)
        if _private_workspace_path(settings.workspace_root, parts):
            raise ToolRuntimeError("workspace_directory_not_allowed")
        entries: list[WorkspaceEntry] = []
        truncated = False
        scanned = 0
        scan_limit = max(64, settings.workspace_max_entries * 4)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

        def visit(directory_fd: int, prefix: tuple[str, ...], depth: int) -> None:
            nonlocal truncated, scanned
            if depth >= settings.workspace_max_depth or truncated:
                return
            try:
                with os.scandir(directory_fd) as scan:
                    names = []
                    for item in scan:
                        if scanned >= scan_limit:
                            truncated = True
                            break
                        scanned += 1
                        names.append(item.name)
                names.sort()
                for name in names:
                    if _private_workspace_component(name):
                        continue
                    mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        continue
                    is_directory = stat.S_ISDIR(mode)
                    if not is_directory and not stat.S_ISREG(mode):
                        continue
                    if len(entries) >= settings.workspace_max_entries:
                        truncated = True
                        return
                    relative = (*prefix, name)
                    entries.append(
                        WorkspaceEntry(
                            path="/".join(relative),
                            kind="directory" if is_directory else "file",
                        )
                    )
                    if is_directory and not truncated:
                        child_fd = os.open(name, flags, dir_fd=directory_fd)
                        try:
                            visit(child_fd, relative, depth + 1)
                        finally:
                            os.close(child_fd)
                        if truncated:
                            return
            except OSError:
                raise ToolRuntimeError("workspace_directory_unavailable") from None

        fd = _open_workspace_directory(settings.workspace_root, parts)
        try:
            visit(fd, parts, 0)
        finally:
            os.close(fd)
        return ListWorkspaceFilesResult(
            path="/".join(parts) or ".",
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
        parts = _workspace_parts(arguments.path, allow_empty=False)
        if _private_workspace_path(settings.workspace_root, parts):
            raise ToolRuntimeError("workspace_file_not_allowed")
        directory_fd = _open_workspace_directory(settings.workspace_root, parts[:-1])
        fd: int | None = None
        try:
            fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            identity = os.fstat(fd)
            if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1:
                raise ToolRuntimeError("workspace_file_not_allowed")
            limit = settings.workspace_max_file_size
            if identity.st_size > limit:
                raise ToolRuntimeError("workspace_file_too_large")
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(fd, limit + 1 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > limit:
                raise ToolRuntimeError("workspace_file_too_large")
            try:
                decoded = data.decode("utf-8")
            except UnicodeDecodeError:
                raise ToolRuntimeError("workspace_file_not_utf8") from None
        except (OSError, ValueError):
            raise ToolRuntimeError("workspace_file_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory_fd)
        return ReadWorkspaceFileResult(
            path="/".join(parts),
            text=decoded,
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
