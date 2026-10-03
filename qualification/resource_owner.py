"""One-shot qualified resource owner; factories own renderer and broker resources.

Factories/handles are trusted and must own every resource they create. A factory
exception is ambiguous and fences admission. Keep the event loop alive for retained
cleanup; event-loop/process death leaves the persistent marker fenced.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from qualification.admission import AdmissionController, Lease
from qualification.dns import QualificationError, _consume_exception, settle
from qualification.runtime_contracts import ExecutionPolicy, validate_digest


class ResourceHandle(Protocol):
    async def stop_and_confirm_cleanup(self) -> bool:
        """Return exactly True only after all owned resources are gone/reaped."""
        ...


T = TypeVar("T")
_RETAINED: set[asyncio.Task] = set()


class LeaseResourceOwner:
    def __init__(
        self,
        admission: AdmissionController,
        policy_digest: str,
        deadline_seconds: float,
        *,
        cleanup_grace: float = 1.0,
    ) -> None:
        validate_digest(policy_digest)
        if (
            type(deadline_seconds) not in (int, float)
            or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 30
        ):
            raise ValueError("invalid_deadline")
        if (
            type(cleanup_grace) not in (int, float)
            or not 0 < cleanup_grace <= 1
            or not math.isfinite(cleanup_grace)
        ):
            raise ValueError("invalid_cleanup_grace")
        self._admission = admission
        self._digest = policy_digest
        self._seconds = float(deadline_seconds)
        self._deadline: float | None = None
        self._grace = cleanup_grace
        self._cleanup_confirmed = False
        self._used = False
        self._closing = False
        self._lifecycle: asyncio.Task | None = None
        self._work: asyncio.Task | None = None

    @property
    def deadline(self) -> float:
        if self._deadline is None:
            raise QualificationError("owner_not_started")
        return self._deadline

    @property
    def cleanup_confirmed(self) -> bool:
        return self._cleanup_confirmed

    @property
    def cleanup_required(self) -> bool:
        """Busy admission owns nothing; an unresolved lifecycle still owns its fence."""
        return self._lifecycle is not None and not self._cleanup_confirmed

    async def run(
        self,
        factory: Callable[[Lease], Awaitable[ResourceHandle]],
        work: Callable[[ResourceHandle], Awaitable[T]],
    ) -> T:
        if self._used or self._closing:
            raise QualificationError("owner_terminal")
        self._used = True
        self._deadline = asyncio.get_running_loop().time() + self._seconds
        lease = self._admission.acquire(self._digest)

        async def lifecycle() -> T:
            confirmed = False
            try:
                handle = await factory(lease)
                try:
                    if self._closing:
                        raise asyncio.CancelledError
                    self._work = asyncio.create_task(work(handle))
                    return await self._work
                finally:
                    confirmed = await handle.stop_and_confirm_cleanup() is True
                    if not confirmed:
                        raise QualificationError("cleanup_unproven")
            finally:
                lease.finish(cleanup_confirmed=confirmed)
                self._cleanup_confirmed = confirmed

        self._lifecycle = asyncio.create_task(lifecycle())
        _RETAINED.add(self._lifecycle)
        self._lifecycle.add_done_callback(_RETAINED.discard)
        self._lifecycle.add_done_callback(_consume_exception)
        try:
            async with asyncio.timeout_at(self._deadline):
                result = await asyncio.shield(self._lifecycle)
            if self._closing or asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            return result
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        # No await before state transition; all callers share one lifecycle/cleanup task.
        if not self._closing:
            self._closing = True
            if self._work is not None and not self._work.done():
                self._work.cancel()
        task = self._lifecycle
        if task is not None:
            await settle(task, self._grace)
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        if task is not None:
            if not task.done():
                raise QualificationError("cleanup_pending")
            if task.cancelled() and not self._cleanup_confirmed:
                raise QualificationError("cleanup_unproven")
            if not task.cancelled() and task.exception() is not None:
                raise QualificationError("owner_failed") from task.exception()


class ResourceOwner(LeaseResourceOwner):
    """Browser wrapper preserves strict execution-policy validation and API."""

    def __init__(
        self, admission: AdmissionController, policy: ExecutionPolicy, *, cleanup_grace: float = 1.0
    ) -> None:
        if type(policy) is not ExecutionPolicy:
            raise ValueError("invalid_policy")
        self._policy = policy
        super().__init__(
            admission, policy.digest, policy.resources.deadline_seconds, cleanup_grace=cleanup_grace
        )

    async def run(
        self,
        factory: Callable[[Lease, ExecutionPolicy], Awaitable[ResourceHandle]],
        work: Callable[[ResourceHandle], Awaitable[T]],
    ) -> T:
        return await super().run(lambda lease: factory(lease, self._policy), work)
