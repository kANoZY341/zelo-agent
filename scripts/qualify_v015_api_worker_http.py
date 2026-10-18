"""Manually qualify one configured public HTTPS API Worker through isolated Core.

Run from this repository:
    env -i PATH=/usr/bin:/bin HOME="$HOME" .venv/bin/python -m \
        scripts.qualify_v015_api_worker_http

This opt-in probe sends one unauthenticated GET to the public GitHub API root.
It never changes the default endpoint registry or the normal Zelo database.
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


async def run() -> None:
    if not __debug__:
        raise RuntimeError("Qualification requires enabled Python assertions")
    if any(key.startswith("ZELO_") for key in os.environ):
        raise RuntimeError("Qualification requires a sanitized environment (no ZELO_* values)")
    (ROOT / "runtime").mkdir(parents=True, exist_ok=True, mode=0o700)
    (ROOT / "workspaces").mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="r3-api-runtime-", dir=ROOT / "runtime") as rt:
        with tempfile.TemporaryDirectory(
            prefix="r3-api-workspaces-", dir=ROOT / "workspaces"
        ) as ws:
            token = secrets.token_urlsafe(40)
            settings = Settings(
                _env_file=None,
                operator_token=SecretStr(token),
                runtime_dir=Path(rt),
                workspace_root=Path(ws),
                runner_poll_seconds=0.02,
                api_worker_endpoints=("github=https://api.github.com",),
                shell_worker_enabled=False,
                browser_worker_enabled=False,
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
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
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
                    base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30.0
                ) as client:
                    health = await client.get("/health")
                    assert health.status_code == 200 and health.json()["status"] == "healthy"
                    headers = {"Authorization": f"Bearer {token}"}

                    noauth = await client.post(
                        "/tasks",
                        json={"kind": "api_run", "payload": {"endpoint_id": "github", "path": "/"}},
                    )
                    assert noauth.status_code == 401
                    print("API_TASK_AUTH_DEFAULT_DENY=PASS", flush=True)

                    async def total() -> int:
                        response = await client.get("/tasks", headers=headers)
                        assert response.status_code == 200
                        return response.json()["total"]

                    original = await total()
                    unapproved = await client.post(
                        "/tasks",
                        headers=headers,
                        json={"kind": "api_run", "payload": {"endpoint_id": "other", "path": "/"}},
                    )
                    assert unapproved.status_code == 422
                    assert unapproved.json()["detail"] == "api_endpoint_not_registered"
                    unsafe_path = await client.post(
                        "/tasks",
                        headers=headers,
                        json={
                            "kind": "api_run",
                            "payload": {"endpoint_id": "github", "path": "//127.0.0.1/"},
                        },
                    )
                    assert unsafe_path.status_code == 422
                    assert await total() == original
                    print("API_UNAPPROVED_ENDPOINT_AND_PATH_REFUSED=PASS", flush=True)

                    submitted = await client.post(
                        "/tasks",
                        headers=headers,
                        json={
                            "kind": "api_run",
                            "payload": {"endpoint_id": "github", "path": "/"},
                            "timeout_seconds": 20,
                        },
                    )
                    assert submitted.status_code == 201, submitted.text[:240]
                    identifier = submitted.json()["id"]
                    for _ in range(300):
                        response = await client.get(f"/tasks/{identifier}", headers=headers)
                        assert response.status_code == 200
                        task = response.json()
                        if task["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
                            break
                        await asyncio.sleep(0.1)
                    assert task["status"] == "succeeded", (task["status"], task.get("error"))
                    assert task["attempts"][0]["outcome"] == "succeeded"
                    assert task["api_spec"]["endpoint"]["endpoint"]["origin"] == (
                        "https://api.github.com"
                    )
                    assert task["api_spec"]["worker_selection"]["descriptor"]["worker_id"] == (
                        "api_core"
                    )
                    result = task["result"]
                    assert result["endpoint_id"] == "github"
                    assert result["status_code"] == 200
                    assert result["body"]["current_user_url"] == "https://api.github.com/user"
                    assert 0 < result["response_bytes"] <= 262_144
                    events = await client.get(f"/tasks/{identifier}/events", headers=headers)
                    assert events.status_code == 200
                    names = [event["event"] for event in events.json()]
                    assert "supervisor_worker_selected" in names
                    assert "api_execution_started" in names
                    assert "api_execution_completed" in names
                    assert "api_completed" in names
                    assert await total() == original + 1
                    print("API_REAL_PUBLIC_HTTPS_TASK_AND_AUDIT=PASS", flush=True)
            finally:
                server.should_exit = True
                await asyncio.wait_for(server_task, timeout=20)
                print("CORE_LOOPBACK_STOPPED=PASS", flush=True)
    print("TEMPORARY_TEST_DATA_CLEANED=PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
