"""Default-disabled Core adapter over qualified, no-host-access built-in compute."""

import asyncio
import hashlib
import json
import math
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.developer_contracts import WorkerSelectionSnapshot
from app.developer_process import DeveloperError
from app.shell_contracts import ShellExecutionSpec
from qualification.admission import AdmissionController, AdmissionDenied
from qualification.dns import QualificationError
from qualification.shell_compute import ShellIdentity, ShellProbeResources, verify_sources
from qualification.shell_owner import ShellOwner


class ShellRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def shell_policy_semantics() -> dict[str, object]:
    # Pure host-independent authority metadata: no source scan or native startup.
    value = asdict(ShellIdentity(1, 1))
    value.pop("uid")
    value.pop("gid")
    argv = list(value["argv"])
    argv[argv.index("--uid") + 1] = "<runtime-nonroot-uid>"
    argv[argv.index("--gid") + 1] = "<runtime-gid>"
    value["argv"] = argv
    value["operator"] = "trusted-runtime-nonroot-uid-gid"
    value["adapter"] = "zelo-shell-core-v1"
    return value


def private_directory(path: Path) -> tuple[int, int, int, int]:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("shell_directory_not_canonical")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("shell_directory_not_private")
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


@dataclass(frozen=True)
class ShellValidation:
    runtime: object
    identity: str
    source_metadata: tuple[tuple[int, ...], ...]


