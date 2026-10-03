"""Qualification-only Shell owner; no Core registration or authority grant."""

import math
from pathlib import Path

from app.developer_process import DeveloperError, ProcessResult
from qualification.admission import AdmissionController, Lease
from qualification.resource_owner import LeaseResourceOwner
from qualification.shell_compute import ShellIdentity, ShellProbeResources, run_probe


class _ShellHandle:
    def __init__(self, identity: ShellIdentity, deadline: float) -> None:
        self._identity = identity
        self._deadline = deadline
        self._resources = ShellProbeResources()
        self._started = False
        self._closed = False

    async def execute(self, script: str) -> ProcessResult:
        if self._started or self._closed:
            raise DeveloperError("shell_handle_terminal")
        self._started = True
        return await run_probe(
            script,
            identity=self._identity,
            deadline=self._deadline,
            resources=self._resources,
        )

    async def stop_and_confirm_cleanup(self) -> bool:
        # Lifecycle only calls this after work has terminated, including its
        # protected native and pin cleanup. Never cancel creation independently.
        self._closed = True
        if not self._started:
            self._resources._terminal = True  # Resource-free factory, work never entered.
        return self._resources.cleanup_confirmed


class ShellOwner(LeaseResourceOwner):
    """One trusted persistent directory; one-shot owner with a distinct identity."""

    def __init__(
        self,
        runtime_directory: Path,
        identity: ShellIdentity,
        *,
        timeout: float = 2.0,
        cleanup_grace: float = 1.0,
    ) -> None:
        if type(identity) is not ShellIdentity:
            raise DeveloperError("shell_identity_invalid")
        identity.verify_current()
        if not runtime_directory.is_absolute() or runtime_directory.resolve() != runtime_directory:
            raise DeveloperError("shell_runtime_directory_invalid")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= identity.max_seconds
        ):
            raise DeveloperError("shell_probe_input_invalid")
        self._identity = identity
        super().__init__(
            AdmissionController(runtime_directory),
            identity.digest,
            timeout,
            cleanup_grace=cleanup_grace,
        )

    async def run_probe(self, script: str) -> ProcessResult:
        self._identity.verify_current()

        async def factory(lease: Lease) -> _ShellHandle:
            # Admission's durable marker precedes all source verification/pins/spawn.
            if lease.policy_digest != self._identity.digest:
                raise DeveloperError("shell_identity_invalid")
            self._handle = _ShellHandle(self._identity, self.deadline)
            return self._handle

        try:
            return await super().run(factory, lambda handle: handle.execute(script))
        except TimeoutError:
            # Either absolute-deadline layer may win. Cleanup evidence remains
            # independent; never map an uncertain termination to a clean timeout.
            if self.cleanup_confirmed:
                raise DeveloperError("process_timeout") from None
            raise
