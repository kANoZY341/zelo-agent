"""Manually qualify Python Core on localhost with an isolated, disposable database.

Run from this repository:
    env -i PATH=/usr/bin:/bin HOME="$HOME" .venv/bin/python -m \
        scripts.qualify_v015_python_worker_http

This never enables Shell, Browser, Computer, external Skill merge or Media rights.
It neither touches the normal Zelo runtime database nor starts a public listener.
"""

import asyncio
import os
import secrets
import socket
import tempfile
from pathlib import Path

import httpx
import uvicorn
from pydantic import SecretStr

from app.application import create_app
from app.config import Settings
from app.database import Database

ROOT = Path(__file__).resolve().parent.parent


def direct_bwrap_children() -> set[int]:
    """Observe only this qualifier's direct native sandbox children on Linux."""
    children = set()
    for thread in Path("/proc/self/task").iterdir():
        try:
            pids = (thread / "children").read_text().split()
        except FileNotFoundError:
            continue  # A short-lived thread ended during enumeration.
        for pid in pids:
            try:
                if os.readlink(f"/proc/{pid}/exe") == "/usr/bin/bwrap":
                    children.add(int(pid))
            except FileNotFoundError:
                continue  # A child exited during observation.
    return children


async def run():
    if not __debug__:
        raise RuntimeError("Qualification requires enabled Python assertions")
    if any(key.startswith("ZELO_") for key in os.environ):
        raise RuntimeError("Qualification requires a sanitized environment (no ZELO_* values)")
    # Ignored worktree directories need not exist in a fresh checkout.
    (ROOT / "runtime").mkdir(parents=True, exist_ok=True, mode=0o700)
    (ROOT / "workspaces").mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="r3-runtime-", dir=ROOT / "runtime") as rt:
        with tempfile.TemporaryDirectory(prefix="r3-workspaces-", dir=ROOT / "workspaces") as ws:
            token = secrets.token_urlsafe(40)
            settings = Settings(
                _env_file=None,
                operator_token=SecretStr(token),
                runtime_dir=Path(rt),
                workspace_root=Path(ws),
                runner_poll_seconds=0.02,
                shell_worker_enabled=False,
                browser_worker_enabled=False,
                api_worker_endpoints=(),
                computer_supervisor_url=None,
                computer_supervisor_token=None,
                computer_token=None,
                skill_publication_external_merge_enabled=False,
                v015_release_approved_main_sha=None,
                youtube_research_approved_spec_sha256=None,
                youtube_company_approved_registry_sha256=None,
            )
            db = Database(settings.database_path)
            db.migrate()
            db.close()
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            app = create_app(settings)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=port,
                    log_level="error",
                    access_log=False,
                    lifespan="on",
                )
            )
            server_task = asyncio.create_task(server.serve())
            try:
                for _ in range(100):
                    if server.started:
                        break
                    if server_task.done():
                        raise RuntimeError("Core exited during startup")
                    await asyncio.sleep(0.1)
                assert server.started, "Core startup timed out"

                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20.0
                ) as client:
                    health = await client.get("/health")
                    assert health.status_code == 200 and health.json()["status"] == "healthy"
                    print("CORE_LOOPBACK_HEALTH=PASS", flush=True)

                    noauth = await client.post(
                        "/tasks", json={"kind": "python_run", "payload": {"code": "print(5)"}}
                    )
                    assert noauth.status_code == 401
                    print("TASK_AUTH_DEFAULT_DENY=PASS", flush=True)

                    headers = {"Authorization": f"Bearer {token}"}

                    async def total():
                        r = await client.get("/tasks", headers=headers)
                        assert r.status_code == 200
                        return r.json()["total"]

                    original = await total()
                    cases = (
                        ("api_run", {"endpoint_id": "unapproved", "path": "/"}),
                        ("browser_run", {"endpoint_id": "unapproved", "path": "/"}),
                        ("shell_run", {"script": "printf unapproved"}),
                        ("computer_run", {"action": "browser_snapshot"}),
                    )
                    for kind, payload in cases:
                        denied = await client.post(
                            "/tasks", headers=headers, json={"kind": kind, "payload": payload}
                        )
                        detail = denied.json().get("detail")
                        print(
                            f"DEFAULT_DENIAL_{kind}={denied.status_code}:{str(detail)[:180]}",
                            flush=True,
                        )
                        expected_denial = {
                            "api_run": "api_endpoint_not_registered",
                            "browser_run": "no_eligible_worker",
                            "shell_run": "no_eligible_worker",
                            "computer_run": "Computer worker requires Agent execution",
                        }[kind]
                        assert denied.status_code == 422
                        assert detail == expected_denial, (kind, detail)
                        assert await total() == original

                    async def execute(code, expected):
                        submitted = await client.post(
                            "/tasks",
                            headers=headers,
                            json={"kind": "python_run", "payload": {"code": code}},
                        )
                        assert submitted.status_code == 201, (
                            submitted.status_code,
                            submitted.text[:240],
                        )
                        identifier = submitted.json()["id"]
                        for _ in range(150):
                            result = await client.get(f"/tasks/{identifier}", headers=headers)
                            assert result.status_code == 200
                            task = result.json()
                            if task["status"] in (
                                "succeeded",
                                "failed",
                                "cancelled",
                                "interrupted",
                            ):
                                break
                            await asyncio.sleep(0.1)
                        assert task["status"] == expected, (task["status"], task.get("error"))
                        events = await client.get(f"/tasks/{identifier}/events", headers=headers)
                        assert events.status_code == 200
                        names = [e["event"] for e in events.json()]
                        assert "supervisor_worker_selected" in names, names
                        assert "python_execution_started" in names, names
                        return identifier, task, names

                    identifier, good, names = await execute("print(sum(range(7)))", "succeeded")
                    assert good["result"]["stdout"].strip() == "21", repr(good["result"]["stdout"])
                    assert good["result"]["exit_code"] == 0
                    print("PYTHON_REAL_HTTP_EXECUTION=PASS", flush=True)

                    _, no_net, _ = await execute(
                        "import socket;socket.create_connection(('1.1.1.1',80),timeout=0.2)",
                        "failed",
                    )
                    assert no_net["result"]["exit_code"] != 0
                    assert "Network is unreachable" in no_net["result"]["stderr"]
                    print("PYTHON_NETWORK_EGRESS_REFUSED=PASS", flush=True)

                    _, no_write, _ = await execute(
                        "open('/tmp/zelo-r3-write','w').write('x')",
                        "failed",
                    )
                    assert no_write["result"]["exit_code"] != 0
                    assert "Read-only file system" in no_write["result"]["stderr"]
                    print("PYTHON_FILESYSTEM_WRITE_REFUSED=PASS", flush=True)

                    assert await total() == original + 3
                    assert "python_execution_completed" in names
                    assert "python_completed" in names
                    print("TASKS_PERSISTED_AND_AUDITED=PASS", flush=True)

                    assert not direct_bwrap_children(), "Prior sandbox child remains"
                    submitted = await client.post(
                        "/tasks",
                        headers=headers,
                        json={
                            "kind": "python_run",
                            "payload": {"code": "import time; time.sleep(30)"},
                            "timeout_seconds": 45,
                        },
                    )
                    assert submitted.status_code == 201, submitted.text[:240]
                    cancelled_id = submitted.json()["id"]
                    for _ in range(100):
                        events = await client.get(f"/tasks/{cancelled_id}/events", headers=headers)
                        assert events.status_code == 200
                        if any(e["event"] == "python_execution_started" for e in events.json()):
                            break
                        await asyncio.sleep(0.05)
                    else:
                        raise AssertionError("Python Worker did not begin before cancellation")
                    for _ in range(100):
                        native_children = direct_bwrap_children()
                        if native_children:
                            break
                        await asyncio.sleep(0.05)
                    assert len(native_children) == 1, "Native Python sandbox did not start"
                    native_pid = next(iter(native_children))
                    cancelled = await client.post(f"/tasks/{cancelled_id}/cancel", headers=headers)
                    assert cancelled.status_code == 202, cancelled.text[:240]
                    assert cancelled.json()["cancel_requested"] is True
                    for _ in range(100):
                        response = await client.get(f"/tasks/{cancelled_id}", headers=headers)
                        assert response.status_code == 200
                        stopped = response.json()
                        if stopped["status"] != "running":
                            break
                        await asyncio.sleep(0.05)
                    assert stopped["status"] == "cancelled", stopped
                    assert stopped["error"] == "cancelled_by_operator"
                    assert stopped["result"] is None
                    assert stopped["attempts"][0]["outcome"] == "cancelled"
                    events = await client.get(f"/tasks/{cancelled_id}/events", headers=headers)
                    assert events.status_code == 200
                    names = [e["event"] for e in events.json()]
                    assert "cancellation_requested" in names
                    assert "cancellation_completed" in names
                    assert "python_failed" in names
                    assert native_pid not in direct_bwrap_children(), "Native sandbox child remains"
                    assert not Path(f"/proc/{native_pid}").exists(), "Native child not reaped"
                    assert await total() == original + 4
                    print("PYTHON_RUNNING_TASK_CANCELLATION_AND_REAP=PASS", flush=True)

                    assert not direct_bwrap_children(), "Prior sandbox child remains"
                    submitted = await client.post(
                        "/tasks",
                        headers=headers,
                        json={
                            "kind": "python_run",
                            "payload": {"code": "import time; time.sleep(30)"},
                            "timeout_seconds": 8,
                        },
                    )
                    assert submitted.status_code == 201, submitted.text[:240]
                    timed_out_id = submitted.json()["id"]
                    for _ in range(100):
                        events = await client.get(f"/tasks/{timed_out_id}/events", headers=headers)
                        assert events.status_code == 200
                        if any(e["event"] == "python_execution_started" for e in events.json()):
                            break
                        await asyncio.sleep(0.05)
                    else:
                        raise AssertionError("Python Worker did not begin before timeout")
                    for _ in range(100):
                        native_children = direct_bwrap_children()
                        if native_children:
                            break
                        await asyncio.sleep(0.05)
                    assert len(native_children) == 1, "Timed Python sandbox did not start"
                    native_pid = next(iter(native_children))
                    for _ in range(200):
                        response = await client.get(f"/tasks/{timed_out_id}", headers=headers)
                        assert response.status_code == 200
                        timed_out = response.json()
                        if timed_out["status"] in (
                            "failed",
                            "succeeded",
                            "cancelled",
                            "interrupted",
                        ):
                            break
                        await asyncio.sleep(0.1)
                    assert timed_out["status"] == "failed", timed_out
                    assert timed_out["error"] == "python_timeout"
                    assert timed_out["result"] is None
                    assert timed_out["attempts"][0]["outcome"] == "failed"
                    events = await client.get(f"/tasks/{timed_out_id}/events", headers=headers)
                    assert events.status_code == 200
                    names = [e["event"] for e in events.json()]
                    assert "python_execution_started" in names
                    assert "python_failed" in names
                    assert "python_execution_completed" not in names
                    assert native_pid not in direct_bwrap_children(), "Timed sandbox child remains"
                    assert not Path(f"/proc/{native_pid}").exists(), (
                        "Timed sandbox child not reaped"
                    )
                    assert await total() == original + 5
                    print("PYTHON_TASK_TIMEOUT_AND_REAP=PASS", flush=True)
            finally:
                server.should_exit = True
                await asyncio.wait_for(server_task, timeout=20)
                print("CORE_LOOPBACK_STOPPED=PASS", flush=True)
    print("TEMPORARY_TEST_DATA_CLEANED=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
