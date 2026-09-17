import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT
from app.main import create_app
from app.tools import (
    SystemInfoResult,
    Tool,
    ToolGateway,
    ToolInput,
    ToolPolicyDenied,
    ToolRegistry,
    ToolRisk,
    ToolRuntimeError,
)
from tests.conftest import AUTH


def test_registry_is_explicit_and_deterministic(settings) -> None:
    gateway = ToolGateway(settings)
    assert [item["name"] for item in gateway.registry.metadata()] == [
        "list_workspace_files",
        "read_workspace_file",
        "repo_status",
        "system_info",
    ]
    with pytest.raises(ValueError, match="Duplicate"):
        gateway.registry.register(gateway.registry.get("system_info"))
    with pytest.raises(ToolRuntimeError, match="unknown_tool"):
        gateway.registry.get("unknown")


def test_tool_policy_fails_closed(settings) -> None:
    class WriteTool(Tool):
        def __init__(self):
            super().__init__(
                name="write",
                description="test",
                input_model=ToolInput,
                result_model=SystemInfoResult,
                risk=ToolRisk.WRITE,
            )

    with pytest.raises(ToolPolicyDenied):
        ToolGateway(settings, ToolRegistry((WriteTool(),))).execute("write", {})


def test_tools_endpoint_is_authenticated(client: TestClient) -> None:
    response = client.get("/tools")
    assert response.status_code == 200
    assert all("name" in tool and "input_schema" in tool for tool in response.json())
    client.headers.pop("Authorization")
    assert client.get("/tools").status_code == 401


def test_workspace_tools_confine_and_validate(settings, database) -> None:
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        root = Path(directory)
        (root / "readme.txt").write_text("hello", encoding="utf-8")
        (root / "binary").write_bytes(b"\xff")
        (root / "large").write_text("123456789", encoding="utf-8")
        (root / "outside").write_text("outside", encoding="utf-8")
        (root / "link").symlink_to(root / "outside")
        configured = settings.model_copy(
            update={"workspace_root": root, "workspace_max_file_size": 8}
        )
        gateway = ToolGateway(configured)
        assert gateway.execute("read_workspace_file", {"path": "readme.txt"})["text"] == "hello"
        assert gateway.execute("list_workspace_files", {})["entries"][0]["path"] == "binary"
        for path in ("../../outside", "/etc/passwd", "link"):
            with pytest.raises(ToolRuntimeError):
                gateway.execute("read_workspace_file", {"path": path})
        for path in ("binary", "large", "missing"):
            with pytest.raises(ToolRuntimeError):
                gateway.execute("read_workspace_file", {"path": path})


def test_tool_call_task_persists_result_and_audit(settings, database) -> None:
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        root = Path(directory) / "workspace"
        configured = settings.model_copy(update={"workspace_root": root})
        assert not root.exists()
        with TestClient(create_app(configured), headers=AUTH) as client:
            assert root.is_dir()
            (root / "value.txt").write_text("value", encoding="utf-8")
            response = client.post(
                "/tasks",
                json={
                    "kind": "tool_call",
                    "payload": {
                        "tool": "read_workspace_file",
                        "arguments": {"path": "value.txt"},
                    },
                },
            )
            assert response.status_code == 201
            task_id = response.json()["id"]
            for _ in range(300):
                task = client.get(f"/tasks/{task_id}").json()
                if task["status"] == "succeeded":
                    break
                time.sleep(0.01)
            assert task["result"]["text"] == "value"
            events = [event["event"] for event in client.get(f"/tasks/{task_id}/events").json()]
            assert "tool_selected" in events
            assert "tool_execution_started" in events
            assert "tool_execution_completed" in events
