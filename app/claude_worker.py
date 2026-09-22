"""Production Claude Code CLI adapter behind Zelo's worker contract."""

import asyncio
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT, Settings
from app.developer_artifacts import PinnedExecutable, pin_executable
from app.developer_contracts import (
    DeveloperRequest,
    Limits,
    WorkerArtifactIdentity,
    WorkerCredentialLocation,
    WorkerIdentity,
    WorkerResult,
)
from app.developer_process import DeveloperError, environment, run_process
from app.developer_sandbox import WORKER_EXECUTABLE, external_worker_sandbox
from app.worker_authority import (
    WorkerAuthorityProfile,
    authority_profile,
    authority_snapshot_matches,
    claude_code_permission_semantics,
    claude_code_settings,
    claude_code_tools,
)

ADAPTER_SCHEMA = "claude-code-zelo-v1"
MIN_CLI_VERSION = (2, 1, 259)
MAX_CLI_VERSION_EXCLUSIVE = (2, 2, 0)
CAPABILITY_PROFILE = "claude-code-2.1.259-2.1.x-restricted-v1"
MAX_AUTH_BYTES = 8192
MAX_TURNS = 12
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\s|$)")
SUMMARY_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"summary": {"type": "string", "maxLength": 4096}},
        "required": ["summary"],
        "additionalProperties": False,
    },
    sort_keys=True,
    separators=(",", ":"),
)


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
        "model": settings.developer_claude_model,
        "max_turns": MAX_TURNS,
        "output_schema": json.loads(SUMMARY_SCHEMA),
    }
    return hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()


