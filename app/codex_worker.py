"""Supported Codex CLI adapter. Local probes precede any model execution."""

import asyncio
import hashlib
import json
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT, Settings
from app.developer_artifacts import PinnedExecutable, pin_artifact, pin_executable
from app.developer_contracts import DeveloperRequest, Limits, WorkerIdentity, WorkerResult
from app.developer_process import DeveloperError, environment, run_process
from app.developer_sandbox import CODE_MODE_HOST, CODEX, ENGINE, REPO, sandbox

CLI_VERSION = "codex-cli 0.155.1"
SUPPORTED_CLI_VERSIONS = frozenset({"codex-cli 0.154.0", CLI_VERSION})
CONFIG_VERSION = "codex-zelo-v4-sealed"
CAPABILITIES = "0.154.0-0.155.1-named-permissions-auth-denial-v1"
LIFECYCLE_EVENTS = frozenset(
    {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "error",
        "item.started",
        "item.updated",
        "item.completed",
    }
)
ITEM_TYPES_BY_CLI_VERSION = {
    # 0.154.0 used the former plan-update item name. Retain its established
    # compatibility without accepting it from the current exec protocol.
    "codex-cli 0.154.0": frozenset(
        {
            "agent_message",
            "reasoning",
            "command_execution",
            "file_change",
            "mcp_tool_call",
            "web_search",
            "plan",
            "error",
        }
    ),
    CLI_VERSION: frozenset(
        {
            "agent_message",
            "reasoning",
            "command_execution",
            "file_change",
            "mcp_tool_call",
            "web_search",
            "todo_list",
            "error",
        }
    ),
}


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(65536):
            result.update(block)
    return result.hexdigest()


def toml_table(values: dict[str, str]) -> str:
    return "{" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in values.items()) + "}"


def command_config() -> list[str]:
    # A named workspace-write equivalent is required for explicit auth read denial.
    # Do not combine default_permissions with legacy sandbox_mode flags.
    filesystem = toml_table(
        {
            ":minimal": "read",
            "/opt/zelo-codex": "read",
            REPO: "write",
            REPO + "/.git": "read",
            ENGINE: "deny",
        }
    )
    overrides = [
        'default_permissions="zelo"',
        "permissions.zelo.filesystem=" + filesystem,
        "permissions.zelo.network.enabled=false",
        'approval_policy="never"',
        'web_search="disabled"',
        'model_reasoning_summary="none"',
        "show_raw_agent_reasoning=false",
        "allow_login_shell=false",
        'shell_environment_policy.inherit="none"',
        "shell_environment_policy.set=" + toml_table(environment()),
        "shell_environment_policy.experimental_use_profile=false",
        'projects."/workspace".trust_level="untrusted"',
    ]
    for feature in (
        "apps",
        "plugins",
        "remote_plugin",
        "hooks",
        "multi_agent",
        "browser_use",
        "computer_use",
        "image_generation",
        "shell_snapshot",
        "workspace_dependencies",
        "skill_mcp_dependency_install",
        "skill_search",
        "network_proxy",
        "guardian_approval",
    ):
        overrides.append(f"features.{feature}=false")
    overrides.append("features.skip_host_skill_discovery=true")
    return [part for value in overrides for part in ("-c", value)]


# Run by the installed sandbox subcommand, never sent to a model. No credentials read.
BOUNDARY_PROBE = """import json, os, socket
from pathlib import Path
denied = True
paths = ["/tmp/zelo-codex-engine/auth.json"]
paths += [str(p / "root/tmp/zelo-codex-engine/auth.json") for p in Path('/proc').iterdir()
          if p.name.isdecimal()]
for path in paths:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        pass
    else:
        os.close(fd)
        denied = False
try:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except OSError:
    network_denied = True
else:
    connection.close()
    network_denied = False
print(json.dumps({"auth_denied": denied, "network_denied": network_denied,
                  "workspace": Path.cwd() == Path("/workspace"),
                  "secret_env_absent": "ZELO_OPERATOR_TOKEN" not in os.environ}))
raise SystemExit(0 if denied and network_denied else 23)
"""

