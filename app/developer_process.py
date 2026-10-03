"""Bounded process execution. No inherited environment or shell interpretation."""

import asyncio
import errno
import math
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DeveloperError(Exception):
    """Only fixed diagnostic codes cross the task/audit boundary."""

    def __init__(self, code: str, *, exit_code: int | None = None, diagnostic: str | None = None):
        super().__init__(code)
        self.exit_code = exit_code
        self.diagnostic = diagnostic


def environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp/zelo-home",
        "XDG_CONFIG_HOME": "/tmp/zelo-home/config",
        "XDG_CACHE_HOME": "/tmp/zelo-home/cache",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_ATTR_NOSYSTEM": "1",
    }


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration: float


async def uncancellable(task: asyncio.Task):
    """Finish cleanup even if more than one operator cancellation arrives."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    return result, cancelled


class NativeExecutionObservation:
    """Trusted per-call cleanup evidence; pending creation never means no resources.

    Caller owns this object before startup. It is not a task payload or global
    registry snapshot. A record is one-shot and only the native helper mutates it.
    """

    def __init__(self) -> None:
        self._claimed = False
        self._creation_terminal = False
        self._spawn_pending = False
        self._process: OwnedProcess | None = None
        self._support_fd: int | None = None
        self._io_settled = False

    @property
    def cleanup_confirmed(self) -> bool:
        if (
            not self._creation_terminal
            or not self._io_settled
            or self._spawn_pending
            or self._support_fd is not None
        ):
            return False
        process = self._process
        return process is None or (
            process.reaped
            and process._pidfd is None
            and all(
                pipe is None or pipe.closed
                for pipe in (process._child.stdin, process._child.stdout, process._child.stderr)
            )
        )

    @property
    def creation_started(self) -> bool:
        return self._claimed

    def _claim(self) -> None:
        if self._claimed:
            raise DeveloperError("process_observation_reused")
        self._claimed = True

    def _no_child(self) -> None:
        self._spawn_pending = False
        self._creation_terminal = True
        self._io_settled = True


_RETAINED_IDENTITIES: set["OwnedProcess"] = set()


class _InputProtocol(asyncio.streams.FlowControlMixin):
    def __init__(self, loop):
        super().__init__(loop=loop)
        self.closed = loop.create_future()

    def connection_lost(self, exc):
        super().connection_lost(exc)
        if not self.closed.done():
            self.closed.set_result(None)

    def _get_close_waiter(self, stream):
        return self.closed


class OwnedProcess:
    """Own an unreaped child identity until group termination is confirmed.

    Popen has no asyncio child watcher. waitid(WNOWAIT) observes exit without
    releasing the leader PID; pidfd binds observation to this exact child. No
    code may poll/wait/reap the private Popen before this owner's final reap.
    """

    def __init__(
        self,
        argv: tuple[str, ...],
        kwargs: dict[str, Any],
        *,
        observation: NativeExecutionObservation | None = None,
        deadline: float | None = None,
    ) -> None:
        if not hasattr(os, "P_PIDFD") or not hasattr(os, "pidfd_open"):
            raise DeveloperError("process_identity_unavailable")
        try:
            support = os.pidfd_open(os.getpid())
            if observation is not None:
                observation._support_fd = support
            try:
                try:
                    os.waitid(os.P_PIDFD, support, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                except OSError as error:
                    if error.errno != errno.ECHILD:
                        raise
            finally:
                os.close(support)
                if observation is not None:
                    observation._support_fd = None
        except OSError:
            raise DeveloperError("process_identity_unavailable") from None
        if kwargs.get("start_new_session") is not True:
            raise DeveloperError("process_identity_unowned")
        limit = kwargs.pop("limit", 8192)
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stderr = asyncio.StreamReader(limit=limit)
        self.stdin = None
        self._transports = []
        self._attached_pipes = set()
        self._code = None
        self.reaped = False
        self._stop_task = None
        self._pidfd = None
        self._acquisition_cleanup = False
        if deadline is not None and time.monotonic() >= deadline:
            raise DeveloperError("process_timeout")
        if observation is not None:
            observation._spawn_pending = True
        try:
            self._child = subprocess.Popen(argv, **kwargs)
        except (OSError, ValueError, TypeError):
            # Normal validation/exec failure paths own their failed construction.
            if observation is not None:
                observation._no_child()
            raise
        except BaseException:
            # Interruption/unknown constructor failure is not no-child evidence.
            # Keep creation pending and fence; never infer a released numeric PID.
            raise
        if observation is not None:
            observation._process = self
            observation._spawn_pending = False
        self.pid = self._child.pid
        _RETAINED_IDENTITIES.add(self)

    def observe(self) -> int | None:
        if self.reaped:
            raise DeveloperError("process_identity_unowned")
        if self._pidfd is not None:
            kind, identity = os.P_PIDFD, self._pidfd
        elif self._acquisition_cleanup:
            # Construction owns this direct child before any await or watcher.
            # Emergency cleanup only: P_PID WNOWAIT verifies it is still our
            # waitable child, reserving its PID before every group signal.
            kind, identity = os.P_PID, self.pid
        else:
            raise DeveloperError("process_identity_unowned")
        try:
            status = os.waitid(kind, identity, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        except ChildProcessError:
            raise DeveloperError("process_identity_lost") from None
        if status is not None:
            if status.si_pid != self.pid:
                raise DeveloperError("process_identity_lost")
            self._code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
        return self._code

    @property
    def returncode(self) -> int | None:
        return self._code if self.reaped else self.observe()

    async def wait(self) -> int:
        if self.reaped:
            assert self._code is not None
            return self._code
        while self.observe() is None:
            await asyncio.sleep(0.01)
        assert self._code is not None
        return self._code

    def signal_group(self, sig: signal.Signals) -> None:
        # Verify the exact pidfd-bound child remains waitable. An unreaped leader
        # reserves the numeric PGID even after exit. No await before signalling.
        self.observe()
        try:
            os.killpg(self.pid, sig)
        except ProcessLookupError:
            pass

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        for pipe, reader in ((self._child.stdout, self.stdout), (self._child.stderr, self.stderr)):
            transport, _ = await loop.connect_read_pipe(
                lambda reader=reader: asyncio.StreamReaderProtocol(reader), pipe
            )
            self._transports.append(transport)
            self._attached_pipes.add(pipe)
        if self._child.stdin is not None:
            protocol = _InputProtocol(loop)
            transport, _ = await loop.connect_write_pipe(lambda: protocol, self._child.stdin)
            self._transports.append(transport)
            self._attached_pipes.add(self._child.stdin)
            self.stdin = asyncio.StreamWriter(transport, protocol, None, loop)

    def close_pipes(self) -> None:
        for transport in self._transports:
            transport.close()
        for pipe in (self._child.stdin, self._child.stdout, self._child.stderr):
            if pipe is not None and pipe not in self._attached_pipes:
                pipe.close()

    async def close_and_confirm_pipes(self) -> None:
        self.close_pipes()
        deadline = time.monotonic() + 0.25
        while any(
            pipe is not None and not pipe.closed
            for pipe in (self._child.stdin, self._child.stdout, self._child.stderr)
        ):
            if time.monotonic() >= deadline:
                raise DeveloperError("process_pipe_close_failed")
            await asyncio.sleep(0)

    def reap(self) -> None:
        self.observe()
        if self._code is None:
            raise DeveloperError("process_reap_failed")
        self._child.wait(timeout=0)
        self.reaped = True
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None
        _RETAINED_IDENTITIES.discard(self)


async def spawn_owned_process(
    *argv: str,
    observation: NativeExecutionObservation | None = None,
    deadline: float | None = None,
    **kwargs: Any,
) -> OwnedProcess:
    if observation is not None:
        if type(observation) is not NativeExecutionObservation:
            raise DeveloperError("process_observation_invalid")
        observation._claim()
    process = None
    try:
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise DeveloperError("process_deadline_invalid")
        options = {}
        if observation is not None:
            options["observation"] = observation
        if deadline is not None:
            options["deadline"] = deadline
        process = OwnedProcess(argv, kwargs, **options)
        try:
            process._pidfd = os.pidfd_open(process.pid)
        except OSError:
            process._acquisition_cleanup = True
            raise DeveloperError("process_identity_unavailable") from None
        async with asyncio.timeout_at(deadline):
            await process.connect()
        if deadline is not None and time.monotonic() >= deadline:
            raise DeveloperError("process_timeout")
        if observation is not None:
            observation._creation_terminal = True
    except BaseException:
        # A constructor failure after creating a child is still reachable through
        # its per-execution record; never infer this from an exception string.
        owned = (
            process
            if process is not None
            else (observation._process if observation is not None else None)
        )
        if owned is not None:
            await stop_process(owned)
            if observation is not None:
                observation._creation_terminal = True
                observation._io_settled = True  # No caller I/O tasks were created.
        elif observation is not None and not observation._spawn_pending:
            observation._no_child()
        raise
    return process


async def stop_process(process: OwnedProcess) -> None:
    if not isinstance(process, OwnedProcess):
        raise DeveloperError("process_identity_unowned")

    async def stop():
        confirmed = False
        try:
            process.signal_group(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 0.25)
            except TimeoutError:
                pass
            process.signal_group(signal.SIGKILL)
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                raise DeveloperError("process_reap_failed") from None
            deadline = time.monotonic() + 1
            while _group_alive(process.pid):
                if time.monotonic() >= deadline:
                    raise DeveloperError("process_group_survived")
                await asyncio.sleep(0.01)
            confirmed = True
        finally:
            await process.close_and_confirm_pipes()
            # Unknown cleanup retains ownership; never release/reuse its PGID.
            if confirmed:
                process.reap()

    if process._stop_task is None:
        process._stop_task = asyncio.create_task(stop())
    _, cancelled = await uncancellable(process._stop_task)
    if cancelled:
        raise asyncio.CancelledError


def _group_alive(group: int) -> bool:
    # Complete relevant /proc visibility is a trusted host prerequisite. A
    # vanished entry is harmless; unreadable/malformed observations are unknown.
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == group and fields[0] != "Z":
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError, IndexError):
                raise DeveloperError("process_group_observation_failed") from None
    except OSError:
        raise DeveloperError("process_group_observation_failed") from None
    return False


async def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdout_limit: int = 65536,
    stderr_limit: int = 16384,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
    line_limit: int | None = None,
    event_limit: int | None = None,
    pass_fds: tuple[int, ...] = (),
    observation: NativeExecutionObservation | None = None,
    deadline: float | None = None,
) -> ProcessResult:
    started = time.monotonic()
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise DeveloperError("process_deadline_invalid")
    deadline = min(started + timeout, deadline) if deadline is not None else started + timeout
    try:
        process = await spawn_owned_process(
            *argv,
            cwd=cwd,
            env=environment() if env is None else env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            pass_fds=pass_fds,
            limit=8192,
            deadline=deadline,
            **({"observation": observation} if observation is not None else {}),
        )
    except OSError:
        raise DeveloperError("executable_unavailable") from None
    except TimeoutError:
        raise DeveloperError("process_timeout") from None

    async def read(stream, limit: int, jsonl: bool = False) -> bytes:
        result = bytearray()
        line_bytes = events = 0
        while chunk := await stream.read(4096):
            if len(result) + len(chunk) > limit:
                raise DeveloperError("process_output_limit")
            if jsonl:
                parts = chunk.split(b"\n")
                for index, part in enumerate(parts):
                    line_bytes += len(part)
                    if line_limit is not None and line_bytes > line_limit:
                        raise DeveloperError("jsonl_line_limit")
                    if index < len(parts) - 1:
                        events += 1
                        line_bytes = 0
                        if event_limit is not None and events > event_limit:
                            raise DeveloperError("jsonl_event_limit")
            result.extend(chunk)
        return bytes(result)

    async def write() -> None:
        if process.stdin is not None:
            try:
                process.stdin.write(stdin or b"")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

    readers = [
        asyncio.create_task(read(process.stdout, stdout_limit, line_limit is not None)),
        asyncio.create_task(read(process.stderr, stderr_limit)),
        asyncio.create_task(write()),
    ]
    try:
        async with asyncio.timeout_at(deadline):
            stdout, stderr, _ = await asyncio.gather(*readers)
            code = await process.wait()
        return ProcessResult(code, stdout, stderr, time.monotonic() - started)
    except TimeoutError:
        raise DeveloperError("process_timeout") from None
    finally:

        async def cleanup() -> None:
            stop_error = None
            try:
                await stop_process(process)
            except BaseException as error:
                stop_error = error
            finally:
                # Child uncertainty never short-circuits independent I/O cleanup.
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)

            async def discard(stream) -> None:
                while await stream.read(4096):
                    pass

            drain_error = None
            try:
                async with asyncio.timeout(2):
                    await asyncio.gather(discard(process.stdout), discard(process.stderr))
            except (TimeoutError, OSError, RuntimeError) as error:
                drain_error = error
            if observation is not None:
                observation._io_settled = drain_error is None and all(
                    reader.done() for reader in readers
                )
            if stop_error is not None:
                raise stop_error
            if drain_error is not None:
                raise DeveloperError("process_pipe_close_failed") from None

        _, cancelled = await uncancellable(asyncio.create_task(cleanup()))
        if cancelled:
            raise asyncio.CancelledError


def run_sync(argv: list[str], *, cwd: Path, timeout: float = 5) -> ProcessResult:
    """Submission/preflight only; HTTP submission already runs in a worker thread.

    Execution uses run_process directly, so Git never blocks the runner event loop.
    """
    return asyncio.run(run_process(argv, cwd=cwd, timeout=timeout))
