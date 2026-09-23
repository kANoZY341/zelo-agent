"""Real Bubblewrap boundary proof for the managed OpenCode HTTP transport."""

import asyncio

import pytest

from app.developer_sandbox import BWRAP
from app.opencode_worker import OpenCodeWorker
from app.worker_authority import authority_profile

FAKE_OPENCODE = r"""#!/usr/bin/python3
import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if "--version" in sys.argv:
    print("2.0.14")
    raise SystemExit(0)

if len(sys.argv) > 1 and sys.argv[1] == "serve" and "--help" in sys.argv:
    print("--hostname string")
    print("--port number")
    raise SystemExit(0)

if len(sys.argv) < 2 or sys.argv[1] != "serve":
    raise SystemExit(2)

config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
if os.environ.get("OPENCODE_DISABLE_PROJECT_CONFIG") != "1":
    raise SystemExit(3)
if config.get("compaction", {}).get("auto") is not False:
    raise SystemExit(4)

username = os.environ.get("OPENCODE_SERVER_USERNAME")
password = os.environ.get("OPENCODE_SERVER_PASSWORD")
if username != "opencode" or not password:
    raise SystemExit(5)

expected_auth = "Basic " + base64.b64encode(
    f"{username}:{password}".encode("ascii")
).decode("ascii")


def option(name, default):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return default


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def authorized(self):
        return self.headers.get("Authorization") == expected_auth

    def send_json(self, status, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return None
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def guard(self):
        if self.authorized():
            return True
        self.send_json(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if not self.guard():
            return
        if self.path == "/api/info":
            self.send_json(200, {"version": "2.0.14"})
            return
        if self.path == "/api/session/ses_fake":
            self.send_json(
                200,
                {
                    "data": {
                        "id": "ses_fake",
                        "outcome": "success",
                        "location": {"directory": os.getcwd()},
                    }
                },
            )
            return
        if self.path == "/api/session/ses_fake/message":
            self.send_json(
                200,
                {
                    "data": [
                        {
                            "type": "assistant",
                            "id": "msg_fake",
                            "content": [
                                {"type": "text", "id": "prt_fake", "text": "fake done"}
                            ],
                        }
                    ]
                },
            )
            return
        self.send_json(404, {"error": "missing"})

    def do_POST(self):
        if not self.guard():
            return
        body = self.read_json()
        if self.path == "/api/session":
            if (
                not isinstance(body, dict)
                or body.get("agent") != "build"
                or body.get("model", {}).get("providerID") != "ollama"
            ):
                self.send_json(400, {"error": "bad session"})
                return
            self.send_json(
                200,
                {
                    "data": {
                        "id": "ses_fake",
                        "location": {"directory": os.getcwd()},
                    }
                },
            )
            return
        if self.path == "/api/session/ses_fake/prompt":
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                self.send_json(400, {"error": "bad prompt"})
                return
            self.send_json(200, {"accepted": True})
            return
        if self.path == "/api/experimental/session/ses_fake/wait":
            self.send_json(200, {"idle": True})
            return
        if self.path == "/api/session/ses_fake/interrupt":
            self.send_json(200, {"interrupted": False})
            return
        self.send_json(404, {"error": "missing"})


host = option("--hostname", "127.0.0.1")
port = int(option("--port", "0"))
server = ThreadingHTTPServer((host, port), Handler)
print(f"server listening on http://{host}:{server.server_address[1]}", flush=True)
server.serve_forever()
"""


@pytest.mark.developer_boundary
def test_opencode_managed_http_transport_crosses_real_sandbox(settings, tmp_path):
    if not BWRAP.is_file():
        pytest.skip("Linux Bubblewrap prerequisite not installed")

    binary = settings.runtime_dir / "fake-opencode-http"
    binary.write_text(FAKE_OPENCODE)
    binary.chmod(0o700)
    configured = settings.model_copy(
        update={
            "developer_opencode_executable": binary,
            "developer_opencode_model": "ollama/qwen3:4b-instruct",
        }
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    git = repo / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\nrepositoryformatversion = 0\n")

    worker = OpenCodeWorker(configured)
    identity = asyncio.run(worker.preflight())
    profile = authority_profile(("workspace-write",))

    async def exercise():
        with worker.bind(identity) as bound:
            async with bound._managed_server(
                repo,
                profile,
                identity.cli_version,
            ) as (base_url, password):
                assert base_url.startswith("http://127.0.0.1:")
                assert password
                session_id = await bound._create_session(base_url, password, "developer")
                try:
                    return await bound._run_session(
                        base_url,
                        password,
                        session_id,
                        "Return a bounded summary.",
                        5,
                    )
                finally:
                    await bound._interrupt(base_url, password, session_id)

    summary, event_count = asyncio.run(exercise())
    assert summary == "fake done"
    assert event_count == 1