def parse_cli_version(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", "strict").strip()
    except UnicodeError:
        raise DeveloperError("claude_version_invalid") from None
    match = _VERSION.match(value)
    if match is None:
        raise DeveloperError("claude_version_invalid")
    parsed = tuple(int(part) for part in match.groups())
    if not MIN_CLI_VERSION <= parsed < MAX_CLI_VERSION_EXCLUSIVE:
        raise DeveloperError("claude_version_unsupported")
    return ".".join(match.groups())


def _stable_file(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_oauth_token(path: Path) -> str:
    """Read a trusted host-side token without ever mounting its file into the worker."""
    descriptor = None
    try:
        if path.is_symlink():
            raise DeveloperError("claude_auth_untrusted")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.getuid()}
            or before.st_mode & 0o077
            or not 16 <= before.st_size <= MAX_AUTH_BYTES
        ):
            raise DeveloperError("claude_auth_untrusted")
        raw = os.read(descriptor, MAX_AUTH_BYTES + 1)
        after = os.fstat(descriptor)
        if _stable_file(before) != _stable_file(after) or len(raw) != before.st_size:
            raise DeveloperError("worker_configuration_changed")
    except FileNotFoundError:
        raise DeveloperError("claude_auth_unavailable") from None
    except OSError:
        raise DeveloperError("claude_auth_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    content = raw[:-1] if raw.endswith(b"\n") else raw
    if b"\n" in content or b"\r" in content:
        raise DeveloperError("claude_auth_invalid")
    try:
        token = content.decode("ascii", "strict")
    except UnicodeError:
        raise DeveloperError("claude_auth_invalid") from None
    if len(token) < 16 or any(character.isspace() for character in token):
        raise DeveloperError("claude_auth_invalid")
    return token


def request_authority(request: DeveloperRequest) -> WorkerAuthorityProfile:
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


def parse_json_output(data: bytes, limits: Limits) -> tuple[str, int, dict[str, int]]:
    if not data or len(data) > limits.stdout_bytes:
        raise DeveloperError("claude_json_invalid")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-finite JSON number")

    try:
        payload = json.loads(
            data.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise DeveloperError("claude_json_invalid") from None
    if not isinstance(payload, dict) or payload.get("is_error") is True:
        raise DeveloperError("claude_turn_failed")
    denials = payload.get("permission_denials", [])
    if denials not in (None, []) and denials != ():
        raise DeveloperError("claude_permission_denied")
    structured = payload.get("structured_output")
    if not isinstance(structured, dict) or set(structured) != {"summary"}:
        raise DeveloperError("claude_summary_invalid")
    summary = structured["summary"]
    if not isinstance(summary, str) or not 1 <= len(summary) <= 4096:
        raise DeveloperError("claude_summary_invalid")
    try:
        summary.encode("utf-8", "strict")
    except UnicodeError:
        raise DeveloperError("claude_summary_invalid") from None

    turns = payload.get("num_turns", 1)
    if type(turns) is not int or not 1 <= turns <= limits.events:
        raise DeveloperError("claude_event_limit")

    usage: dict[str, int] = {}
    raw_usage = payload.get("usage", {})
    if raw_usage is not None:
        if not isinstance(raw_usage, dict):
            raise DeveloperError("claude_json_invalid")
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            if key in raw_usage:
                value = raw_usage[key]
                if type(value) is not int or value < 0:
                    raise DeveloperError("claude_json_invalid")
                usage[key] = value
    return summary, turns, usage


class ClaudeCodeWorker:
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
                approved_auth = approved.credential_location("oauth_token")
            except ValueError:
                raise DeveloperError("worker_configuration_changed") from None
            if approved_auth.path != str(
                configuration.developer_claude_oauth_token_file.absolute()
            ):
                raise DeveloperError("worker_configuration_changed")
        with pin_executable(
            configuration.developer_claude_executable, approved_executable
        ) as artifact:
            yield ClaudeCodeWorker(configuration, artifact)

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
        # Validate credential presence and permissions without persisting its contents.
        read_oauth_token(self.settings.developer_claude_oauth_token_file)
        result = await run_process(
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
        if result.exit_code:
            raise DeveloperError("claude_preflight_failed")
        version = parse_cli_version(result.stdout)
        return WorkerIdentity(
            implementation="claude_code",
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
            credential_locations=(
                WorkerCredentialLocation(
                    name="oauth_token",
                    path=str(self.settings.developer_claude_oauth_token_file.absolute()),
                ),
            ),
        )

    def argv(self, repo: Path, profile: WorkerAuthorityProfile) -> list[str]:
        binary = self.artifact()
        semantics = claude_code_permission_semantics(profile)
        tools = ",".join(claude_code_tools(profile))
        settings = _canonical_json(claude_code_settings(profile))
        command = [
            WORKER_EXECUTABLE,
            "--bare",
            "--restricted",
            "--no-session-persistence",
            "--permission-mode",
            str(semantics["permission_mode"]),
            "--permission-prompts",
            str(semantics["permission_prompts"]),
            "--tools",
            tools,
            "--allowedTools",
            tools,
            "--disallowedTools",
            ",".join(semantics["disallowed_tools"]),
            "--settings",
            settings,
            "--model",
            self.settings.developer_claude_model,
            "--max-turns",
            str(MAX_TURNS),
            "--output-format",
            "json",
            "--json-schema",
            SUMMARY_SCHEMA,
            "-p",
            (
                "Execute the Zelo coding task supplied on standard input. Work only in the "
                "current workspace, obey its security boundary, and return only the requested "
                "structured completion summary."
            ),
        ]
        return external_worker_sandbox(
            repo,
            command,
            worker_fd=binary.fd,
            writable=profile.workspace_write,
            service_network=True,
        )

    async def run(self, request: DeveloperRequest, repo: Path) -> WorkerResult:
        if self.pinned is None:
            with self.bind(request.worker) as bound:
                return await bound.run(request, repo)
        binary = self.artifact()
        try:
            approved_executable = request.worker.artifact("executable")
            approved_auth = request.worker.credential_location("oauth_token")
        except ValueError:
            raise DeveloperError("worker_configuration_changed") from None
        expected_auth_path = str(self.settings.developer_claude_oauth_token_file.absolute())
        if (
            str(binary.source) != approved_executable.path
            or binary.digest != approved_executable.sha256
            or approved_auth.path != expected_auth_path
            or request.worker.config_version != configuration_version(self.settings)
        ):
            raise DeveloperError("worker_configuration_changed")
        if request.worker_selection.descriptor.role != "developer":
            raise DeveloperError("worker_role_mismatch")

        profile = request_authority(request)
        token = read_oauth_token(self.settings.developer_claude_oauth_token_file)
        prompt = (
            "You are Zelo's subordinate coding worker in an isolated checkout. "
            "Read only the files available inside this checkout. Make only the requested edits. "
            "Run useful local tests when allowed. Do not commit, push, merge, rebase, tag, deploy, "
            "publish, access unrelated host files, or attempt network access from commands. "
            "Exact instruction:\n" + request.instruction
        )
        started = datetime.now(UTC)
        result = await run_process(
            self.argv(repo, profile),
            cwd=repo,
            timeout=request.limits.worker_seconds,
            stdout_limit=request.limits.stdout_bytes,
            stderr_limit=request.limits.stderr_bytes,
            stdin=prompt.encode("utf-8"),
            env=environment()
            | {
                "CLAUDE_CODE_OAUTH_TOKEN": token,
                "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
            },
            pass_fds=(binary.fd,),
        )
        if result.exit_code:
            raise DeveloperError("claude_nonzero_exit", exit_code=result.exit_code)
        summary, turns, usage = parse_json_output(result.stdout, request.limits)
        return WorkerResult(
            exit_code=0,
            summary=summary,
            event_count=turns,
            usage=usage,
            nonfatal_error_items=0,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=result.duration,
        )
