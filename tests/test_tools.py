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
        (root / ".env.local").write_text("private", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("private", encoding="utf-8")
        for path in ("../../outside", "/etc/passwd", "link", ".env.local", ".git/config"):
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


def test_workspace_read_uses_checked_directory_after_name_swap(settings, monkeypatch) -> None:
    """A concurrent name swap cannot redirect a read to another directory."""
    import os

    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        base = Path(directory)
        root = base / "workspace"
        safe = root / "dir"
        private = base / "private"
        safe.mkdir(parents=True)
        private.mkdir()
        (safe / "note.txt").write_text("safe", encoding="utf-8")
        (private / "note.txt").write_text("private", encoding="utf-8")
        gateway = ToolGateway(settings.model_copy(update={"workspace_root": root}))
        swapped = False
        original_is_file = Path.is_file
        original_open = os.open

        def swap() -> None:
            nonlocal swapped
            if not swapped:
                safe.rename(root / "saved")
                safe.symlink_to(private, target_is_directory=True)
                swapped = True

        def checked_is_file(path: Path, *args, **kwargs) -> bool:
            result = original_is_file(path, *args, **kwargs)
            if path == safe / "note.txt" and result:
                swap()
            return result

        def checked_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == "dir" and kwargs.get("dir_fd") is not None:
                swap()
            return fd

        monkeypatch.setattr(Path, "is_file", checked_is_file)
        monkeypatch.setattr(os, "open", checked_open)
        result = gateway.execute("read_workspace_file", {"path": "dir/note.txt"})
        assert swapped
        assert result["text"] == "safe"


def test_workspace_list_uses_checked_directory_after_name_swap(settings, monkeypatch) -> None:
    """A concurrent name swap cannot list entries outside the workspace."""
    import os

    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        base = Path(directory)
        root = base / "workspace"
        safe = root / "dir"
        private = base / "private"
        safe.mkdir(parents=True)
        private.mkdir()
        (safe / "safe.txt").write_text("safe", encoding="utf-8")
        (private / "private.txt").write_text("private", encoding="utf-8")
        gateway = ToolGateway(settings.model_copy(update={"workspace_root": root}))
        swapped = False
        original_is_dir = Path.is_dir
        original_open = os.open

        def swap() -> None:
            nonlocal swapped
            if not swapped:
                safe.rename(root / "saved")
                safe.symlink_to(private, target_is_directory=True)
                swapped = True

        def checked_is_dir(path: Path, *args, **kwargs) -> bool:
            result = original_is_dir(path, *args, **kwargs)
            if path == safe and result:
                swap()
            return result

        def checked_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == "dir" and kwargs.get("dir_fd") is not None:
                swap()
            return fd

        monkeypatch.setattr(Path, "is_dir", checked_is_dir)
        monkeypatch.setattr(os, "open", checked_open)
        result = gateway.execute("list_workspace_files", {"path": "dir"})
        assert swapped
        assert [entry["path"] for entry in result["entries"]] == ["dir/safe.txt"]


def test_workspace_read_refuses_hardlinked_file_outside_root(settings) -> None:
    import os

    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        base = Path(directory)
        root = base / "workspace"
        root.mkdir()
        private = base / "private.txt"
        private.write_text("private", encoding="utf-8")
        os.link(private, root / "alias.txt")
        gateway = ToolGateway(settings.model_copy(update={"workspace_root": root}))
        with pytest.raises(ToolRuntimeError, match="workspace_file_not_allowed"):
            gateway.execute("read_workspace_file", {"path": "alias.txt"})


def test_workspace_tool_rejects_nul_without_leaking_directory_descriptors(settings) -> None:
    import os

    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        gateway = ToolGateway(settings.model_copy(update={"workspace_root": Path(directory)}))
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(3):
            with pytest.raises(ToolRuntimeError):
                gateway.execute("list_workspace_files", {"path": "bad\x00"})
            with pytest.raises(ToolRuntimeError):
                gateway.execute("read_workspace_file", {"path": "bad\x00/note.txt"})
        assert len(os.listdir("/proc/self/fd")) == before


def test_workspace_read_rejects_sensitive_configured_root(settings) -> None:
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        root = Path(directory) / "project" / "secrets"
        root.mkdir(parents=True)
        (root / "key.txt").write_text("private", encoding="utf-8")
        gateway = ToolGateway(settings.model_copy(update={"workspace_root": root}))
        with pytest.raises(ToolRuntimeError, match="workspace_file_not_allowed"):
            gateway.execute("read_workspace_file", {"path": "key.txt"})


def test_workspace_listing_bounds_directory_enumeration(settings, monkeypatch) -> None:
    import os

    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        root = Path(directory)
        for number in range(150):
            (root / f"entry-{number:03d}").touch()
        gateway = ToolGateway(
            settings.model_copy(update={"workspace_root": root, "workspace_max_entries": 1})
        )
        original_scandir = os.scandir
        scanned = 0

        class CountedScan:
            def __init__(self, iterator):
                self.iterator = iterator

            def __enter__(self):
                self.iterator.__enter__()
                return self

            def __exit__(self, *args):
                return self.iterator.__exit__(*args)

            def __iter__(self):
                nonlocal scanned
                for entry in self.iterator:
                    scanned += 1
                    if scanned > 100:
                        raise AssertionError("directory scan exceeded bound")
                    yield entry

        with monkeypatch.context() as patch:
            patch.setattr(os, "scandir", lambda path: CountedScan(original_scandir(path)))
            result = gateway.execute("list_workspace_files", {})
        assert result["truncated"] is True
        assert len(result["entries"]) == 1
        assert scanned <= 100


def test_workspace_listing_hides_sensitive_names_and_roots(settings) -> None:
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "workspaces") as directory:
        root = Path(directory) / "workspace"
        root.mkdir()
        (root / "visible.txt").write_text("okay", encoding="utf-8")
        (root / ".env.local").write_text("private", encoding="utf-8")
        for name in ("secrets", "runtime", ".git"):
            protected = root / name
            protected.mkdir()
            (protected / "private.txt").write_text("private", encoding="utf-8")
        gateway = ToolGateway(settings.model_copy(update={"workspace_root": root}))
        names = [entry["path"] for entry in gateway.execute("list_workspace_files", {})["entries"]]
        assert names == ["visible.txt"]
        for name in ("secrets", "runtime", ".git"):
            with pytest.raises(ToolRuntimeError, match="workspace_directory_not_allowed"):
                gateway.execute("list_workspace_files", {"path": name})
        sensitive_root = ToolGateway(
            settings.model_copy(update={"workspace_root": root / "secrets"})
        )
        with pytest.raises(ToolRuntimeError, match="workspace_directory_not_allowed"):
            sensitive_root.execute("list_workspace_files", {})
