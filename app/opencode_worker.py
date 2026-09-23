"""Production OpenCode v2 HTTP adapter using a trusted local Ollama provider."""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from app.config import ROOT, Settings
from app.developer_artifacts import PinnedExecutable, pin_executable
from app.developer_contracts import (
    DeveloperRequest,
    ReviewerExecutionRequest,
    WorkerArtifactIdentity,
    WorkerIdentity,
    WorkerResult,
)
from app.developer_process import DeveloperError, environment, run_process, stop_process
from app.developer_sandbox import REPO, WORKER_EXECUTABLE, external_worker_sandbox
from app.worker_authority import (
    WorkerAuthorityProfile,
    authority_profile,
    authority_snapshot_matches,
    opencode_permission_semantics,
)

ADAPTER_SCHEMA = "opencode-zelo-v2"
MIN_CLI_VERSION = (2, 0, 0)
MAX_CLI_VERSION_EXCLUSIVE = (3, 0, 0)
CAPABILITY_PROFILE = "opencode-v2-http-ollama-inline-policy-v4"
MAX_STEPS = 12
COMPACTION_KEEP_TOKENS = 2000
COMPACTION_BUFFER_TOKENS = 1024
SERVER_START_SECONDS = 5.0
API_CALL_SECONDS = 5.0
API_RESPONSE_BYTES = 1 << 20
SERVER_USERNAME = "opencode"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:\s|$)")
_SERVER_URL = re.compile(rb"http://127\.0\.0\.1:(\d+)\b")
PROJECT_CONFIG_NAMES = ("opencode.json", "opencode.jsonc", ".opencode")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def configuration_version(settings: Settings) -> str:
    payload = {
        "schema": ADAPTER_SCHEMA,
        "model": settings.developer_opencode_model,
        "ollama_url": settings.ollama_url,
        "transport": "authenticated-local-http",
        "server_hostname": "127.0.0.1",
        "server_auth": "basic-random-per-run",
        "server_username": SERVER_USERNAME,
        "server_start_seconds": SERVER_START_SECONDS,
        "api_call_seconds": API_CALL_SECONDS,
        "api_response_bytes": API_RESPONSE_BYTES,
        "project_config": "disabled",
        "max_steps": MAX_STEPS,
        "compaction": {
            "auto": False,
            "keep_tokens": COMPACTION_KEEP_TOKENS,
            "buffer_tokens": COMPACTION_BUFFER_TOKENS,
        },
        "formatter": False,
        "snapshots": False,
        "mcp_servers": "none",
    }
    return hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()


