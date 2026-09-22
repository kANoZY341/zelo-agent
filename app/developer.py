"""Approval-bound standalone developer checkouts; no source promotion."""

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import stat
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.claude_worker import ClaudeCodeWorker
from app.codex_worker import CodexCliWorker, file_digest
from app.config import ROOT, Settings
from app.developer_contracts import (
    DeveloperRequest,
    DeveloperWorker,
    Limits,
    ReviewerBinding,
    ReviewerExecutionRequest,
    ReviewerReport,
    ValidationResult,
)
from app.developer_process import DeveloperError, run_process, run_sync, uncancellable
from app.developer_review import inventory
from app.developer_sandbox import git_argv, sandbox, sandbox_policy_version
from app.developer_validation import VALIDATION_VERSION, python_command, validation_manifest
from app.worker_registry import (
    Supervisor,
    WorkerRegistry,
    descriptor_for_reviewer,
    descriptor_for_worker,
)

DeveloperRuntimeError = DeveloperError
Emit = Callable[[str, dict[str, Any]], None]


def no_symlinks(path: Path) -> Path:
    path = path.absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise DeveloperError("workspace_escape")
    if path.resolve() != path:
        raise DeveloperError("workspace_escape")
    return path


def identifier(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DeveloperError("workspace_identifier_invalid") from None
    return value


def review_name(value: bytes) -> str:
    try:
        return value.decode("utf-8", "strict")
    except UnicodeError:
        raise DeveloperError("review_filename_encoding") from None


def parse_reviewer_report(value: str) -> ReviewerReport:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        payload = json.loads(value, object_pairs_hook=pairs)
        return ReviewerReport.model_validate(payload)
    except (ValueError, TypeError, RecursionError):
        raise DeveloperError("reviewer_report_invalid") from None


def tree_digest(root: Path) -> str:
    """No symlink following or special-file reads; bounded trusted-state hashing."""
    digest = hashlib.sha256()
    count = total = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(directory) / name
            info = path.lstat()
            count += 1
            if count > 20000:
                raise DeveloperError("snapshot_limit")
            digest.update(str(path.relative_to(root)).encode())
            digest.update(str((info.st_mode, info.st_size, info.st_mtime_ns)).encode())
            if stat.S_ISLNK(info.st_mode):
                digest.update(os.readlink(path).encode())
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 64 * 1024 * 1024:
                    raise DeveloperError("snapshot_limit")
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    while block := stream.read(65536):
                        digest.update(block)
            elif not stat.S_ISDIR(info.st_mode):
                raise DeveloperError("snapshot_special_file")
    return digest.hexdigest()


class DeveloperRuntime:
    def __init__(
        self,
        settings: Settings,
        worker: DeveloperWorker | None = None,
        source_repository: Path = ROOT,
        registry: WorkerRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.source_repository = source_repository.resolve()
        self._registry_explicit = registry is not None
        if registry is not None:
            self.registry = registry
            self.supervisor = Supervisor(self.registry)
            selected, _ = self.supervisor.select(
                role="developer", capabilities=("zelo_core_development",)
            )
            if worker is not None and selected.worker is not worker:
                raise DeveloperError("worker_registry_mismatch")
            self._worker = selected.worker
        else:
            if worker is None:
                codex = CodexCliWorker(settings)
                registrations = [
                    (
                        descriptor_for_worker(
                            codex,
                            implementation="codex",
                            worker_id="codex_core",
                            priority=100,
                        ),
                        codex,
                    ),
                    (
                        descriptor_for_reviewer(
                            codex,
                            implementation="codex",
                            worker_id="codex_reviewer",
                        ),
                        codex,
                    ),
                ]
                if settings.developer_claude_enabled:
                    claude = ClaudeCodeWorker(settings)
                    registrations.extend(
                        [
                            (
                                descriptor_for_worker(
                                    claude,
                                    implementation="claude_code",
                                    worker_id="claude_core",
                                    priority=90,
                                ),
                                claude,
                            ),
                            (
                                descriptor_for_reviewer(
                                    claude,
                                    implementation="claude_code",
                                    worker_id="claude_reviewer",
                                ),
                                claude,
                            ),
                        ]
                    )
                self.registry = WorkerRegistry(registrations)
                self.supervisor = Supervisor(self.registry)
                selected, _ = self.supervisor.select(
                    role="developer", capabilities=("zelo_core_development",)
                )
                self._worker = selected.worker
            else:
                self._worker = worker
                self.registry = WorkerRegistry(
                    [
                        (
                            descriptor_for_worker(
                                self._worker,
                                implementation=self._worker.identity().implementation,
                            ),
                            self._worker,
                        )
                    ]
                )
                self.supervisor = Supervisor(self.registry)

    @property
    def worker(self) -> DeveloperWorker:
        return self._worker

    @worker.setter
    def worker(self, value: DeveloperWorker) -> None:
        if self._registry_explicit:
            raise DeveloperError("worker_registry_explicit")
        implementation = value.identity().implementation
        self._worker = value
        self.registry = WorkerRegistry(
            [(descriptor_for_worker(value, implementation=implementation), value)]
        )
        self.supervisor = Supervisor(self.registry)

    def limits(self, timeout: float) -> Limits:
        s = self.settings
        return Limits(
            overall_seconds=min(timeout, s.developer_timeout_seconds),
            worker_seconds=s.developer_worker_timeout_seconds,
            validation_seconds=s.developer_validation_timeout_seconds,
            git_seconds=s.developer_git_timeout_seconds,
            stdout_bytes=s.developer_stdout_bytes,
            stderr_bytes=s.developer_stderr_bytes,
            line_bytes=s.developer_jsonl_line_bytes,
            events=s.developer_jsonl_events,
            diff_bytes=s.developer_diff_bytes,
            changed_files=s.developer_changed_files,
        )

    def root(self) -> Path:
        workspace = no_symlinks(self.settings.workspace_root)
        root = no_symlinks(workspace / "developer")
        if not root.is_relative_to(workspace) or workspace == self.source_repository:
            raise DeveloperError("workspace_escape")
        return root

    def path(self, execution_id: str) -> Path:
        root = self.root()
        path = no_symlinks(root / identifier(execution_id) / "repo")
        if not path.is_relative_to(root) or path == self.source_repository:
            raise DeveloperError("workspace_escape")
        return path

    def validation_identity(self) -> str:
        return validation_manifest(ROOT / ".venv", self.source_repository)

    def request(
        self,
        instruction: str,
        profile: str,
        timeout: float,
        *,
        capabilities: Iterable[str] = ("zelo_core_development",),
        running_counts: Mapping[str, int] | None = None,
    ) -> DeveloperRequest:
        if profile != "zelo_core" or not instruction.strip():
            raise DeveloperError("developer_request_invalid")
        self.root()
        if not no_symlinks(self.source_repository / ".git").is_dir():
            raise DeveloperError("source_repository_unavailable")
        top = run_sync(git_argv("rev-parse", "--show-toplevel"), cwd=self.source_repository)
        if top.exit_code or top.stdout.decode().strip() != str(self.source_repository):
            raise DeveloperError("source_repository_mismatch")
        result = run_sync(git_argv("rev-parse", "HEAD"), cwd=self.source_repository)
        if result.exit_code:
            raise DeveloperError("source_repository_unavailable")
        selected, selection = self.supervisor.select(
            role="developer",
            capabilities=capabilities,
            running_counts=running_counts,
        )
        try:
            identity = selected.worker.identity()
        except (DeveloperError, OSError, ValueError, RuntimeError):
            raise DeveloperError("worker_identity_unavailable") from None
        if identity.implementation != selected.descriptor.implementation:
            raise DeveloperError("worker_descriptor_mismatch")

        reviewer = None
        try:
            review_registration, review_selection = self.select_reviewer(
                selected.descriptor.worker_id
            )
        except DeveloperError as error:
            if str(error) != "no_eligible_worker":
                raise
        else:
            try:
                review_identity = review_registration.worker.identity()
            except (DeveloperError, OSError, ValueError, RuntimeError):
                raise DeveloperError("reviewer_identity_unavailable") from None
            if review_identity.implementation != review_registration.descriptor.implementation:
                raise DeveloperError("reviewer_descriptor_mismatch")
            reviewer = ReviewerBinding(
                worker=review_identity,
                worker_selection=review_selection,
            )

        return DeveloperRequest(
            instruction=instruction,
            source_repository=str(self.source_repository),
            workspace_root=str(self.root()),
            validation_identity=self.validation_identity(),
            validation_version=VALIDATION_VERSION,
            base_sha=result.stdout.decode().strip(),
            worker=identity,
            worker_selection=selection,
            reviewer=reviewer,
            sandbox_version=sandbox_policy_version(),
            limits=self.limits(timeout),
        )

    def select_reviewer(
        self,
        producer_worker_id: str,
        *,
        running_counts: Mapping[str, int] | None = None,
    ):
        producer = self.registry.get(producer_worker_id)
        if producer is None:
            raise DeveloperError("producer_worker_unknown")
        excluded = tuple(
            descriptor.worker_id
            for descriptor in self.registry.list()
            if (registration := self.registry.get(descriptor.worker_id)) is not None
            and registration.worker is producer.worker
        )
        return self.supervisor.select(
            role="reviewer",
            capabilities=("code_review",),
            running_counts=running_counts,
            exclude_worker_ids=excluded,
        )

    def compatible(self, request: DeveloperRequest, timeout: float) -> bool:
        selected = self.registry.get(request.worker_selection.descriptor.worker_id)
        if selected is None:
            return False
        selection = request.worker_selection
        developer_binding_valid = (
            selection.requested_role == "developer"
            and selection.descriptor.role == "developer"
            and request.worker.implementation == selection.descriptor.implementation
            and self.supervisor.matches(selection)
        )
        reviewer_compatible = True
        if request.reviewer is not None:
            reviewer = request.reviewer
            review_selection = reviewer.worker_selection
            producer_id = selection.descriptor.worker_id
            reviewer_compatible = (
                review_selection.requested_role == "reviewer"
                and review_selection.descriptor.role == "reviewer"
                and "code_review" in review_selection.requested_capabilities
                and review_selection.authority_profile == "workspace-read-no-network-v1"
                and reviewer.worker.implementation
                == review_selection.descriptor.implementation
                and review_selection.descriptor.worker_id != producer_id
                and producer_id in review_selection.excluded_worker_ids
                and self.supervisor.matches(review_selection)
            )
        return (
            request.source_repository == str(self.source_repository)
            and request.workspace_root == str(self.root())
            and request.sandbox_version == sandbox_policy_version()
            and request.validation_version == VALIDATION_VERSION
            and request.limits == self.limits(timeout)
            and developer_binding_valid
            and reviewer_compatible
        )

    def approval_compatible(self, request: DeveloperRequest, timeout: float) -> bool:
        """Perform the expensive live approval check only from a synchronous boundary.

        Runner polling never calls this method. The operator approval route is synchronous
        (FastAPI runs it in a worker thread), and execution performs the same definitive
        checks again before any worker bytes can run.
        """
        if not self.compatible(request, timeout):
            return False
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise DeveloperError("approval_revalidation_unavailable")

        selected = self.registry.get(request.worker_selection.descriptor.worker_id)
        if selected is None:
            return False
        reviewer_registration = None
        if request.reviewer is not None:
            reviewer_registration = self.registry.get(
                request.reviewer.worker_selection.descriptor.worker_id
            )
            if reviewer_registration is None:
                return False
        try:
            head = run_sync(git_argv("rev-parse", "HEAD"), cwd=self.source_repository)
            if head.exit_code:
                raise DeveloperError("approval_revalidation_unavailable")
            if head.stdout.decode().strip() != request.base_sha:
                return False
            try:
                validation_identity = self.validation_identity()
            except DeveloperError as error:
                if str(error) in {
                    "validation_environment_changed",
                    "validation_environment_unavailable",
                }:
                    raise DeveloperError("approval_revalidation_unavailable") from None
                raise
            if validation_identity != request.validation_identity:
                return False
            with selected.worker.bind(request.worker) as bound_worker:
                identity = asyncio.run(bound_worker.preflight())
            reviewer_identity = None
            if request.reviewer is not None and reviewer_registration is not None:
                with reviewer_registration.worker.bind(request.reviewer.worker) as bound_reviewer:
                    reviewer_identity = asyncio.run(bound_reviewer.preflight())
        except DeveloperError as error:
            if str(error) in {
                "worker_configuration_changed",
                "codex_executable_untrusted",
                "worker_artifact_mismatch",
                "worker_artifact_unsealed",
                "codex_version_unsupported",
                "codex_capabilities_unsupported",
            }:
                return False
            if str(error) == "approval_revalidation_unavailable":
                raise
            raise DeveloperError("approval_revalidation_unavailable") from None
        except (OSError, ValueError, RuntimeError):
            raise DeveloperError("approval_revalidation_unavailable") from None
        reviewer_matches = request.reviewer is None or (
            reviewer_identity == request.reviewer.worker
            and reviewer_identity.implementation
            == request.reviewer.worker_selection.descriptor.implementation
        )
        return (
            identity == request.worker
            and identity.implementation == request.worker_selection.descriptor.implementation
            and reviewer_matches
        )

    async def git(self, repo: Path, request: DeveloperRequest, *args: str, trusted=False) -> bytes:
        argv = git_argv(*args)
        if not trusted:
            self.verify_checkout_path(repo)
            argv = sandbox(repo, argv, writable=False, git_inspection=True)
        result = await run_process(
            argv,
            cwd=repo,
            timeout=request.limits.git_seconds,
            stdout_limit=request.limits.stdout_bytes,
            stderr_limit=request.limits.stderr_bytes,
        )
        if result.exit_code:
            raise DeveloperError("git_operation_failed")
        return result.stdout

    def verify_checkout_path(self, repo: Path) -> None:
        no_symlinks(repo)
        if repo != self.path(repo.parent.name) or not repo.is_dir():
            raise DeveloperError("workspace_escape")
        git = no_symlinks(repo / ".git")
        if (
            not git.is_dir()
            or (git / "commondir").exists()
            or (git / "objects/info/alternates").exists()
        ):
            raise DeveloperError("git_metadata_invalid")

    async def prepare(self, repo: Path, request: DeveloperRequest) -> None:
        repo.mkdir(mode=0o700)
        # Fetch through local transport copies objects; no shared object DB or remote config.
        for args in (
            ("init", "--quiet", str(repo)),
            (
                "-c",
                "protocol.file.allow=always",
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                str(self.source_repository),
                request.base_sha,
            ),
            ("checkout", "--quiet", "--detach", request.base_sha),
        ):
            await self.git(repo, request, *args, trusted=True)
        self.verify_checkout_path(repo)
        if (
            await self.git(repo, request, "rev-parse", "HEAD")
        ).decode().strip() != request.base_sha:
            raise DeveloperError("checkout_base_mismatch")
        if await self.git(repo, request, "remote"):
            raise DeveloperError("checkout_remote_forbidden")

    def _primary_snapshot_digest(self, outputs: list[bytes]) -> str:
        source = self.source_repository
        digest = hashlib.sha256(b"".join(outputs))
        # Hash tracked contents and Git state. Never open unrelated untracked user files.
        total = 0
        for raw in outputs[-1].split(b"\0"):
            if not raw:
                continue
            path = source / os.fsdecode(raw)
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                total += path.stat().st_size
                if total > 32 * 1024 * 1024:
                    raise DeveloperError("snapshot_limit")
                digest.update(file_digest(path).encode())
        digest.update(tree_digest(source / ".git").encode())
        return digest.hexdigest()

    async def primary_snapshot(self, request: DeveloperRequest) -> str:
        source = self.source_repository
        outputs = []
        for args in (
            ("rev-parse", "HEAD"),
            ("status", "--porcelain=v1", "-z"),
            ("show-ref", "--head"),
            ("ls-files", "-z"),
        ):
            outputs.append(await self.git(source, request, *args, trusted=True))
        return await asyncio.to_thread(self._primary_snapshot_digest, outputs)

    async def validate(self, repo: Path, request: DeveloperRequest) -> list[ValidationResult]:
        commands = [
            ("ruff_check", python_command("-m", "ruff", "check", ".")),
            ("ruff_format", python_command("-m", "ruff", "format", "--check", ".")),
            ("pytest", python_command("-m", "pytest", "-q")),
            (
                "git_diff_check",
                git_argv(
                    "diff", "--no-ext-diff", "--no-textconv", "--check", request.base_sha, "--"
                ),
            ),
        ]
        results = []
        for name, command in commands:
            if await asyncio.to_thread(self.validation_identity) != request.validation_identity:
                raise DeveloperError("validation_environment_changed")
            self.verify_checkout_path(repo)
            started = time.monotonic()
            try:
                result = await run_process(
                    sandbox(repo, command, venv=ROOT / ".venv"),
                    cwd=repo,
                    timeout=request.limits.validation_seconds,
                    stdout_limit=min(request.limits.stdout_bytes, 32768),
                    stderr_limit=request.limits.stderr_bytes,
                )
                output = (result.stdout + result.stderr).decode("utf-8", "replace")[:32768]
                code, error = result.exit_code, None
            except DeveloperError as failure:
                code, output, error = 124, "", str(failure)
            results.append(
                ValidationResult(
                    identifier=name,
                    exit_code=code,
                    output=output,
                    duration_seconds=time.monotonic() - started,
                    completed_at=datetime.now(UTC),
                    error=error,
                )
            )
            if code:
                break
        if await asyncio.to_thread(self.validation_identity) != request.validation_identity:
            raise DeveloperError("validation_environment_changed")
        return results

    async def review(self, repo: Path, request: DeveloperRequest) -> dict[str, Any]:
        head = (await self.git(repo, request, "rev-parse", "HEAD")).decode().strip()
        status = await self.git(
            repo, request, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        names = await self.git(
            repo,
            request,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            request.base_sha,
            "--",
        )
        tracked = await self.git(repo, request, "ls-files", "-z")
        artifacts, ignored = await inventory(
            repo, request, [review_name(p) for p in tracked.split(b"\0") if p]
        )
        ignored_names = {item["path"] for item in ignored}
        paths = list(
            dict.fromkeys(
                [review_name(p) for p in names.split(b"\0") if p]
                + [item["path"] for item in artifacts]
            )
        )
        if len(paths) > request.limits.changed_files:
            raise DeveloperError("changed_file_limit")
        diff = await self.git(
            repo, request, "diff", "--no-ext-diff", "--no-textconv", request.base_sha, "--"
        )
        stat_output = await self.git(
            repo,
            request,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--stat",
            request.base_sha,
            "--",
        )
        for item in artifacts:
            name = item["path"]
            if name in ignored_names:
                # Even small text artifacts may contain tool-produced credentials.
                # Ignored artifacts are metadata-only, never added to persisted diff.
                stat_output += json.dumps(name, ensure_ascii=True).encode() + b" (ignored)\n"
                continue
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise DeveloperError("review_path_invalid")
            self.verify_checkout_path(repo)
            command = git_argv(
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                "/dev/null",
                "./" + name,
            )
            part = await run_process(
                sandbox(repo, command, writable=False, git_inspection=True),
                cwd=repo,
                timeout=request.limits.git_seconds,
                stdout_limit=request.limits.diff_bytes,
                stderr_limit=request.limits.stderr_bytes,
            )
            if part.exit_code not in (0, 1):
                raise DeveloperError("review_untracked_failed")
            diff += part.stdout
            if len(diff) > request.limits.diff_bytes:
                raise DeveloperError("diff_limit")
            stat_output += json.dumps(name, ensure_ascii=True).encode() + b" (untracked)\n"
        if len(diff) > request.limits.diff_bytes or len(stat_output) > request.limits.stat_bytes:
            raise DeveloperError("diff_limit")
        return {
            "head": head,
            "head_changed": head != request.base_sha,
            "changed_files": paths,
            "ignored_generated_files": ignored,
            "git_status": status.decode("utf-8", "replace"),
            "diff_stat": stat_output.decode("utf-8", "replace"),
            "diff": diff.decode("utf-8", "replace"),
        }

    async def run_independent_review(
        self,
        repo: Path,
        request: DeveloperRequest,
        emit: Emit,
    ) -> dict[str, Any] | None:
        binding = request.reviewer
        if binding is None:
            return None
        selection = binding.worker_selection
        registration = self.registry.get(selection.descriptor.worker_id)
        if registration is None or not self.supervisor.matches(selection):
            raise DeveloperError("reviewer_configuration_changed")

        review_request = ReviewerExecutionRequest(
            instruction=(
                "Review the current checkout independently for correctness, security, regressions, "
                "and compliance with the requested task. Do not modify anything. Return exactly "
                "one "
                "JSON object with no markdown using this shape: "
                '{"summary":"bounded summary","findings":[{"severity":"low|medium|high|critical",'
                '"message":"bounded finding","path":"relative/path/or null"}]}. '
                "Use an empty findings array when no concrete issue is found."
            ),
            source_repository=request.source_repository,
            workspace_root=request.workspace_root,
            base_sha=request.base_sha,
            worker=binding.worker,
            worker_selection=selection,
            sandbox_version=request.sandbox_version,
            limits=request.limits,
        )
        before = await asyncio.to_thread(tree_digest, repo)
        lock = self.worker_lock(
            registration.descriptor.worker_id,
            registration.descriptor.max_concurrency,
        )
        context = registration.worker.bind(binding.worker)
        entered = False
        emit(
            "reviewer_capacity_claimed",
            {
                "worker_id": registration.descriptor.worker_id,
                "max_concurrency": registration.descriptor.max_concurrency,
            },
        )
        emit(
            "review_started",
            {
                "worker_id": registration.descriptor.worker_id,
                "implementation": registration.descriptor.implementation,
            },
        )
        try:
            enter_task = asyncio.create_task(asyncio.to_thread(context.__enter__))
            bound, cancelled = await uncancellable(enter_task)
            entered = True
            if cancelled:
                raise asyncio.CancelledError
            actual = await bound.preflight()
            if actual != binding.worker:
                raise DeveloperError("reviewer_configuration_changed")
            result = await bound.run(review_request, repo)
            if result.exit_code:
                raise DeveloperError("reviewer_nonzero_exit", exit_code=result.exit_code)
            if await asyncio.to_thread(tree_digest, repo) != before:
                raise DeveloperError("reviewer_boundary_violation")
            report = parse_reviewer_report(result.summary)
            payload = {
                "worker_id": registration.descriptor.worker_id,
                "implementation": registration.descriptor.implementation,
                "worker_version": binding.worker.cli_version,
                "report": report.model_dump(mode="json"),
                "usage": result.usage,
                "event_count": result.event_count,
                "started_at": result.started_at.isoformat(),
                "completed_at": result.completed_at.isoformat(),
                "duration_seconds": result.duration_seconds,
            }
            emit(
                "review_completed",
                {
                    "worker_id": registration.descriptor.worker_id,
                    "finding_count": len(report.findings),
                },
            )
            return payload
        except BaseException as error:
            try:
                emit(
                    "review_failed",
                    {
                        "worker_id": registration.descriptor.worker_id,
                        "reason": (
                            str(error)
                            if isinstance(error, DeveloperError)
                            else "reviewer_interrupted_or_failed"
                        ),
                    },
                )
            finally:
                raise
        finally:
            release_failure = None
            lock.close()
            try:
                emit(
                    "reviewer_capacity_released",
                    {"worker_id": registration.descriptor.worker_id},
                )
            except Exception as error:
                release_failure = error
            if entered:
                exit_task = asyncio.create_task(
                    asyncio.to_thread(context.__exit__, None, None, None)
                )
                await uncancellable(exit_task)
            if release_failure is not None:
                raise release_failure

    def _lock_root(self) -> Path:
        root = self.root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root()
        lock_root = no_symlinks(ROOT / "runtime")
        lock_root.mkdir(exist_ok=True, mode=0o700)
        return lock_root

    @staticmethod
    def _open_lock_stream(path: Path):
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            return os.fdopen(fd, "w")
        except BaseException:
            os.close(fd)
            raise

    def worker_lock(self, worker_id: str, max_concurrency: int):
        """Acquire one cross-process capacity slot for the approved worker."""
        lock_root = self._lock_root()
        for slot in range(max_concurrency):
            stream = self._open_lock_stream(lock_root / f"developer-worker-{worker_id}-{slot}.lock")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close()
                continue
            return stream
        raise DeveloperError("developer_worker_capacity_exhausted")

    def maintenance_lock(self):
        """Serialize operator cleanup without globally serializing worker execution."""
        lock_root = self._lock_root()
        stream = self._open_lock_stream(lock_root / "developer-maintenance.lock")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise DeveloperError("developer_runtime_busy") from None
        return stream

    def lock(self):
        """Compatibility alias for the maintenance lock.

        Worker execution uses per-worker capacity slots and never this global lock.
        """
        return self.maintenance_lock()

    async def run(
        self,
        task_id: str,
        execution_id: str,
        request: DeveloperRequest,
        emit: Emit,
    ) -> dict[str, Any]:
        started = datetime.now(UTC)
        monotonic_start = time.monotonic()
        identifier(task_id)
        repo = self.path(execution_id)
        result: dict[str, Any] = {
            "worker": request.worker.implementation,
            "worker_version": request.worker.cli_version,
            "worker_config_version": request.worker.config_version,
            "base_sha": request.base_sha,
            "worktree": str(repo.relative_to(self.root())),
            "worker_exit_code": None,
            "worker_summary": "",
            "usage": {},
            "nonfatal_error_items": 0,
            "changed_files": [],
            "ignored_generated_files": [],
            "git_status": "",
            "diff_stat": "",
            "diff": "",
            "head": None,
            "head_changed": False,
            "validation": [],
            "validation_passed": False,
            "independent_review": None,
            "started_at": started.isoformat(),
            "worker_started_at": None,
            "worker_completed_at": None,
            "validation_completed_at": None,
            "error": None,
            "protocol_diagnostic": None,
            "boundary_error": None,
            "original_error": None,
            "cancelled": False,
        }
        lock = None
        before = git_before = manifest = None
        artifacts = ExitStack()
        bound_worker_context = None
        bound_worker = None
        bound_worker_entered = False
        lock_released = False
        transient_error = None
        try:
            emit("developer_started", {})
            async with asyncio.timeout(request.limits.overall_seconds):
                if not self.compatible(request, request.limits.overall_seconds):
                    raise DeveloperError("developer_configuration_changed")
                source_head = (
                    (
                        await self.git(
                            self.source_repository,
                            request,
                            "rev-parse",
                            "HEAD",
                            trusted=True,
                        )
                    )
                    .decode()
                    .strip()
                )
                if source_head != request.base_sha:
                    raise DeveloperError("source_repository_changed")
                # Trusted Git/state capture precedes every replaceable worker executable.
                before = await self.primary_snapshot(request)
                selected = self.registry.get(request.worker_selection.descriptor.worker_id)
                if selected is None or not self.supervisor.matches(request.worker_selection):
                    raise DeveloperError("worker_configuration_changed")
                lock = self.worker_lock(
                    selected.descriptor.worker_id, selected.descriptor.max_concurrency
                )
                emit(
                    "worker_capacity_claimed",
                    {
                        "worker_id": selected.descriptor.worker_id,
                        "max_concurrency": selected.descriptor.max_concurrency,
                    },
                )
                bound_worker_context = selected.worker.bind(request.worker)
                enter_task = asyncio.create_task(asyncio.to_thread(bound_worker_context.__enter__))
                bound_worker, bind_cancelled = await uncancellable(enter_task)
                bound_worker_entered = True
                if bind_cancelled:
                    raise asyncio.CancelledError
                actual = await bound_worker.preflight()
                if (
                    actual != request.worker
                    or await asyncio.to_thread(self.validation_identity)
                    != request.validation_identity
                ):
                    raise DeveloperError("worker_configuration_changed")
                repo.parent.mkdir(mode=0o700)
                manifest = repo.parent / "owner.json"
                manifest.write_text(
                    json.dumps({"task": task_id, "execution": execution_id, "active": True})
                )
                await self.prepare(repo, request)
                emit("developer_workspace_created", {"workspace": result["worktree"]})
                git_before = await asyncio.to_thread(tree_digest, repo / ".git")
                self.verify_checkout_path(repo)
                emit("developer_worker_started", {})
                result["worker_started_at"] = datetime.now(UTC).isoformat()
                try:
                    worker = await bound_worker.run(request, repo)
                except BaseException:
                    emit("developer_worker_failed", {"reason": "worker_interrupted_or_failed"})
                    raise
                result.update(
                    worker_exit_code=worker.exit_code,
                    worker_summary=worker.summary,
                    worker_completed_at=worker.completed_at.isoformat(),
                    usage=worker.usage,
                    nonfatal_error_items=worker.nonfatal_error_items,
                )
                emit(
                    "developer_worker_failed" if worker.exit_code else "developer_worker_completed",
                    {"exit_code": worker.exit_code},
                )
                if worker.exit_code:
                    raise DeveloperError("worker_nonzero_exit")
                self.verify_checkout_path(repo)
                if await asyncio.to_thread(tree_digest, repo / ".git") != git_before:
                    raise DeveloperError("developer_boundary_violation")
                emit("developer_validation_started", {})
                validation = await self.validate(repo, request)
                result["validation"] = [v.model_dump(mode="json") for v in validation]
                result["validation_completed_at"] = datetime.now(UTC).isoformat()
                passed = len(validation) == 4 and all(v.exit_code == 0 for v in validation)
                emit("developer_validation_completed", {"passed": passed})
                if not passed:
                    # Preserve the execution failure before a later protected-state
                    # check can report a distinct boundary failure.
                    result["error"] = "developer_validation_failed"
                self.verify_checkout_path(repo)
                if await asyncio.to_thread(tree_digest, repo / ".git") != git_before:
                    raise DeveloperError("developer_boundary_violation")
                result.update(await self.review(repo, request))
                if result["head_changed"]:
                    raise DeveloperError("developer_boundary_violation")
                if passed and request.reviewer is not None:
                    result["independent_review"] = await self.run_independent_review(
                        repo, request, emit
                    )
                result["validation_passed"] = passed
        except asyncio.CancelledError:
            result.update(error="developer_cancelled", cancelled=True)
        except TimeoutError:
            result["error"] = "developer_overall_timeout"
        except DeveloperError as error:
            if str(error) == "developer_worker_capacity_exhausted":
                transient_error = error
            result["error"] = str(error)
            result["protocol_diagnostic"] = error.diagnostic
            if error.exit_code is not None:
                result["worker_exit_code"] = error.exit_code
        except Exception:
            result["error"] = "developer_execution_failed"
        finally:

            async def finalize():
                if before is not None:
                    try:
                        remaining = request.limits.overall_seconds - (
                            time.monotonic() - monotonic_start
                        )
                        # Once the deadline/cancellation stops processes, reserve a bounded
                        # finalization grace. A late verification can never produce success.
                        async with asyncio.timeout(
                            max(0.01, min(5, remaining)) if remaining > 0 else 5
                        ):
                            self.path(execution_id)
                            if await self.primary_snapshot(request) != before:
                                raise DeveloperError("developer_boundary_violation")
                            if git_before is not None:
                                self.verify_checkout_path(repo)
                                if (
                                    await asyncio.to_thread(tree_digest, repo / ".git")
                                    != git_before
                                ):
                                    try:
                                        head = (
                                            (await self.git(repo, request, "rev-parse", "HEAD"))
                                            .decode()
                                            .strip()
                                        )
                                        result.update(
                                            head=head, head_changed=head != request.base_sha
                                        )
                                    except DeveloperError:
                                        pass
                                    raise DeveloperError("developer_boundary_violation")
                    except Exception as failure:
                        result["boundary_error"] = (
                            "developer_boundary_violation"
                            if isinstance(failure, DeveloperError)
                            and str(failure)
                            in {
                                "developer_boundary_violation",
                                "workspace_escape",
                                "git_metadata_invalid",
                            }
                            else "developer_boundary_verification_failed"
                        )
                        if result["error"] and result["error"] not in {
                            "developer_boundary_violation",
                            "developer_boundary_verification_failed",
                        }:
                            result["original_error"] = result["error"]
                        if (
                            not result["error"]
                            or result["boundary_error"] == "developer_boundary_violation"
                        ):
                            result["error"] = result["boundary_error"]
                if manifest is not None:
                    try:
                        no_symlinks(manifest)
                        manifest.write_text(
                            json.dumps(
                                {"task": task_id, "execution": execution_id, "active": False}
                            )
                        )
                    except DeveloperError:
                        if result["error"] and result["original_error"] is None:
                            result["original_error"] = result["error"]
                        result["error"] = "developer_boundary_violation"
                    except OSError:
                        if not result["error"]:
                            result["error"] = "workspace_finalization_failed"
                if result["error"] == "developer_boundary_violation":
                    result["boundary_error"] = "developer_boundary_violation"
                if result["boundary_error"]:
                    emit(result["boundary_error"], {"workspace": result["worktree"]})

            try:
                _, cancelled = await uncancellable(asyncio.create_task(finalize()))
                if cancelled:
                    result["cancelled"] = True
                    if not result["error"]:
                        result["error"] = "developer_cancelled"
            finally:

                async def cleanup():
                    nonlocal lock_released
                    release_failure = None
                    if lock is not None and not lock_released:
                        lock.close()
                        lock_released = True
                        try:
                            emit(
                                "worker_capacity_released",
                                {
                                    "worker_id": request.worker_selection.descriptor.worker_id,
                                },
                            )
                        except Exception as error:  # pragma: no cover - exercised via runner tests
                            release_failure = error
                    try:
                        if bound_worker_context is not None and bound_worker_entered:
                            await asyncio.to_thread(bound_worker_context.__exit__, None, None, None)
                    finally:
                        artifacts.close()
                    if release_failure is not None:
                        raise release_failure

                _, cancelled = await uncancellable(asyncio.create_task(cleanup()))
                if cancelled:
                    result["cancelled"] = True
                    if not result["error"]:
                        result["error"] = "developer_cancelled"
        if (
            time.monotonic() - monotonic_start > request.limits.overall_seconds
            and not result["error"]
        ):
            result["error"] = "developer_overall_timeout"
        if result["error"]:
            result["validation_passed"] = False
        result["completed_at"] = datetime.now(UTC).isoformat()
        result["duration_seconds"] = time.monotonic() - monotonic_start
        if transient_error is not None and result["error"] == str(transient_error):
            raise transient_error
        return result

    def cleanup(self, execution_id: str) -> None:
        """Internal operator service only. No API accepts deletion paths."""
        with self.maintenance_lock():
            repo = self.path(execution_id)
            owner = no_symlinks(repo.parent / "owner.json")
            if not owner.is_file() or owner.stat().st_size > 1024:
                raise DeveloperError("workspace_ownership_invalid")
            data = json.loads(owner.read_text())
            if data.get("execution") != execution_id or data.get("active") is not False:
                raise DeveloperError("workspace_active_or_unowned")
            identifier(data.get("task", ""))
            if not shutil.rmtree.avoids_symlink_attacks:
                raise DeveloperError("safe_cleanup_unavailable")
            self.path(execution_id)
            shutil.rmtree(repo.parent)
