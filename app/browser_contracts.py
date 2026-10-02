"""Core-owned immutable identity for bounded document rendering."""

from typing import Literal

from pydantic import Field

from app.api_contracts import ApiContract, ApiEndpointBinding
from app.developer_contracts import WorkerSelectionSnapshot


class BrowserExecutionSpec(ApiContract):
    schema_version: Literal["zelo-browser-execution-v1"] = "zelo-browser-execution-v1"
    endpoint: ApiEndpointBinding
    worker_selection: WorkerSelectionSnapshot
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str = Field(min_length=1, max_length=2048)
    timeout_seconds: float = Field(ge=0.1, le=30, allow_inf_nan=False)