def parse_cli_version(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", "strict").strip()
    except UnicodeError:
        raise DeveloperError("opencode_version_invalid") from None
    match = _VERSION.match(value)
    if match is None:
        raise DeveloperError("opencode_version_invalid")
    parsed = tuple(int(part) for part in match.groups())
    if not MIN_CLI_VERSION <= parsed < MAX_CLI_VERSION_EXCLUSIVE:
        raise DeveloperError("opencode_version_unsupported")
    return ".".join(match.groups())


def assert_project_config_absent(repo: Path) -> None:
    """Reject project configuration that could widen the trusted inline policy."""
    for name in PROJECT_CONFIG_NAMES:
        candidate = repo / name
        if candidate.exists() or candidate.is_symlink():
            raise DeveloperError("opencode_project_configuration_forbidden")


def request_authority(
    request: DeveloperRequest | ReviewerExecutionRequest,
) -> WorkerAuthorityProfile:
    selection = request.worker_selection
    if not authority_snapshot_matches(
        selection.descriptor.authority,
        selection.authority_profile,
        selection.authority_policy_version,
    ):
        raise DeveloperError("worker_authority_changed")
    try:
        return authority_profile(selection.descriptor.authority)
    except ValueError:
        raise DeveloperError("worker_authority_unsupported") from None


def _model_ref(settings: Settings) -> dict[str, str]:
    provider, separator, model = settings.developer_opencode_model.partition("/")
    if separator != "/" or provider != "ollama" or not model:
        raise DeveloperError("opencode_model_invalid")
    return {"providerID": provider, "id": model}


def inline_config(settings: Settings, profile: WorkerAuthorityProfile) -> dict[str, object]:
    semantics = opencode_permission_semantics(profile)
    return {
        "model": settings.developer_opencode_model,
        "providers": {
            "ollama": {
                "settings": {
                    "baseURL": settings.ollama_url + "/v1",
                }
            }
        },
        "permissions": semantics["permissions"],
        "experimental": {"policies": semantics["policies"]},
        "compaction": {
            "auto": False,
            "keep": {"tokens": COMPACTION_KEEP_TOKENS},
            "buffer": COMPACTION_BUFFER_TOKENS,
        },
        "plugins": [],
        "skills": [],
        "commands": {},
        "formatter": False,
        "snapshots": False,
        "mcp": {"servers": {}},
        "agents": {"build": {"steps": MAX_STEPS}},
    }


def parse_text_output(data: bytes) -> str:
    if not data:
        raise DeveloperError("opencode_output_invalid")
    try:
        summary = data.decode("utf-8", "strict").strip()
    except UnicodeError:
        raise DeveloperError("opencode_output_invalid") from None
    if not 1 <= len(summary) <= 4096:
        raise DeveloperError("opencode_output_invalid")
    return summary


def parse_server_url(line: bytes) -> str:
    match = _SERVER_URL.search(line)
    if match is None:
        raise DeveloperError("opencode_server_start_failed")
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        raise DeveloperError("opencode_server_start_failed")
    return f"http://127.0.0.1:{port}"


def _unwrap_data(value: object) -> object:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def parse_assistant_summary(payload: object) -> str:
    messages = _unwrap_data(payload)
    if not isinstance(messages, list):
        raise DeveloperError("opencode_protocol_invalid")

    assistant: dict[str, object] | None = None
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "assistant":
            assistant = item
            break
        info = item.get("info")
        if isinstance(info, dict) and info.get("role") == "assistant":
            assistant = item
            break

    if assistant is None:
        raise DeveloperError("opencode_output_invalid")

    if assistant.get("type") == "assistant":
        error = assistant.get("error")
        if error is not None:
            raise DeveloperError("opencode_model_error")
        direct_text = assistant.get("text")
        if isinstance(direct_text, str) and direct_text.strip():
            return parse_text_output(direct_text.encode("utf-8"))
        parts = assistant.get("content", [])
    else:
        info = assistant.get("info")
        if isinstance(info, dict) and info.get("error") is not None:
            raise DeveloperError("opencode_model_error")
        parts = assistant.get("parts", [])

    if not isinstance(parts, list):
        raise DeveloperError("opencode_protocol_invalid")
    text_parts = [
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    text = "\n".join(part for part in text_parts if isinstance(part, str) and part.strip())
    return parse_text_output(text.encode("utf-8"))


def _basic_auth(password: str) -> str:
    token = base64.b64encode(f"{SERVER_USERNAME}:{password}".encode("ascii")).decode("ascii")
    return "Basic " + token


def _api_json(
    base_url: str,
    password: str,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    timeout: float = API_CALL_SECONDS,
) -> object | None:
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": _basic_auth(password),
    }
    if payload is not None:
        body = _canonical_json(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base_url + path, data=body, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=max(0.1, timeout)) as response:
            data = response.read(API_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise DeveloperError("opencode_api_error", exit_code=exc.code) from None
    except TimeoutError:
        raise DeveloperError("opencode_api_timeout") from None
    except (URLError, OSError):
        raise DeveloperError("opencode_api_unavailable") from None
    if len(data) > API_RESPONSE_BYTES:
        raise DeveloperError("opencode_api_response_limit")
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError):
        raise DeveloperError("opencode_protocol_invalid") from None


async def _api_json_async(
    base_url: str,
    password: str,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    timeout: float = API_CALL_SECONDS,
) -> object | None:
    return await asyncio.to_thread(
        _api_json,
        base_url,
        password,
        method,
        path,
        payload=payload,
        timeout=timeout,
    )


async def _drain(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(4096):
        pass


class OpenCodeWorker:
    def __init__(self, settings: Settings, pinned: PinnedExecutable | None = None):
        self.settings = settings
        self.pinned = pinned

    @contextmanager
    def bind(self, approved: WorkerIdentity | None = None):
        configuration = self.settings.model_copy(deep=True)
        approved_executable = None
        if approved is not None:
            try:
                approved_executable = approved.artifact("executable")
            except ValueError:
                raise DeveloperError("worker_configuration_changed") from None
            if approved.credential_locations:
                raise DeveloperError("worker_configuration_changed")
        with pin_executable(
            configuration.developer_opencode_executable, approved_executable
        ) as artifact:
            yield OpenCodeWorker(configuration, artifact)

    def artifact(self) -> PinnedExecutable:
        if self.pinned is None:
            raise DeveloperError("worker_artifact_not_pinned")
        self.pinned.verify()
        return self.pinned

    def identity(self) -> WorkerIdentity:
        return asyncio.run(self.preflight())

    async def preflight(self) -> WorkerIdentity:
        if self.pinned is None:
            with self.bind() as bound:
                return await bound.preflight()
        binary = self.artifact()
        version_result = await run_process(
            external_worker_sandbox(
                None,
                [WORKER_EXECUTABLE, "--version"],
                worker_fd=binary.fd,
                writable=False,
                service_network=False,
            ),
            cwd=ROOT,
            timeout=5,
            pass_fds=(binary.fd,),
        )
        if version_result.exit_code:
            raise DeveloperError("opencode_preflight_failed")
        version = parse_cli_version(version_result.stdout)

        serve_help = await run_process(
            external_worker_sandbox(
                None,
                [WORKER_EXECUTABLE, "serve", "--help"],
                worker_fd=binary.fd,
                writable=False,
                service_network=False,
            ),
            cwd=ROOT,
            timeout=5,
            pass_fds=(binary.fd,),
        )
        if serve_help.exit_code:
            raise DeveloperError("opencode_preflight_failed")
        try:
            help_text = serve_help.stdout.decode("utf-8", "strict")
        except UnicodeError:
            raise DeveloperError("opencode_preflight_failed") from None
        if "--hostname" not in help_text or "--port" not in help_text:
            raise DeveloperError("opencode_capabilities_unsupported")

        return WorkerIdentity(
            implementation="opencode",
            config_version=configuration_version(self.settings),
            cli_version=version,
            capability_profile=CAPABILITY_PROFILE,
            artifacts=(
                WorkerArtifactIdentity(
                    name="executable",
                    path=str(binary.source),
                    sha256=binary.digest,
                ),
            ),
            credential_locations=(),
        )

    def server_argv(self, repo: Path, profile: WorkerAuthorityProfile) -> list[str]:
        binary = self.artifact()
        command = [
            WORKER_EXECUTABLE,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            "0",
        ]
        return external_worker_sandbox(
            repo,
            command,
            worker_fd=binary.fd,
            writable=profile.workspace_write,
            service_network=True,
        )

    def _server_environment(
        self,
        profile: WorkerAuthorityProfile,
        password: str,
    ) -> dict[str, str]:
        config = inline_config(self.settings, profile)
        return environment() | {
            "OPENCODE_CONFIG_CONTENT": _canonical_json(config),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_PRUNE": "1",
            "OPENCODE_DISABLE_TERMINAL_TITLE": "1",
            "OPENCODE_SERVER_USERNAME": SERVER_USERNAME,
            "OPENCODE_SERVER_PASSWORD": password,
        }

    @asynccontextmanager
    async def _managed_server(
        self,
        repo: Path,
        profile: WorkerAuthorityProfile,
        expected_version: str,
    ):
        binary = self.artifact()
        password = secrets.token_urlsafe(32)
        try:
            process = await asyncio.create_subprocess_exec(
                *self.server_argv(repo, profile),
                cwd=repo,
                env=self._server_environment(profile, password),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                pass_fds=(binary.fd,),
                limit=8192,
            )
        except OSError:
            raise DeveloperError("executable_unavailable") from None

        drain_stdout: asyncio.Task | None = None
        drain_stderr = asyncio.create_task(_drain(process.stderr))
        try:
            base_url = None
            async with asyncio.timeout(SERVER_START_SECONDS):
                while base_url is None:
                    line = await process.stdout.readline()
                    if not line:
                        raise DeveloperError("opencode_server_start_failed")
                    if len(line) > 4096:
                        raise DeveloperError("opencode_server_start_failed")
                    match = _SERVER_URL.search(line)
                    if match is not None:
                        base_url = parse_server_url(line)
            drain_stdout = asyncio.create_task(_drain(process.stdout))

            health = await _api_json_async(
                base_url,
                password,
                "GET",
                "/api/info",
                timeout=API_CALL_SECONDS,
            )
            health_data = _unwrap_data(health)
            if not isinstance(health_data, dict) or health_data.get("version") != expected_version:
                raise DeveloperError("opencode_server_health_invalid")
            yield base_url, password
        except TimeoutError:
            raise DeveloperError("opencode_server_start_failed") from None
        finally:
            await stop_process(process)
            for task in (drain_stdout, drain_stderr):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (drain_stdout, drain_stderr) if task is not None),
                return_exceptions=True,
            )

    async def _create_session(
        self,
        base_url: str,
        password: str,
        role: str,
    ) -> str:
        response = await _api_json_async(
            base_url,
            password,
            "POST",
            "/api/session",
            payload={
                "title": f"Zelo OpenCode {role}",
                "agent": "build",
                "model": _model_ref(self.settings),
            },
        )
        data = _unwrap_data(response)
        if not isinstance(data, dict):
            raise DeveloperError("opencode_protocol_invalid")
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id.startswith("ses_"):
            raise DeveloperError("opencode_protocol_invalid")
        location = data.get("location")
        if isinstance(location, dict):
            directory = location.get("directory")
            if directory is not None and directory != REPO:
                raise DeveloperError("opencode_workspace_mismatch")
        return session_id

    async def _interrupt(self, base_url: str, password: str, session_id: str) -> None:
        try:
            await _api_json_async(
                base_url,
                password,
                "POST",
                f"/api/session/{session_id}/interrupt",
                timeout=2,
            )
        except DeveloperError:
            pass

    async def _run_session(
        self,
        base_url: str,
        password: str,
        session_id: str,
        prompt: str,
        timeout: float,
    ) -> tuple[str, int]:
        deadline = time.monotonic() + timeout

        def remaining(*, cap: float | None = None) -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise DeveloperError("opencode_timeout")
            return max(0.1, min(value, cap)) if cap is not None else max(0.1, value)

        await _api_json_async(
            base_url,
            password,
            "POST",
            f"/api/session/{session_id}/prompt",
            payload={"text": prompt},
            timeout=remaining(cap=API_CALL_SECONDS),
        )
        try:
            await _api_json_async(
                base_url,
                password,
                "POST",
                f"/api/experimental/session/{session_id}/wait",
                timeout=remaining(),
            )
        except DeveloperError as exc:
            if exc.args and exc.args[0] == "opencode_api_timeout":
                raise DeveloperError("opencode_timeout") from None
            raise

        session = _unwrap_data(
            await _api_json_async(
                base_url,
                password,
                "GET",
                f"/api/session/{session_id}",
                timeout=remaining(cap=API_CALL_SECONDS),
            )
        )
        if not isinstance(session, dict):
            raise DeveloperError("opencode_protocol_invalid")
        if session.get("outcome") == "failed":
            raise DeveloperError("opencode_session_failed")

        messages = await _api_json_async(
            base_url,
            password,
            "GET",
            f"/api/session/{session_id}/message",
            timeout=remaining(cap=API_CALL_SECONDS),
        )
        message_data = _unwrap_data(messages)
        if not isinstance(message_data, list):
            raise DeveloperError("opencode_protocol_invalid")
        return parse_assistant_summary(messages), len(message_data)

    async def run(
        self, request: DeveloperRequest | ReviewerExecutionRequest, repo: Path
    ) -> WorkerResult:
        if self.pinned is None:
            with self.bind(request.worker) as bound:
                return await bound.run(request, repo)
        binary = self.artifact()
        try:
            approved_executable = request.worker.artifact("executable")
        except ValueError:
            raise DeveloperError("worker_configuration_changed") from None
        if request.worker.credential_locations:
            raise DeveloperError("worker_configuration_changed")
        if (
            str(binary.source) != approved_executable.path
            or binary.digest != approved_executable.sha256
            or request.worker.config_version != configuration_version(self.settings)
        ):
            raise DeveloperError("worker_configuration_changed")

        role = request.worker_selection.descriptor.role
        if role not in {"developer", "reviewer"}:
            raise DeveloperError("worker_role_mismatch")
        assert_project_config_absent(repo)
        profile = request_authority(request)

        if role == "developer":
            prompt = (
                "You are Zelo's subordinate OpenCode coding worker. Work only inside the current "
                "isolated checkout. Make only the requested file edits. Shell access is limited "
                "to Zelo-approved read-only Git inspection commands; all other shell commands, "
                "web access, subagents, skills, and external-directory access are blocked. "
                "Do not commit, push, merge, tag, publish, or modify worker configuration. "
                "Zelo runs tests through trusted validation after you finish. Return a concise "
                "summary. "
                "Exact instruction:\n" + request.instruction
            )
        else:
            prompt = (
                "You are Zelo's independent read-only OpenCode reviewer. Inspect only the current "
                "isolated checkout. Editing is blocked. Shell access is limited to Zelo-approved "
                "read-only Git inspection commands; web access, subagents, skills, and external-"
                "directory access are blocked. Return exactly the JSON "
                "review object requested by the instruction, with no markdown.\nExact "
                "instruction:\n" + request.instruction
            )

        started_at = datetime.now(UTC)
        monotonic_started = time.monotonic()
        event_count = 0
        async with self._managed_server(repo, profile, request.worker.cli_version) as (
            base_url,
            password,
        ):
            session_id = await self._create_session(base_url, password, role)
            try:
                elapsed = time.monotonic() - monotonic_started
                remaining = max(0.1, request.limits.worker_seconds - elapsed)
                summary, event_count = await self._run_session(
                    base_url,
                    password,
                    session_id,
                    prompt,
                    remaining,
                )
            finally:
                await self._interrupt(base_url, password, session_id)

        return WorkerResult(
            exit_code=0,
            summary=summary,
            event_count=event_count,
            usage={},
            nonfatal_error_items=0,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_seconds=time.monotonic() - monotonic_started,
        )
