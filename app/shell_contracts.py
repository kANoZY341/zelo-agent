"""Core-owned exact identity; scripts cannot supply worker or native policy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.developer_contracts import WorkerSelectionSnapshot


class ShellExecutionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-shell-execution-v1"] = "zelo-shell-execution-v1"
    worker_selection: WorkerSelectionSnapshot
    script_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(ge=0.01, le=2, allow_inf_nan=False)
