"""Opt-in real localhost smoke test: python -m scripts.smoke_agent."""

import asyncio
import json
import secrets
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn

from app.config import ROOT, Settings
from app.database import Database
from app.gateway import OllamaProvider
from app.main import create_app
from app.tools import ToolGateway

MODEL = "qwen3:4b-instruct"
PROMPT = "Check the current Zelo repository status and tell me what branch I am on."


async def smoke(settings: Settings) -> None:
    provider = OllamaProvider("http://127.0.0.1:11434")
    if MODEL not in await provider.models():
        raise RuntimeError("Required local model is unavailable; no model was downloaded")
    database = Database(settings.database_path)
    try:
        database.migrate()
    finally:
        database.close()
    expected_branch = ToolGateway(settings).execute("repo_status", {})["branch"]
    application = create_app(settings, provider=provider)
    server = uvicorn.Server(uvicorn.Config(application, log_level="warning", access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        await serving
                        raise RuntimeError("Smoke server did not start")
                    await asyncio.sleep(0.01)
            token = settings.operator_token.get_secret_value()
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
                trust_env=False,
            ) as client:
                response = await client.post(
                    "/tasks",
                    json={
                        "kind": "agent_run",
                        "payload": {"prompt": PROMPT, "profile": "general"},
                    },
                )
                response.raise_for_status()
                task_id = response.json()["id"]
                async with asyncio.timeout(settings.agent_timeout_seconds + 10):
                    while True:
                        response = await client.get(f"/tasks/{task_id}")
                        response.raise_for_status()
                        task = response.json()
                        if task["status"] not in {"pending", "running"}:
                            break
                        await asyncio.sleep(0.2)
                response = await client.get(f"/tasks/{task_id}/events")
                response.raise_for_status()
                events = [
                    {"event": event["event"], "details": event["details"]}
                    for event in response.json()
                    if event["event"].startswith("agent_")
                ]
                print(
                    json.dumps(
                        {
                            "status": task["status"],
                            "error": task["error"],
                            "result": task["result"],
                            "events": events,
                        },
                        indent=2,
                    )
                )
                assert task["status"] == "succeeded", "Agent smoke task failed"
                result = task["result"]
                assert result["alias"] == "local_general" and result["actual_model"] == MODEL
                assert result["tools_used"] == ["repo_status"] and result["tool_calls"] == 1
                assert expected_branch in result["final_response"]
                assert task["attempts"][0]["result"] == result
                assert [event["event"] for event in events].count("agent_tool_completed") == 1
                assert events[-1]["event"] == "agent_completed"
        finally:
            server.should_exit = True
            await serving


def main() -> None:
    (ROOT / "runtime").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="smoke-agent-", dir=ROOT / "runtime") as directory:
        settings = Settings(
            _env_file=None,
            operator_token=secrets.token_urlsafe(32),
            runtime_dir=Path(directory),
            ollama_general_model=MODEL,
            ollama_url="http://127.0.0.1:11434",
        )
        asyncio.run(smoke(settings))


if __name__ == "__main__":
    main()
