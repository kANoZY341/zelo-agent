"""Owned fixed Linux Docker adapter. No shell, build, pull or image removal.

The local daemon, CLI executable, runtime ancestors and preparing operator are
trusted. CLI failures cannot establish container absence. All discovery uses the
unpredictable lease name AND label; unknown identities leave admission fenced.
"""

import asyncio
import os
import re
import stat
import tempfile
from pathlib import Path

from qualification.admission import Lease
from qualification.dns import QualificationError, _consume_exception
from qualification.renderer_contracts import RendererContract
from qualification.renderer_protocol import MESSAGE_BYTES, render_document, strict_json

LABEL = "org.zelo.renderer.lease"
TMPFS = "rw,nosuid,nodev,noexec,size=33554432"
ENV = [
    "LD_LIBRARY_PATH=/opt/libs",
    "HOME=/tmp",
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent",
    "PATH=/opt/browser:/opt/libs",
]
CLI = ("/usr/bin/docker", "--host=unix:///var/run/docker.sock")
CLI_BYTES = 131072


def create_arguments(contract: RendererContract, name: str, identity: str, profile: Path) -> tuple:
    contract.validate()
    return (
        "create",
        "--pull=never",
        "--interactive",
        "--name",
        name,
        "--label",
        f"{LABEL}={identity}",
        "--network=none",
        "--read-only",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--security-opt=seccomp={profile}",
        "--memory=536870912",
        "--memory-swap=536870912",
        "--pids-limit=128",
        "--cpu-period=100000",
        "--cpu-quota=50000",
        "--ipc=private",
        "--cgroupns=private",
        "--tmpfs",
        f"/tmp:{TMPFS}",
        "--shm-size=16777216",
        "--restart=no",
        "--log-driver=none",
        "--entrypoint=/browser-pipe",
        contract.execution.image_identity,
    )


def _same(actual, expected) -> bool:
    return type(actual) is type(expected) and actual == expected


def verify_image(data: dict, contract: RendererContract) -> None:
    config = data.get("Config", {})
    if (
        data.get("Id") != contract.execution.image_identity
        or data.get("Architecture") != "amd64"
        or data.get("Os") != "linux"
        or config.get("Entrypoint") != ["/browser-pipe"]
        or config.get("Cmd")
        or config.get("Volumes")
        or config.get("ExposedPorts")
        or config.get("OnBuild")
        or config.get("Healthcheck")
        or sorted(config.get("Env", [])) != sorted(ENV)
    ):
        raise QualificationError("image_configuration_drift")


def verify_container(data: dict, contract: RendererContract, name: str, identity: str) -> None:
    config, host = data.get("Config", {}), data.get("HostConfig", {})
    expected = {
        "Memory": 536870912,
        "MemorySwap": 536870912,
        "PidsLimit": 128,
        "CpuPeriod": 100000,
        "CpuQuota": 50000,
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "IpcMode": "private",
        "CgroupnsMode": "private",
        "PidMode": "",
        "UTSMode": "",
        "ShmSize": 16777216,
    }
    if any(not _same(host.get(key), value) for key, value in expected.items()):
        raise QualificationError("container_limits_drift")
    if (
        data.get("Name") != "/" + name
        or data.get("Image") != contract.execution.image_identity
        or config.get("Labels", {}).get(LABEL) != identity
        or config.get("User") != "65534:65534"
        or config.get("Entrypoint") != ["/browser-pipe"]
        or config.get("Cmd")
        or config.get("Volumes")
        or config.get("ExposedPorts")
        or config.get("Healthcheck")
        or config.get("WorkingDir") not in ("", "/")
        or config.get("OpenStdin") is not True
        or config.get("Tty") is not False
        or sorted(config.get("Env", [])) != sorted(ENV)
        or host.get("CapDrop") != ["ALL"]
        or host.get("Tmpfs") != {"/tmp": TMPFS}
        or host.get("RestartPolicy", {}).get("Name") != "no"
        or host.get("LogConfig", {}).get("Type") != "none"
        or host.get("PublishAllPorts", False) is not False
        or host.get("AutoRemove", False) is not False
        or data.get("State", {}).get("Running") is not False
    ):
        raise QualificationError("container_configuration_drift")
    for key in (
        "CapAdd",
        "Binds",
        "Devices",
        "DeviceRequests",
        "PortBindings",
        "Links",
        "ExtraHosts",
        "VolumesFrom",
        "Dns",
        "GroupAdd",
        "Sysctls",
        "DeviceCgroupRules",
        "CgroupParent",
        "StorageOpt",
    ):
        if host.get(key):
            raise QualificationError("container_authority_drift")
    # Docker may list its declared tmpfs mount; no other mount is accepted.
    for mount in data.get("Mounts", []):
        if mount.get("Type") != "tmpfs" or mount.get("Destination") != "/tmp":
            raise QualificationError("container_mount_drift")
    security = host.get("SecurityOpt", [])
    if type(security) is not list or len(security) != 2 or "no-new-privileges" not in security:
        raise QualificationError("container_security_drift")
    profiles = [s[8:] for s in security if type(s) is str and s.startswith("seccomp=")]
    if len(profiles) != 1 or strict_json(profiles[0].encode()) != strict_json(
        contract.profile.profile_bytes
    ):
        raise QualificationError("container_profile_drift")


