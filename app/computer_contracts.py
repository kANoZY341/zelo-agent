"""Typed immutable execution identity for bounded Agent computer work."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api_contracts import API_PATH_LIMIT, ApiEndpointBinding
from app.developer_contracts import WorkerSelectionSnapshot

ComputerReadAction = Literal[
    "browser_snapshot",
    "browser_read",
    "files_list",
    "files_read",
]
ComputerAction = Literal[
    "browser_snapshot",
    "browser_read",
    "browser_navigate",
    "files_list",
    "files_read",
]
_NAVIGATION_PATH = re.compile(r"^/[A-Za-z0-9/._~!$&'()*+,;=:@-]*$")


class ComputerContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComputerExecutionSpec(ComputerContract):
    schema_version: Literal[
        "zelo-computer-execution-v1",
        "zelo-computer-execution-v2",
    ] = "zelo-computer-execution-v2"
    agent_instance_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    worker_selection: WorkerSelectionSnapshot
    action: ComputerAction
    path: str = Field(default="", max_length=API_PATH_LIMIT)
    space_activation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    endpoint: ApiEndpointBinding | None = None
    egress_policy_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    permission_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(ge=1.0, le=30.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_action_path(self):
        path = self.path
        if self.space_activation_id is not None and self.action != "files_read":
            raise ValueError("computer_space_activation_files_read_only")
        if self.action == "browser_navigate":
            if self.schema_version != "zelo-computer-execution-v2":
                raise ValueError("computer_navigation_requires_v2")
            if (
                self.endpoint is None
                or self.egress_policy_version is None
                or not path
                or not _NAVIGATION_PATH.fullmatch(path)
                or "//" in path
                or any(character in path for character in "\\?#\r\n\x00")
                or any(part in {".", ".."} for part in path.split("/"))
            ):
                raise ValueError("computer_navigation_binding_invalid")
            return self

        if self.endpoint is not None or self.egress_policy_version is not None:
            raise ValueError("computer_read_egress_binding_forbidden")
        if len(path) > 1024:
            raise ValueError("computer_path_invalid")
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
