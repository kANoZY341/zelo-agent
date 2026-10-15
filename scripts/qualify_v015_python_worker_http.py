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
            finally:
                server.should_exit = True
                await asyncio.wait_for(server_task, timeout=20)
                print("CORE_LOOPBACK_STOPPED=PASS", flush=True)
    print("TEMPORARY_TEST_DATA_CLEANED=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