async def _read_bounded(stream, limit: int) -> bytes:
    output = bytearray()
    while chunk := await stream.read(min(4096, limit + 1 - len(output))):
        output.extend(chunk)
        if len(output) > limit:
            raise QualificationError("process_output_limit")
    return bytes(output)


async def _discard(stream) -> None:
    while await stream.read(4096):
        pass


async def _kill_reap(process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    if process.stdin is not None:
        process.stdin.close()
    await asyncio.gather(_discard(process.stdout), _discard(process.stderr))
    await process.wait()


class DockerHandle:
    """Construct before any resource creation; only trusted constructor seams."""

    def __init__(
        self,
        lease: Lease,
        contract: RendererContract,
        bundle: Path,
        runtime: Path,
        *,
        spawn=asyncio.create_subprocess_exec,
    ) -> None:
        self.contract, self.bundle, self.runtime = contract, bundle, runtime
        self.identity = lease.identity
        self.name = "zelo-renderer-" + lease.identity
        self._spawn = spawn
        self._commands: set[asyncio.Task] = set()
        self._attach: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._render: asyncio.Task | None = None
        self._close: asyncio.Task | None = None
        self._case: Path | None = None
        self._attempted = False
        self._acknowledged = False
        self._cli_cleanup_uncertain = False

    def _environment(self) -> dict[str, str]:
        if self._case is None:
            self._profile()
        # Cleanup must remain possible after profile drift. The owned directory
        # contains no Docker config/contexts/credentials; HOME is not inherited.
        return {"DOCKER_CONFIG": str(self._case)}

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
                        asyncio.create_task(_read_bounded(process.stderr, 16384)),
                    ]
                    output, _ = await asyncio.gather(*readers)
                    if await process.wait() != 0:
                        raise QualificationError("docker_command_failed")
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
        # Retain completed commands until cleanup too: a spawn failure is ambiguous.
        return await asyncio.shield(task)

    async def _inspect(self, kind: str, identity: str) -> dict:
        raw = await self._command(kind, "inspect", identity)
        data = strict_json(raw)
        if type(data) is not list or len(data) != 1 or type(data[0]) is not dict:
            raise QualificationError("inspect_shape")
        return data[0]

    def _profile(self) -> Path:
        if self._case is None:
            info = self.runtime.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.geteuid()
            ):
                raise QualificationError("unsafe_renderer_runtime")
            self._case = Path(tempfile.mkdtemp(prefix="renderer-", dir=self.runtime))
            path = self._case / "seccomp.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(self.contract.profile.profile_bytes)
                file.flush()
                os.fsync(file.fileno())
        path = self._case / "seccomp.json"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or file.read(65537) != self.contract.profile.profile_bytes
            ):
                raise QualificationError("profile_file_drift")
        return path

    async def render(self, text: str, media_type: str, deadline: float):
        if self._close is not None or self._attempted:
            raise QualificationError("renderer_terminal")
        async with asyncio.timeout_at(deadline):
            self.contract.verify_bundle(self.bundle)
            profile = self._profile()
            verify_image(
                await self._inspect("image", self.contract.execution.image_identity), self.contract
            )
            self._attempted = True  # before create: acknowledgement may be lost
            await self._command(*create_arguments(self.contract, self.name, self.identity, profile))
            self._acknowledged = True
            self.contract.verify_bundle(self.bundle)
            self._profile()
            verify_image(
                await self._inspect("image", self.contract.execution.image_identity), self.contract
            )
            verify_container(
                await self._inspect("container", self.name), self.contract, self.name, self.identity
            )
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
                    limit=MESSAGE_BYTES,
                )
            )
            process = await asyncio.shield(self._attach)
            self._stderr = asyncio.create_task(_read_bounded(process.stderr, 16384))
            self._render = asyncio.create_task(
                render_document(process.stdout, process.stdin, deadline, text, media_type)
            )
            done, _ = await asyncio.wait(
                (self._stderr, self._render), return_when=asyncio.FIRST_COMPLETED
            )
            if self._stderr in done:
                self._stderr.result()
                raise QualificationError("browser_pipe_closed")
            result = self._render.result()
            if self._stderr.done():
                self._stderr.result()
            return result

    async def _discover(self) -> list[str]:
        raw = await self._command(
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"label={LABEL}={self.identity}",
            "--format",
            "{{.ID}}",
        )
        try:
            ids = raw.decode("ascii").splitlines()
        except UnicodeError:
            raise QualificationError("discovery_invalid") from None
        if len(ids) > 1 or any(re.fullmatch(r"[0-9a-f]{64}", cid) is None for cid in ids):
            raise QualificationError("discovery_invalid")
        return ids

    async def stop_and_confirm_cleanup(self) -> bool:
        if self._close is None:
            self._close = asyncio.create_task(self._cleanup())
            self._close.add_done_callback(_consume_exception)
        return await asyncio.shield(self._close)

    async def _cleanup(self) -> bool:
        # Each step attempts its own cleanup even when another step fails.
        confirmed = True
        if self._stderr is not None and self._stderr.done() and not self._stderr.cancelled():
            if self._stderr.exception() is not None:
                confirmed = False
        for task in (self._render, self._stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(t for t in (self._render, self._stderr) if t is not None), return_exceptions=True
        )
        if self._attach is not None:
            try:
                await _kill_reap(await self._attach)
            except Exception:
                confirmed = False
        # Settle every create/inspect CLI before daemon discovery/removal.
        await asyncio.gather(*self._commands, return_exceptions=True)
        if self._attempted:
            try:
                ids = await self._discover()
                if not ids and not self._acknowledged:
                    raise QualificationError("create_outcome_unknown")
                for cid in ids:
                    data = await self._inspect("container", cid)
                    if (
                        data.get("Name") != "/" + self.name
                        or data.get("Config", {}).get("Labels", {}).get(LABEL) != self.identity
                        or data.get("Id") != cid
                    ):
                        raise QualificationError("cleanup_identity_mismatch")
                    try:
                        await self._command("container", "rm", "--force", cid)
                    except Exception:
                        # Only subsequent successful enumeration can confirm absence.
                        pass
                if await self._discover():
                    raise QualificationError("container_remaining")
            except Exception:
                confirmed = False
        if self._case is not None:
            try:
                (self._case / "seccomp.json").unlink(missing_ok=True)
                self._case.rmdir()
            except OSError:
                confirmed = False
        return confirmed and not self._cli_cleanup_uncertain
