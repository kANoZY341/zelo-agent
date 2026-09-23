"""Bounded process execution. No inherited environment or shell interpretation."""

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


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


async def stop_process(process: asyncio.subprocess.Process) -> None:
    def send(sig: signal.Signals) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 0.25)
    except TimeoutError:
        pass
    # Always kill the remaining group, including when its leader already exited.
    send(signal.SIGKILL)
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        raise DeveloperError("process_reap_failed") from None
    # PID namespaces kill escaped sessions when their init exits. Also verify that
    # no live member of our host process group remains (zombies cannot execute).
    deadline = time.monotonic() + 1
    while _group_alive(process.pid):
        if time.monotonic() >= deadline:
            raise DeveloperError("process_group_survived")
        await asyncio.sleep(0.01)


def _group_alive(group: int) -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] != "Z":
                return True
        except (OSError, ValueError, IndexError):
            continue
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
) -> ProcessResult:
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=environment() if env is None else env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            pass_fds=pass_fds,
            limit=8192,
        )
    except OSError:
        raise DeveloperError("executable_unavailable") from None

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
        async with asyncio.timeout(timeout):
            stdout, stderr, _ = await asyncio.gather(*readers)
            code = await process.wait()
        return ProcessResult(code, stdout, stderr, time.monotonic() - started)
    except TimeoutError:
        raise DeveloperError("process_timeout") from None
    finally:

        async def cleanup() -> None:
            await stop_process(process)
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

            async def discard(stream) -> None:
                while await stream.read(4096):
                    pass

            # Overflow stops a reader before EOF. Drain the killed process's pipes
            # without retaining bytes so transports close before the loop exits.
            try:
                async with asyncio.timeout(2):
                    await asyncio.gather(discard(process.stdout), discard(process.stderr))
            except TimeoutError:
                raise DeveloperError("process_pipe_close_failed") from None

        _, cancelled = await uncancellable(asyncio.create_task(cleanup()))
        if cancelled:
            raise asyncio.CancelledError


def run_sync(argv: list[str], *, cwd: Path, timeout: float = 5) -> ProcessResult:
    """Submission/preflight only; HTTP submission already runs in a worker thread.

    Execution uses run_process directly, so Git never blocks the runner event loop.
    """
    return asyncio.run(run_process(argv, cwd=cwd, timeout=timeout))
