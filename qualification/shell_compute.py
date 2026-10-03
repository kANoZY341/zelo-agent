"""Qualification-only, built-in Shell compute; never a registered Core worker.

Fixed installed artifacts and non-root same-UID operator/ancestors are trusted.
No workspace, network, credentials, writes, new process or policy fallback.
"""

import asyncio
import hashlib
import math
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from app.developer_artifacts import pin_artifact
from app.developer_process import DeveloperError, ProcessResult, run_process

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


def verify_file(path: Path, digest: str, *, owner: int = 0) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise DeveloperError("shell_source_invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
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
        os.close(fd)


def verify_sources() -> None:
    for source, digest, _ in SOURCES:
        verify_file(Path(source), digest)


def arguments() -> list[str]:
    uid, gid = os.geteuid(), os.getegid()
    if uid == 0:
        raise DeveloperError("shell_nonroot_required")
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
    for source, _, destination in SOURCES:
        if destination is not None:
            argv.extend(("--ro-bind", source, destination))
    argv.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"))
    for directory in ("/proc", "/dev", "/tmp", "/"):
        argv.extend(("--remount-ro", directory))
    for name, value in ENVIRONMENT.items():
        argv.extend(("--setenv", name, value))
    argv.extend(
        (
            "--chdir",
            "/tmp",
            "--",
            "/usr/bin/prlimit",
            f"--as={MEMORY_BYTES}:{MEMORY_BYTES}",
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
    return argv


@contextmanager
def _pin_sources():
    with ExitStack() as stack:
        artifacts = []
        for source, digest, _ in SOURCES:
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


def _pinned_arguments(artifacts) -> list[str]:
    if len(artifacts) != len(SOURCES):
        raise DeveloperError("shell_source_invalid")
    argv = arguments()
    argv[0] = f"/proc/self/fd/{artifacts[0].fd}"
    pinned = {
        source: artifact.fd for (source, _, _), artifact in zip(SOURCES, artifacts, strict=True)
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


async def run_probe(script: str, *, timeout: float = MAX_SECONDS) -> ProcessResult:
    if (
        not isinstance(script, str)
        or not script
        or "\0" in script
        or len(script.encode("utf-8")) > INPUT_BYTES
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= MAX_SECONDS
    ):
        raise DeveloperError("shell_probe_input_invalid")
    arguments()  # Fail closed on root before any source resource is opened.
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        async with asyncio.timeout_at(deadline):
            # Qualification setup is synchronous and owns every pinned FD before
            # yielding. The absolute deadline budget still covers this local work;
            # no guest starts if source verification consumes that budget.
            verify_sources()
            with _pin_sources() as artifacts:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise DeveloperError("process_timeout")
                return await run_process(
                    _pinned_arguments(artifacts),
                    cwd=Path("/"),
                    timeout=remaining,
                    stdout_limit=OUTPUT_BYTES,
                    stderr_limit=ERROR_BYTES,
                    stdin=script.encode("utf-8"),
                    env=dict(ENVIRONMENT),
                    pass_fds=tuple(artifact.fd for artifact in artifacts),
                )
    except TimeoutError:
        raise DeveloperError("process_timeout") from None
