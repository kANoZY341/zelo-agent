"""Production OpenCode CLI adapter using a trusted local Ollama provider."""

import asyncio
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT, Settings
from app.developer_artifacts import PinnedExecutable, pin_executable
from app.developer_contracts import (
    DeveloperRequest,
    ReviewerExecutionRequest,
    WorkerArtifactIdentity,
    WorkerIdentity,
    WorkerResult,
)
from app.developer_process import DeveloperError, environment, run_process
from app.developer_sandbox import WORKER_EXECUTABLE, external_worker_sandbox
from app.worker_authority import (
    WorkerAuthorityProfile,
    authority_profile,
    authority_snapshot_matches,
    opencode_permission_semantics,
)

ADAPTER_SCHEMA = "opencode-zelo-v1"
MIN_CLI_VERSION = (2, 0, 0)
MAX_CLI_VERSION_EXCLUSIVE = (3, 0, 0)
CAPABILITY_PROFILE = "opencode-v2-standalone-ollama-inline-policy-v2"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:\s|$)")
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
        "standalone": True,
        "pure": True,
        "format": "default",
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
        "plugins": [],
        "skills": [],
        "commands": {},
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
            raise DeveloperError("opencode_preflight_failed")
        version = parse_cli_version(result.stdout)
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

    def argv(self, repo: Path, profile: WorkerAuthorityProfile, prompt: str) -> list[str]:
        binary = self.artifact()
        command = [
            WORKER_EXECUTABLE,
            "--pure",
            "run",
            "--standalone",
            "--model",
            self.settings.developer_opencode_model,
            "--agent",
            "build",
            "--format",
            "default",
            prompt,
        ]
        return external_worker_sandbox(
            repo,
            command,
            worker_fd=binary.fd,
            writable=profile.workspace_write,
            service_network=True,
        )

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

        started = datetime.now(UTC)
        config = inline_config(self.settings, profile)
        result = await run_process(
            self.argv(repo, profile, prompt),
            cwd=repo,
            timeout=request.limits.worker_seconds,
            stdout_limit=request.limits.stdout_bytes,
            stderr_limit=request.limits.stderr_bytes,
            env=environment()
            | {
                "OPENCODE_CONFIG_CONTENT": _canonical_json(config),
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_PRUNE": "1",
                "OPENCODE_DISABLE_TERMINAL_TITLE": "1",
            },
            pass_fds=(binary.fd,),
        )
        if result.exit_code:
            raise DeveloperError("opencode_nonzero_exit", exit_code=result.exit_code)
        summary = parse_text_output(result.stdout)
        return WorkerResult(
            exit_code=0,
            summary=summary,
            event_count=1,
            usage={},
            nonfatal_error_items=0,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=result.duration,
        )
