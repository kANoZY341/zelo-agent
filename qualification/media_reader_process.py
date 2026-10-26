"""Owned fixed Media reader container; qualification-only, no image build or pull."""

import asyncio
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from qualification import media_reader_container as container
from qualification import media_reader_fixture as fixture
from qualification import media_reader_seccomp as seccomp
from qualification.admission import Lease
from qualification.dns import QualificationError, _consume_exception
from qualification.renderer_process import _kill_reap, _read_bounded
from qualification.renderer_protocol import strict_json

CLI = ("/usr/bin/docker", "--host=unix:///var/run/docker.sock")
CLI_BYTES = 131072


@dataclass(frozen=True)
class ReaderProcessResult:
    case: str
    stdout: bytes
    stderr: bytes
    exit_code: int


class DockerReaderHandle:
    """Own exactly one fixed reader container under one admission lease."""

    def __init__(
        self,
        lease: Lease,
        contract: container.ReaderContainerContract,
        runtime: Path,
        profile_bytes: bytes,
        *,
        spawn=asyncio.create_subprocess_exec,
        runtime_probe=None,
        stdout_limit=fixture.STDOUT_BYTES,
        stderr_limit=fixture.STDERR_BYTES,
    ) -> None:
        try:
            contract.validate()
        except container.Refused as exc:
            raise QualificationError("reader_handle_contract") from exc
        if type(profile_bytes) is not bytes or profile_bytes != seccomp.profile_bytes():
            raise QualificationError("reader_handle_profile")
        self.contract = contract
        self.image = contract.image_identity
        self.runtime = runtime
        self.profile_bytes = profile_bytes
        self.identity = lease.identity
        if re.fullmatch(r"[0-9a-f]{64}", self.identity) is None:
            raise QualificationError("reader_handle_lease")
        self.name = "zelo-media-reader-" + self.identity
        if type(stdout_limit) is not int or not 0 < stdout_limit <= fixture.STDOUT_BYTES:
            raise QualificationError("reader_stdout_limit")
        if type(stderr_limit) is not int or not 0 < stderr_limit <= fixture.STDERR_BYTES:
            raise QualificationError("reader_stderr_limit")
        self._spawn = spawn
        self._runtime_probe = runtime_probe
        self._stdout_limit = stdout_limit
        self._stderr_limit = stderr_limit
        self._commands: set[asyncio.Task] = set()
        self._attach: asyncio.Task | None = None
        self._stdout: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._close: asyncio.Task | None = None
        self._case: Path | None = None
        self._attempted = False
        self._acknowledged = False
        self._cli_cleanup_uncertain = False

    def _case_directory(self) -> Path:
        if self._case is None:
            info = self.runtime.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.geteuid()
            ):
                raise QualificationError("reader_runtime_unsafe")
            self._case = Path(tempfile.mkdtemp(prefix="native-reader-", dir=self.runtime))
        return self._case

    def _environment(self) -> dict[str, str]:
        return {"DOCKER_CONFIG": str(self._case_directory()), "GOMAXPROCS": "1"}

    def _profile(self) -> Path:
        case = self._case_directory()
        path = case / "seccomp.json"
        if not path.exists():
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
            try:
                if os.write(fd, self.profile_bytes) != len(self.profile_bytes):
                    raise QualificationError("reader_profile_write")
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            raw = bytearray()
            while chunk := os.read(fd, min(65536, len(self.profile_bytes) + 1 - len(raw))):
                raw.extend(chunk)
                if len(raw) > len(self.profile_bytes):
                    raise QualificationError("reader_profile_drift")
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or bytes(raw) != self.profile_bytes
            ):
                raise QualificationError("reader_profile_drift")
        finally:
            os.close(fd)
        return path

    async def _command(self, *args: str) -> bytes:
        async def owned() -> bytes:
            creation = asyncio.create_task(
                self._spawn(
                    *CLI,
                    *args,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._environment(),
                    close_fds=True,
                    limit=CLI_BYTES + 1,
                )
            )
            process = None
            readers = []
            try:
                async with asyncio.timeout(3):
                    process = await asyncio.shield(creation)
                    readers = [
                        asyncio.create_task(_read_bounded(process.stdout, CLI_BYTES)),
                        asyncio.create_task(_read_bounded(process.stderr, fixture.STDERR_BYTES)),
                    ]
                    output, _ = await asyncio.gather(*readers)
                    if await process.wait() != 0:
                        raise QualificationError("reader_docker_command")
                    return output
            finally:
                for task in readers:
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                try:
                    if process is None:
                        process = await creation
                    await _kill_reap(process)
                except BaseException:
                    self._cli_cleanup_uncertain = True
                    raise

        task = asyncio.create_task(owned())
        self._commands.add(task)
        task.add_done_callback(_consume_exception)
        return await asyncio.shield(task)

    async def _inspect(self, kind: str, identity: str) -> dict:
        raw = await self._command(kind, "inspect", identity)
        value = strict_json(raw)
        if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
            raise QualificationError("reader_inspect_shape")
        return value[0]

    async def stream(self, payload: bytes, deadline: float) -> ReaderProcessResult:
        if self._close is not None or self._attempted:
            raise QualificationError("reader_terminal")
        try:
            case = fixture.identify(payload)
        except fixture.Refused:
            raise QualificationError("reader_input_identity") from None
        async with asyncio.timeout_at(deadline):
            profile = self._profile()
            try:
                container.verify_image(
                    await self._inspect("image", self.image), self.contract, self.identity
                )
            except container.Refused as exc:
                raise QualificationError("reader_image_refused") from exc
            self._attempted = True
            try:
                create = container.create_arguments(
                    self.contract, self.name, self.identity, profile
                )
            except container.Refused as exc:
                raise QualificationError("reader_create_contract") from exc
            await self._command(*create)
            self._acknowledged = True
            self._profile()
            try:
                container.verify_image(
                    await self._inspect("image", self.image), self.contract, self.identity
                )
                container.verify_container(
                    await self._inspect("container", self.name),
                    self.contract,
                    self.name,
                    self.identity,
                    self.profile_bytes,
                )
            except container.Refused as exc:
                raise QualificationError("reader_container_refused") from exc
            self._attach = asyncio.create_task(
                self._spawn(
                    *CLI,
                    "start",
                    "--attach",
                    "--interactive",
                    self.name,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._environment(),
                    close_fds=True,
                    limit=max(self._stdout_limit, self._stderr_limit) + 1,
                )
            )
            process = await asyncio.shield(self._attach)
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise QualificationError("reader_pipe_missing")
            if self._runtime_probe is not None:
                data = await self._inspect("container", self.name)
                try:
                    container.verify_container(
                        data,
                        self.contract,
                        self.name,
                        self.identity,
                        self.profile_bytes,
                        running=True,
                    )
                except container.Refused as exc:
                    raise QualificationError("reader_runtime_probe") from exc
                await self._runtime_probe(data)
            self._stdout = asyncio.create_task(_read_bounded(process.stdout, self._stdout_limit))
            self._stderr = asyncio.create_task(_read_bounded(process.stderr, self._stderr_limit))
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            wait_closed = getattr(process.stdin, "wait_closed", None)
            if wait_closed is not None:
                await wait_closed()
            stdout, stderr = await asyncio.gather(self._stdout, self._stderr)
            code = await process.wait()
            return ReaderProcessResult(case, stdout, stderr, code)

    async def _discover(self) -> list[str]:
        raw = await self._command(
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"label={container.LEASE_LABEL}={self.identity}",
            "--format",
            "{{.ID}}",
        )
        try:
            identifiers = raw.decode("ascii").splitlines()
        except UnicodeError:
            raise QualificationError("reader_discovery") from None
        if len(identifiers) > 1 or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None for value in identifiers
        ):
            raise QualificationError("reader_discovery")
        return identifiers

    async def stop_and_confirm_cleanup(self) -> bool:
        if self._close is None:
            self._close = asyncio.create_task(self._cleanup())
            self._close.add_done_callback(_consume_exception)
        return await asyncio.shield(self._close)

    async def _cleanup(self) -> bool:
        confirmed = True
        for task in (self._stdout, self._stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._stdout, self._stderr) if task is not None),
            return_exceptions=True,
        )
        if self._attach is not None:
            try:
                await _kill_reap(await self._attach)
            except Exception:
                confirmed = False
        await asyncio.gather(*self._commands, return_exceptions=True)
        if self._attempted:
            try:
                identifiers = await self._discover()
                if not identifiers and not self._acknowledged:
                    raise QualificationError("reader_create_unknown")
                for cid in identifiers:
                    data = await self._inspect("container", cid)
                    if (
                        data.get("Id") != cid
                        or data.get("Name") != "/" + self.name
                        or data.get("Config", {}).get("Labels", {}).get(container.LEASE_LABEL)
                        != self.identity
                    ):
                        raise QualificationError("reader_cleanup_identity")
                    try:
                        await self._command("container", "rm", "--force", cid)
                    except Exception:
                        pass
                if await self._discover():
                    raise QualificationError("reader_container_remaining")
            except Exception:
                confirmed = False
        if self._case is not None:
            try:
                (self._case / "seccomp.json").unlink(missing_ok=True)
                self._case.rmdir()
            except OSError:
                confirmed = False
        return confirmed and not self._cli_cleanup_uncertain
