"""Qualification-only, built-in Shell compute; never a registered Core worker.

Fixed installed artifacts and non-root same-UID operator/ancestors are trusted.
No workspace, network, credentials, writes, new process or policy fallback.
"""

import asyncio
import hashlib
import json
import math
import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.developer_artifacts import pin_artifact
from app.developer_process import (
    DeveloperError,
    NativeExecutionObservation,
    ProcessResult,
    run_process,
)

# Observed installed artifact bytes on the qualification host; not provenance.
SOURCES = (
    ("/usr/bin/bwrap", "e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71", None),
    (
        "/usr/bin/dash",
        "86d31f6fb799e91fa21bad341484564510ca287703a16e9e46c53338776f4f42",
        "/usr/bin/dash",
    ),
    (
        "/usr/bin/prlimit",
        "17064f67e650d6152a6902b013aab496b54c87587c8eea6f5023aafee6154069",
        "/usr/bin/prlimit",
    ),
    (
        "/usr/lib/x86_64-linux-gnu/libc.so.6",
        "3a15d66867d83762c7f2f1e37359cb8f6c5743edb369c65285cb0b1c4f7498bf",
        "/lib/x86_64-linux-gnu/libc.so.6",
    ),
    (
        "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "c20a2dc8917c755f02b94049356320fe1f62ac7d9f8994731f807d9df39302da",
        "/lib64/ld-linux-x86-64.so.2",
    ),
    (
        "/usr/lib/x86_64-linux-gnu/libsmartcols.so.1.1.0",
        "0560fded774642441657d3cf3125ac62234545dbe30725ed97cc6a79ba45791b",
        "/lib/x86_64-linux-gnu/libsmartcols.so.1",
    ),
)
INPUT_BYTES = 65536
OUTPUT_BYTES = 65536
ERROR_BYTES = 16384
MEMORY_BYTES = 268435456
MAX_SECONDS = 2.0
ENVIRONMENT = {"PATH": "/usr/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}


class ShellProbeResources:
    """Trusted per-execution source/pin/native ownership, never task input."""

    def __init__(self) -> None:
        self.native = NativeExecutionObservation()
        self._verification_pending = 0
        self._pins_settled = True
        self._terminal = False

    @property
    def cleanup_confirmed(self) -> bool:
        return (
            self._terminal
            and self._verification_pending == 0
            and self._pins_settled
            and (not self.native.creation_started or self.native.cleanup_confirmed)
        )


def verify_file(
    path: Path,
    digest: str,
    *,
    owner: int = 0,
    resources: ShellProbeResources | None = None,
) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise DeveloperError("shell_source_invalid")
    if resources is not None:
        resources._verification_pending += 1
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or before.st_mode & 0o7022
            or not 0 < before.st_size <= 16777216
        ):
            raise DeveloperError("shell_source_invalid")
        value = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as source:
            total = 0
            while block := source.read(65536):
                total += len(block)
                if total > 16777216:
                    raise DeveloperError("shell_source_invalid")
                value.update(block)
        after = os.fstat(fd)
        fields = (
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
        if (
            any(getattr(before, name) != getattr(after, name) for name in fields)
            or total != before.st_size
            or value.hexdigest() != digest
        ):
            raise DeveloperError("shell_source_changed")
    finally:
        if fd is not None:
            os.close(fd)
        if resources is not None:
            resources._verification_pending -= 1


def verify_sources(identity: "ShellIdentity", resources: ShellProbeResources | None = None) -> None:
    for source, digest, _ in identity.sources:
        verify_file(Path(source), digest, resources=resources)


@dataclass(frozen=True)
class ShellIdentity:
    """Closed immutable native proof identity; not provenance or Core authority."""

    uid: int
    gid: int
    sources: tuple[tuple[str, str, str | None], ...] = field(default=SOURCES, init=False)
    environment: tuple[tuple[str, str], ...] = field(default=tuple(ENVIRONMENT.items()), init=False)
    input_bytes: int = field(default=INPUT_BYTES, init=False)
    output_bytes: int = field(default=OUTPUT_BYTES, init=False)
    error_bytes: int = field(default=ERROR_BYTES, init=False)
    memory_bytes: int = field(default=MEMORY_BYTES, init=False)
    max_seconds: float = field(default=MAX_SECONDS, init=False)
    cpu_seconds: int = field(default=2, init=False)
    open_files: int = field(default=32, init=False)
    processes: int = field(default=0, init=False)
    core_bytes: int = field(default=0, init=False)
    file_bytes: int = field(default=0, init=False)
    artifact_handling: str = field(default="verified-sealed-memfd-and-guest-data-0500", init=False)
    schema: str = field(default="zelo-shell-owner-qualification-v1", init=False)
    argv: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if type(self.uid) is not int or not 1 <= self.uid <= 4294967294:
            raise DeveloperError("shell_nonroot_required")
        if type(self.gid) is not int or not 0 <= self.gid <= 4294967294:
            raise DeveloperError("shell_identity_invalid")
        uid, gid = self.uid, self.gid
        argv = [
            "/usr/bin/bwrap",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--disable-userns",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--uid",
            str(uid),
            "--gid",
            str(gid),
            "--clearenv",
            "--hostname",
            "zelo-shell-proof",
            "--tmpfs",
            "/",
            "--dir",
            "/usr",
            "--dir",
            "/usr/bin",
            "--dir",
            "/lib",
            "--dir",
            "/lib/x86_64-linux-gnu",
            "--dir",
            "/lib64",
        ]
        for source, _, destination in self.sources:
            if destination is not None:
                argv.extend(("--ro-bind", source, destination))
        argv.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"))
        for directory in ("/proc", "/dev", "/tmp", "/"):
            argv.extend(("--remount-ro", directory))
        for name, value in self.environment:
            argv.extend(("--setenv", name, value))
        argv.extend(
            (
                "--chdir",
                "/tmp",
                "--",
                "/usr/bin/prlimit",
                f"--as={self.memory_bytes}:{self.memory_bytes}",
                "--cpu=2:2",
                "--core=0:0",
                "--fsize=0:0",
                "--nofile=32:32",
                "--nproc=0:0",
                "--",
                "/usr/bin/dash",
                "-s",
            )
        )
        object.__setattr__(self, "argv", tuple(argv))

    @classmethod
    def current(cls) -> "ShellIdentity":
        return cls(os.geteuid(), os.getegid())

    def verify_current(self) -> None:
        if type(self) is not ShellIdentity or self != ShellIdentity(self.uid, self.gid):
            raise DeveloperError("shell_identity_invalid")
        if (os.geteuid(), os.getegid()) != (self.uid, self.gid):
            raise DeveloperError("shell_identity_operator_changed")

    @property
    def digest(self) -> str:
        self.verify_current()
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()


def arguments() -> list[str]:
    return list(ShellIdentity.current().argv)


@contextmanager
def _pin_sources(
    identity: ShellIdentity | None = None, resources: ShellProbeResources | None = None
):
    identity = ShellIdentity.current() if identity is None else identity
    if resources is not None:
        resources._pins_settled = False
    stack = ExitStack()
    try:
        artifacts = []
        for source, digest, _ in identity.sources:
            artifact = stack.enter_context(
                pin_artifact(
                    Path(source),
                    approved_path=source,
                    approved_digest=digest,
                    name="zelo-shell-qualified-artifact",
                )
            )
            artifact.verify()
            artifacts.append(artifact)
        yield artifacts
    finally:
        stack.close()
        if resources is not None:
            resources._pins_settled = True


def _pinned_arguments(artifacts, identity: ShellIdentity | None = None) -> list[str]:
    identity = ShellIdentity.current() if identity is None else identity
    identity.verify_current()
    if len(artifacts) != len(identity.sources):
        raise DeveloperError("shell_source_invalid")
    argv = list(identity.argv)
    argv[0] = f"/proc/self/fd/{artifacts[0].fd}"
    pinned = {
        source: artifact.fd
        for (source, _, _), artifact in zip(identity.sources, artifacts, strict=True)
    }
    result = []
    index = 0
    while index < len(argv):
        if argv[index] == "--ro-bind":
            result.extend(
                ("--perms", "0500", "--ro-bind-data", str(pinned[argv[index + 1]]), argv[index + 2])
            )
            index += 3
        else:
            result.append(argv[index])
            index += 1
    return result


async def run_probe(
    script: str,
    *,
    timeout: float = MAX_SECONDS,
    identity: ShellIdentity | None = None,
    observation: NativeExecutionObservation | None = None,
    deadline: float | None = None,
    resources: ShellProbeResources | None = None,
) -> ProcessResult:
    if resources is not None:
        if (
            type(resources) is not ShellProbeResources
            or observation is not None
            or resources._terminal
        ):
            raise DeveloperError("shell_resources_invalid")
        observation = resources.native
    try:
        identity = ShellIdentity.current() if identity is None else identity
        identity.verify_current()
        if (
            not isinstance(script, str)
            or not script
            or "\0" in script
            or len(script.encode("utf-8")) > identity.input_bytes
            or type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= identity.max_seconds
        ):
            raise DeveloperError("shell_probe_input_invalid")
        now = asyncio.get_running_loop().time()
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise DeveloperError("shell_probe_input_invalid")
        deadline = min(now + timeout, deadline) if deadline is not None else now + timeout
        if deadline <= now:
            raise DeveloperError("process_timeout")
        async with asyncio.timeout_at(deadline):
            verify_sources(identity, **({"resources": resources} if resources is not None else {}))
            with _pin_sources(
                identity, **({"resources": resources} if resources is not None else {})
            ) as artifacts:
                identity.verify_current()
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise DeveloperError("process_timeout")
                return await run_process(
                    _pinned_arguments(artifacts, identity),
                    cwd=Path("/"),
                    timeout=remaining,
                    deadline=deadline,
                    stdout_limit=identity.output_bytes,
                    stderr_limit=identity.error_bytes,
                    stdin=script.encode("utf-8"),
                    env=dict(identity.environment),
                    pass_fds=tuple(artifact.fd for artifact in artifacts),
                    **({"observation": observation} if observation is not None else {}),
                )
    except TimeoutError:
        raise DeveloperError("process_timeout") from None
    finally:
        if resources is not None:
            resources._terminal = True