LOCAL_COMMAND_PROBE = """import json, os, socket, subprocess
from pathlib import Path
host = Path('/opt/zelo-codex/codex-code-mode-host')
host_ok = host.is_file() and os.access(host, os.X_OK)
if host_ok:
    host_ok = subprocess.run([str(host), '--help'], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, check=False).returncode == 0
Path('DEVELOPER_SMOKE.txt').write_text('Zelo developer worker online\\n', encoding='utf-8')
try:
    Path('/tmp/zelo-codex-engine/auth.json').stat()
except OSError:
    auth_denied = True
else:
    auth_denied = False
primary_absent = not Path(PRIMARY_PATH).exists()
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except OSError:
    network_denied = True
else:
    network_denied = False
Path('/tmp/zelo-command-probe').write_text('x')
print(json.dumps({'host_ok': host_ok, 'auth_denied': auth_denied,
                  'network_denied': network_denied,
                  'primary_absent': primary_absent, 'workspace': Path.cwd() == Path('/workspace')}))
raise SystemExit(0 if host_ok and auth_denied and network_denied and primary_absent else 23)
"""


def parse_jsonl(
    data: bytes, limits: Limits, cli_version: str = CLI_VERSION
) -> tuple[str, int, dict[str, int], int]:
    if not data or not data.endswith(b"\n") or len(data) > limits.stdout_bytes:
        raise DeveloperError("codex_jsonl_incomplete")
    try:
        lines = data.decode("utf-8", "strict").splitlines()
    except UnicodeError:
        raise DeveloperError("codex_jsonl_invalid") from None
    if len(lines) > limits.events:
        raise DeveloperError("jsonl_event_limit")
    summary = None
    terminal = False
    last_completed_item_type = None
    usage = {}
    nonfatal_error_items = 0
    item_types = ITEM_TYPES_BY_CLI_VERSION.get(cli_version)
    if item_types is None:
        raise DeveloperError("codex_version_unsupported")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-finite JSON number")

    def structural_type(value) -> str:
        if (
            isinstance(value, str)
            and 1 <= len(value) <= 64
            and value.isascii()
            and all(character.isalnum() or character in "._-" for character in value)
        ):
            return value
        return "invalid"

    def unknown(event: dict) -> DeveloperError:
        fields = [f"event_type={structural_type(event.get('type'))}"]
        item = event.get("item")
        if isinstance(item, dict):
            fields.append(f"item_type={structural_type(item.get('type'))}")
        return DeveloperError("codex_event_unknown", diagnostic=" ".join(fields))

    for line in lines:
        if len(line.encode()) > limits.line_bytes:
            raise DeveloperError("jsonl_line_limit")
        try:
            event = json.loads(line, object_pairs_hook=pairs, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise DeveloperError("codex_jsonl_invalid") from None
        if not isinstance(event, dict) or terminal:
            raise DeveloperError("codex_jsonl_invalid")
        kind = event.get("type")
        if kind not in LIFECYCLE_EVENTS:
            raise unknown(event)
        if kind in {"error", "turn.failed"}:
            raise DeveloperError("codex_turn_failed")
        if kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise DeveloperError("codex_jsonl_invalid")
            item_type = item["type"]
            if item_type not in item_types:
                raise unknown(event)
            if item_type == "error":
                # ErrorItem is a completed, non-fatal notification. Its body
                # remains private; retain only a bounded count in the result.
                if kind != "item.completed":
                    raise DeveloperError("codex_jsonl_invalid")
                if (
                    not isinstance(item.get("id"), str)
                    or not 1 <= len(item["id"]) <= 256
                    or not isinstance(item.get("message"), str)
                ):
                    raise DeveloperError("codex_jsonl_invalid")
                nonfatal_error_items += 1
            # Item bodies are discarded. Only the final completed agent message
            # is retained; reasoning and command output never cross this boundary.
            if kind == "item.completed":
                last_completed_item_type = item_type
            if kind == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 4096:
                    raise DeveloperError("codex_summary_invalid")
                try:
                    text.encode("utf-8", "strict")
                except UnicodeError:
                    raise DeveloperError("codex_summary_invalid") from None
                summary = text
        elif kind == "turn.completed":
            terminal = True
            raw_usage = event.get("usage", {})
            if not isinstance(raw_usage, dict):
                raise DeveloperError("codex_jsonl_invalid")
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                if key in raw_usage:
                    value = raw_usage[key]
                    if type(value) is not int or value < 0:
                        raise DeveloperError("codex_jsonl_invalid")
                    usage[key] = value
    if not terminal or summary is None or last_completed_item_type != "agent_message":
        raise DeveloperError("codex_final_missing")
    return summary, len(lines), usage, nonfatal_error_items


class CodexCliWorker:
    def __init__(
        self,
        settings: Settings,
        pinned: PinnedExecutable | None = None,
        code_mode_host: PinnedExecutable | None = None,
    ):
        self.settings = settings
        self.pinned = pinned
        self.code_mode_host = code_mode_host

    @contextmanager
    def bind(self, approved: WorkerIdentity | None = None):
        configuration = self.settings.model_copy(deep=True)
        with ExitStack() as artifacts:
            artifact = artifacts.enter_context(
                pin_executable(configuration.developer_codex_executable, approved)
            )
            host_path = artifact.source.parent / "codex-code-mode-host"
            host = artifacts.enter_context(
                pin_artifact(
                    host_path,
                    approved_path=approved.code_mode_host if approved else None,
                    approved_digest=approved.code_mode_host_sha256 if approved else None,
                    name="zelo-approved-codex-code-mode-host",
                )
            )
            yield CodexCliWorker(configuration, artifact, host)

    def artifact(self) -> PinnedExecutable:
        if self.pinned is None or self.code_mode_host is None:
            raise DeveloperError("worker_artifact_not_pinned")
        self.pinned.verify()
        self.code_mode_host.verify()
        return self.pinned

    def host_artifact(self) -> PinnedExecutable:
        self.artifact()
        assert self.code_mode_host is not None
        return self.code_mode_host

    def identity(self) -> WorkerIdentity:
        return asyncio.run(self.preflight())

    async def preflight(self) -> WorkerIdentity:
        if self.pinned is None:
            with self.bind() as bound:
                return await bound.preflight()
        binary = self.artifact()
        outputs = []
        for arguments in (["--version"], ["--help"], ["exec", "--help"], ["sandbox", "--help"]):
            result = await run_process(
                sandbox(
                    None,
                    [CODEX, *arguments],
                    codex_fd=self.artifact().fd,
                    code_mode_host_fd=self.host_artifact().fd,
                ),
                cwd=ROOT,
                timeout=5,
                pass_fds=(binary.fd, self.host_artifact().fd),
            )
            if result.exit_code:
                raise DeveloperError("codex_preflight_failed")
            outputs.append(result.stdout.decode("utf-8", "strict"))
        host_result = await run_process(
            sandbox(
                None,
                [CODE_MODE_HOST, "--help"],
                codex_fd=self.artifact().fd,
                code_mode_host_fd=self.host_artifact().fd,
            ),
            cwd=ROOT,
            timeout=5,
            pass_fds=(binary.fd, self.host_artifact().fd),
        )
        if host_result.exit_code:
            raise DeveloperError("code_mode_host_unavailable")
        version = outputs[0].strip()
        if version not in SUPPORTED_CLI_VERSIONS:
            raise DeveloperError("codex_version_unsupported")
        if (
            not all(
                flag in outputs[2]
                for flag in (
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--ephemeral",
                    "--json",
                    "--strict-config",
                    "workspace-write",
                )
            )
            or "--ask-for-approval" not in outputs[1]
            or "--permission-profile" not in outputs[3]
        ):
            raise DeveloperError("codex_capabilities_unsupported")
        identity = WorkerIdentity(
            implementation="codex",
            config_version=CONFIG_VERSION,
            cli_version=version,
            capability_profile=CAPABILITIES,
            executable=str(binary.source),
            executable_sha256=binary.digest,
            code_mode_host=str(self.host_artifact().source),
            code_mode_host_sha256=self.host_artifact().digest,
            auth_location=str(self.settings.developer_codex_auth_file.absolute()),
        )
        return identity

    async def probe(self, repo: Path, auth: Path) -> None:
        if self.pinned is None:
            with self.bind() as bound:
                return await bound.probe(repo, auth)
        args = [
            CODEX,
            *command_config(),
            "sandbox",
            "-P",
            "zelo",
            "-C",
            REPO,
            "--",
            "/usr/bin/python3",
            "-I",
            "-c",
            BOUNDARY_PROBE,
        ]
        env = environment() | {"CODEX_HOME": ENGINE}
        result = await run_process(
            sandbox(
                repo,
                args,
                codex_fd=self.artifact().fd,
                code_mode_host_fd=self.host_artifact().fd,
                auth=auth,
                service_network=True,
            ),
            cwd=repo,
            timeout=15,
            env=env,
            pass_fds=(self.artifact().fd, self.host_artifact().fd),
        )
        expected = {
            "auth_denied": True,
            "network_denied": True,
            "workspace": True,
            "secret_env_absent": True,
        }
        try:
            valid = result.exit_code == 0 and json.loads(result.stdout) == expected
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise DeveloperError("codex_command_boundary_unverified")

    async def local_command_probe(self, repo: Path, auth: Path, primary: Path = ROOT) -> None:
        """Exercise the installed Codex command sandbox without a model request."""
        args = [
            CODEX,
            *command_config(),
            "sandbox",
            "-P",
            "zelo",
            "-C",
            REPO,
            "--",
            "/usr/bin/python3",
            "-I",
            "-c",
            LOCAL_COMMAND_PROBE.replace("PRIMARY_PATH", repr(str(primary))),
        ]
        result = await run_process(
            sandbox(
                repo,
                args,
                codex_fd=self.artifact().fd,
                code_mode_host_fd=self.host_artifact().fd,
                auth=auth,
                service_network=True,
            ),
            cwd=repo,
            timeout=15,
            env=environment() | {"CODEX_HOME": ENGINE},
            pass_fds=(self.artifact().fd, self.host_artifact().fd),
        )
        host_tmp_marker = Path("/tmp/zelo-command-probe")
        try:
            valid = (
                result.exit_code == 0
                and json.loads(result.stdout)
                == {
                    "host_ok": True,
                    "auth_denied": True,
                    "network_denied": True,
                    "primary_absent": True,
                    "workspace": True,
                }
                and not host_tmp_marker.exists()
            )
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise DeveloperError("codex_command_boundary_unverified")

    def argv(self, repo: Path) -> list[str]:
        return sandbox(
            repo,
            [
                CODEX,
                "exec",
                "--strict-config",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--json",
                "-C",
                REPO,
                *command_config(),
                "-",
            ],
            codex_fd=self.artifact().fd,
            code_mode_host_fd=self.host_artifact().fd,
            auth=self.settings.developer_codex_auth_file,
            service_network=True,
        )

    async def run(self, request: DeveloperRequest, repo: Path) -> WorkerResult:
        if self.pinned is None:
            with self.bind(request.worker) as bound:
                return await bound.run(request, repo)
        binary = self.artifact()
        if (
            str(binary.source) != request.worker.executable
            or binary.digest != request.worker.executable_sha256
        ):
            raise DeveloperError("worker_configuration_changed")
        await self.probe(repo, self.settings.developer_codex_auth_file)
        prompt = (
            "You are Zelo's subordinate coding worker in a standalone isolated checkout. "
            "Read AGENTS.md; all work is confined to this checkout. Make only requested edits. "
            "Run useful tests when allowed. Do not commit, push, merge, rebase, tag, deploy, "
            "publish, enable network, access unrelated files, or modify worker configuration. "
            "Return a concise completion summary.\nExact instruction:\n" + request.instruction
        )
        started = datetime.now(UTC)
        limits = request.limits
        result = await run_process(
            self.argv(repo),
            cwd=repo,
            timeout=limits.worker_seconds,
            stdout_limit=limits.stdout_bytes,
            stderr_limit=limits.stderr_bytes,
            line_limit=limits.line_bytes,
            event_limit=limits.events,
            stdin=prompt.encode(),
            env=environment() | {"CODEX_HOME": ENGINE},
            pass_fds=(binary.fd, self.host_artifact().fd),
        )
        if result.exit_code:
            raise DeveloperError("codex_nonzero_exit", exit_code=result.exit_code)
        try:
            summary, count, usage, nonfatal_error_items = parse_jsonl(
                result.stdout, limits, request.worker.cli_version
            )
        except DeveloperError as error:
            error.exit_code = result.exit_code
            raise
        return WorkerResult(
            exit_code=result.exit_code,
            summary=summary,
            event_count=count,
            usage=usage,
            nonfatal_error_items=nonfatal_error_items,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=result.duration,
        )