class ShellRuntime:
    """Trusted assembly chooses one persistent directory, never task/model input."""

    def __init__(self, admission_directory: Path) -> None:
        self.policy = ShellIdentity.current()
        self.admission_directory = admission_directory
        self._directory = private_directory(admission_directory)
        self.admission = AdmissionController(admission_directory)
        self._closing = False
        self._owners: set[ShellOwner] = set()
        self._validations: set[ShellProbeResources] = set()
        self._validation_failures: set[ShellProbeResources] = set()
        self._lock = threading.Lock()
        self.identity = self._identity()

    def _identity(self) -> str:
        return hashlib.sha256(
            canonical(
                {
                    "policy": self.policy.digest,
                    "admission": str(self.admission_directory),
                    "directory": self._directory,
                    "adapter": "zelo-shell-core-v1",
                }
            )
        ).hexdigest()

    def _validate_directory(self) -> None:
        self.policy.verify_current()
        if (
            private_directory(self.admission_directory) != self._directory
            or self.identity != self._identity()
            or self._closing
        ):
            raise ValueError("shell_configuration_changed")

    def _source_metadata(self) -> tuple[tuple[int, ...], ...]:
        result = []
        for source, _, _ in self.policy.sources:
            info = os.stat(source, follow_symlinks=False)
            result.append(
                tuple(
                    getattr(info, name)
                    for name in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_uid",
                        "st_gid",
                        "st_nlink",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                )
            )
        return tuple(result)

    def prepare_validation(self) -> ShellValidation:
        """Hash only approved fixed sources outside a Core writer transaction.

        Validation owns a short durable lease before file descriptors. Its explicit
        source settlement permits release; a stalled thread/unknown close retains
        the lease or fence. No source bytes are cached for later execution.
        """
        resources = ShellProbeResources()
        lease = None
        with self._lock:
            if self._closing:
                raise ShellRuntimeError("shell_execution_spec_invalid")
            if self._validation_failures:
                raise ShellRuntimeError("shell_cleanup_unproven")
            self._validations.add(resources)
        try:
            self._validate_directory()
            lease = self.admission.acquire(self.policy.digest)
            before = self._source_metadata()
            verify_sources(self.policy, resources)
            if before != self._source_metadata():
                raise ValueError("shell_source_changed")
            self._validate_directory()
            return ShellValidation(self, self.identity, before)
        except AdmissionDenied:
            raise ShellRuntimeError("shell_admission_denied") from None
        except (ValueError, OSError, DeveloperError):
            raise ShellRuntimeError("shell_execution_spec_invalid") from None
        finally:
            resources._terminal = True
            try:
                if lease is not None:
                    lease.finish(cleanup_confirmed=resources.cleanup_confirmed)
            except BaseException:
                with self._lock:
                    self._validation_failures.add(resources)
                raise ShellRuntimeError("shell_cleanup_unproven") from None
            finally:
                with self._lock:
                    if resources.cleanup_confirmed and resources not in self._validation_failures:
                        self._validations.discard(resources)

    def bind_validated(
        self,
        validation: ShellValidation | None,
        worker_selection: WorkerSelectionSnapshot,
        script: str,
        timeout_seconds: float,
    ) -> ShellExecutionSpec:
        try:
            self._validate_directory()
            if (
                validation is None
                or validation.runtime is not self
                or validation.identity != self.identity
                or validation.source_metadata != self._source_metadata()
            ):
                raise ValueError("shell_validation_stale")
            from app.schemas import ShellPayload

            payload = ShellPayload(script=script)
            return ShellExecutionSpec(
                worker_selection=worker_selection,
                script_digest=hashlib.sha256(payload.script.encode()).hexdigest(),
                policy_version=self.policy.digest,
                operator_identity=self.identity,
                timeout_seconds=timeout_seconds,
            )
        except (ValueError, OSError, DeveloperError):
            raise ShellRuntimeError("shell_execution_spec_invalid") from None

    def bind(
        self, selection: WorkerSelectionSnapshot, script: str, timeout_seconds: float
    ) -> ShellExecutionSpec:
        return self.bind_validated(self.prepare_validation(), selection, script, timeout_seconds)

    def matches_validated(
        self, validation: ShellValidation | None, spec: ShellExecutionSpec
    ) -> bool:
        try:
            self._validate_directory()
            checked = ShellExecutionSpec.model_validate(spec.model_dump(mode="json"))
            return (
                validation is not None
                and validation.runtime is self
                and validation.identity == self.identity
                and validation.source_metadata == self._source_metadata()
                and checked.policy_version == self.policy.digest
                and checked.operator_identity == self.identity
            )
        except (ValueError, OSError, DeveloperError, ShellRuntimeError):
            return False

    def matches(self, spec: ShellExecutionSpec) -> bool:
        try:
            return self.matches_validated(self.prepare_validation(), spec)
        except ShellRuntimeError:
            return False

    @staticmethod
    def request_matches(
        spec: ShellExecutionSpec, payload: dict[str, Any], timeout_seconds: float
    ) -> bool:
        from app.schemas import ShellPayload

        try:
            checked = ShellPayload.model_validate(payload)
            return (
                hashlib.sha256(checked.script.encode()).hexdigest() == spec.script_digest
                and type(timeout_seconds) in (int, float)
                and math.isfinite(timeout_seconds)
                and 0.01 <= timeout_seconds <= 2
                and timeout_seconds == spec.timeout_seconds
            )
        except ValueError:
            return False

    def _prune(self) -> None:
        for owner in tuple(self._owners):
            if owner.cleanup_confirmed:
                self._owners.discard(owner)
        # Only prepare_validation's finally block may release its record, after
        # both source settlement and durable lease completion. A terminal source
        # record alone is insufficient while that worker thread still owns FDs.

    def _pending(self) -> bool:
        self._prune()
        with self._lock:
            return bool(self._owners or self._validations)

    async def run(
        self,
        *,
        spec: ShellExecutionSpec,
        script: str,
        timeout_seconds: float,
        emit: Callable[[str, dict[str, Any]], None],
        deadline: float | None = None,
    ) -> dict[str, Any]:
        from app.schemas import ShellRunResult

        if not self.request_matches(spec, {"script": script}, timeout_seconds):
            raise ShellRuntimeError("shell_execution_spec_invalid")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise ShellRuntimeError("shell_execution_spec_invalid")
        self._prune()
        limit = asyncio.get_running_loop().time() + timeout_seconds
        if deadline is not None:
            limit = min(limit, deadline)
        try:
            async with asyncio.timeout_at(limit):
                validation = await asyncio.to_thread(self.prepare_validation)
                valid = self.matches_validated(validation, spec)
        except TimeoutError:
            raise ShellRuntimeError(
                "shell_cleanup_pending" if self._pending() else "shell_timeout"
            ) from None
        if not valid or self._closing:
            raise ShellRuntimeError("shell_execution_spec_stale")
        owner = None
        owner_started = False
        try:
            owner = ShellOwner(
                self.admission_directory, self.policy, timeout=timeout_seconds, deadline=limit
            )
            self._owners.add(owner)
            emit("shell_execution_started", {"policy_version": spec.policy_version})
            owner_started = True
            result = await owner.run_probe(script)
            if not owner.cleanup_confirmed:
                raise ShellRuntimeError("shell_cleanup_unproven")
            if self._closing:
                raise asyncio.CancelledError
        except AdmissionDenied:
            if owner is not None and not owner.cleanup_required:
                self._owners.discard(owner)
            raise ShellRuntimeError("shell_admission_denied") from None
        except (DeveloperError, QualificationError) as error:
            cause = error if isinstance(error, DeveloperError) else error.__cause__
            code = str(cause) if isinstance(cause, DeveloperError) else "shell_worker_error"
            mapped = {
                "process_timeout": "shell_timeout",
                "process_output_limit": "shell_output_limit",
            }.get(code, "shell_worker_error")
            if owner is not None:
                if not owner.cleanup_required:
                    self._owners.discard(owner)
                elif not owner.cleanup_confirmed:
                    mapped = "shell_cleanup_unproven"
            raise ShellRuntimeError(mapped) from None
        except ShellRuntimeError:
            raise
        except Exception:
            raise ShellRuntimeError(
                "shell_cleanup_unproven"
                if owner is not None and owner.cleanup_required
                else "shell_worker_error"
            ) from None
        finally:
            if owner is not None and not owner_started:
                self._owners.discard(owner)
            self._prune()
        stdout = result.stdout.decode("utf-8", "replace")
        stderr = result.stderr.decode("utf-8", "replace")
        if (
            len(stdout.encode()) > self.policy.output_bytes
            or len(stderr.encode()) > self.policy.error_bytes
        ):
            raise ShellRuntimeError("shell_output_limit")
        emit("shell_execution_completed", {"exit_code": result.exit_code})
        return ShellRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            duration_seconds=result.duration,
            policy_version=spec.policy_version,
            completed_at=datetime.now(UTC),
        ).model_dump(mode="json")

    async def aclose(self) -> None:
        with self._lock:
            self._closing = True
        outcomes = await asyncio.gather(
            *(owner.aclose() for owner in tuple(self._owners)), return_exceptions=True
        )
        deadline = asyncio.get_running_loop().time() + 1
        while self._pending() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        if self._pending() or any(isinstance(value, BaseException) for value in outcomes):
            raise ShellRuntimeError("shell_cleanup_unproven")


def configured_shell_runtime(settings: Settings) -> ShellRuntime | None:
    if not settings.shell_worker_enabled:
        return None
    if settings.shell_worker_admission is None:
        raise ValueError("shell_worker_configuration_incomplete")
    runtime = ShellRuntime(settings.shell_worker_admission)
    # Explicit enablement must qualify prerequisites before registration. This
    # resource-only lease cannot create a guest or reset an unknown fence.
    runtime.prepare_validation()
    return runtime
