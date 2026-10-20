"""Opt-in localhost Research Task proof with the pinned local model and private data."""

import argparse
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

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIGEST = "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd"
FACT = "The fictional Cedar Lantern project launch date is 14 June 2031."


async def inspect_model():
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        response = await client.get("http://127.0.0.1:11434/api/tags")
        response.raise_for_status()
        models = response.json()["models"]
    assert [model["digest"] for model in models if model["name"] == "qwen3.5:4b"] == [MODEL_DIGEST]


async def run():
    if not __debug__ or any(key.startswith("ZELO_") for key in os.environ):
        raise RuntimeError("Sanitized Python with assertions required")
    await inspect_model()
    (ROOT / "runtime").mkdir(mode=0o700, exist_ok=True)
    (ROOT / "workspaces").mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="research-runtime-", dir=ROOT / "runtime") as rt:
        with tempfile.TemporaryDirectory(
            prefix="research-workspace-", dir=ROOT / "workspaces"
        ) as ws:
            (Path(ws) / "facts.txt").write_text(FACT + "\n", encoding="utf-8")
            token = secrets.token_urlsafe(40)
            settings = Settings(
                _env_file=None,
                operator_token=SecretStr(token),
                runtime_dir=Path(rt),
                workspace_root=Path(ws),
                scheduler_enabled=False,
                runner_poll_seconds=0.02,
                ollama_url="http://127.0.0.1:11434",
                ollama_general_model="qwen3.5:4b",
                ollama_mind_model="qwen3.5:4b",
                ollama_reasoning_model="qwen3.5:4b",
                model_config_version="1",
                model_timeout_seconds=180,
                agent_timeout_seconds=300,
                max_agent_steps=6,
                max_agent_tool_calls=4,
                browser_worker_enabled=False,
                shell_worker_enabled=False,
                api_worker_endpoints=(),
                computer_supervisor_url=None,
                computer_supervisor_token=None,
                computer_token=None,
                skill_publication_external_merge_enabled=False,
                v015_release_approved_main_sha=None,
            )
            db = Database(settings.database_path)
            db.migrate()
            db.close()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
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
            serving = asyncio.create_task(server.serve())
            try:
                for _ in range(100):
                    if server.started:
                        break
                    if serving.done():
                        raise RuntimeError("Core exited during startup")
                    await asyncio.sleep(0.1)
                assert server.started, "Core startup timeout"
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30
                ) as client:
                    health = await client.get("/health")
                    assert health.status_code == 200, health.text
                    unauth = await client.post(
                        "/tasks", json={"kind": "research_run", "payload": {"query": "x"}}
                    )
                    assert unauth.status_code == 401, unauth.text
                    headers = {"Authorization": f"Bearer {token}"}
                    submitted = await client.post(
                        "/tasks",
                        headers=headers,
                        json={
                            "kind": "research_run",
                            "payload": {
                                "query": (
                                    "Read facts.txt using read_workspace_file. Give the exact "
                                    "Cedar Lantern launch date with a [S#] citation. Do not guess."
                                )
                            },
                            "timeout_seconds": 300,
                        },
                    )
                    assert submitted.status_code == 201, submitted.text[:300]
                    task_id = submitted.json()["id"]
                    assert (
                        submitted.json()["research_spec"]["descriptor"]["worker_id"]
                        == "research_core"
                    )
                    for _ in range(600):
                        response = await client.get(f"/tasks/{task_id}", headers=headers)
                        assert response.status_code == 200
                        task = response.json()
                        if task["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
                            break
                        await asyncio.sleep(0.5)
                    else:
                        raise AssertionError("Research task exceeded polling deadline")
                    events = await client.get(f"/tasks/{task_id}/events", headers=headers)
                    assert events.status_code == 200
                    names = [e["event"] for e in events.json()]
                    print(f"TASK_STATUS={task['status']}", flush=True)
                    print(f"TASK_ERROR={task.get('error')}", flush=True)
                    print(f"AUDIT_EVENTS={names}", flush=True)
                    if task["status"] == "succeeded":
                        result = task["result"]
                        print(f"ANSWER={result.get('answer')}", flush=True)
                        print(f"SOURCES={result.get('sources')}", flush=True)
                        assert "14 June 2031" in result["answer"]
                        assert any(
                            s["tool"] == "read_workspace_file"
                            and s["locator"] == "workspace:facts.txt"
                            and f"[{s['id']}]" in result["answer"]
                            for s in result["sources"]
                        )
                        assert "research_started" in names
                        assert "research_completed" in names
                        print("RESEARCH_HTTP_NATIVE_MODEL_AND_CITATION=PASS", flush=True)
                    else:
                        raise AssertionError(f"Research task failed: {task.get('error')}")
            finally:
                server.should_exit = True
                await serving
    await inspect_model()
    print("PRIVATE_LOOPBACK_CLEANUP=PASS", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-local-research", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_local_research:
        parser.error("Explicit local Research qualification flag required")
    asyncio.run(run())
