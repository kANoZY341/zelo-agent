"""Typed immutable execution identity for read-only Agent computer work."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.developer_contracts import WorkerSelectionSnapshot

ComputerReadAction = Literal[
    "browser_snapshot",
    "browser_read",
    "files_list",
    "files_read",
]


class ComputerContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComputerExecutionSpec(ComputerContract):
    schema_version: Literal["zelo-computer-execution-v1"] = "zelo-computer-execution-v1"
    agent_instance_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    worker_selection: WorkerSelectionSnapshot
    action: ComputerReadAction
    path: str = Field(default="", max_length=1024)
    permission_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(ge=1.0, le=30.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_action_path(self):
        path = self.path
        if (
            path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(part == ".." for part in path.split("/"))
        ):
            raise ValueError("computer_path_invalid")
        if self.action == "files_read" and not path:
            raise ValueError("computer_file_path_required")
        if self.action.startswith("browser_") and path:
            raise ValueError("computer_browser_path_forbidden")
        return self
