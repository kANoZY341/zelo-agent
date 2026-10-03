"""Opt-in fixed harmless Shell ownership proof; never a Core activation command."""

import argparse
import asyncio
import json
import os
import select
import signal
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.developer_process import DeveloperError, spawn_owned_process, stop_process
from qualification.admission import AdmissionDenied
from qualification.dns import QualificationError
from qualification.shell_compute import ShellIdentity
from qualification.shell_owner import ShellOwner
from scripts import qualify_shell_compute as boundary_proof
from scripts.qualify_browser_renderer import command

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "runtime/shell-owner-qualification"


def private_directory(path: Path) -> None:
    info = path.lstat()
    if (
        not path.is_absolute()
        or path.resolve() != path
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RuntimeError("shell_fixture_directory_invalid")


async def wait_spawn(owner: ShellOwner):
    async with asyncio.timeout(1):
        while True:
            handle = getattr(owner, "_handle", None)
            if handle is not None and handle._resources.native._process is not None:
                return handle._resources.native._process
            await asyncio.sleep(0.005)


async def fixture_parent(root: Path) -> None:
    if root.parent != BASE.resolve() or not root.name.startswith("owner-proof-"):
        raise RuntimeError("shell_fixture_path_invalid")
    private_directory(root)
    identity = ShellIdentity.current()
    directory = root / "crash"
    # First establish actual effective guest policy with this same frozen identity.
    state_owner = ShellOwner(directory, identity)
    state = await state_owner.run_probe(boundary_proof.STATUS)
    if state.exit_code or state.stderr:
        raise RuntimeError("shell_fixture_state_invalid")
    boundary_proof.check_status(state.stdout.decode("ascii"))
    owner = ShellOwner(directory, identity)
    operation = asyncio.create_task(owner.run_probe("while :; do :; done\n"))
    try:
        process = await wait_spawn(owner)
        print(
            json.dumps(
                {
                    "launcher_pid": process.pid,
                    "state_cleanup_confirmed": state_owner.cleanup_confirmed,
                }
            ),
            flush=True,
        )
        await operation
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await owner.aclose()


@dataclass(frozen=True)
class ProcessIdentity:
    parent: int
    group: int
    session: int
    start_ticks: int
    name: str


def process_identity(pid: int) -> ProcessIdentity:
    text = Path(f"/proc/{pid}/stat").read_text()
    if int(text.split(" ", 1)[0]) != pid:
        raise RuntimeError("shell_fixture_process_identity_invalid")
    fields = text.rsplit(")", 1)[1].split()
    return ProcessIdentity(
        int(fields[1]),
        int(fields[2]),
        int(fields[3]),
        int(fields[19]),
        text.split("(", 1)[1].rsplit(")", 1)[0],
    )


def descendants(parent: int) -> dict[int, ProcessIdentity]:
    entries = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            entries[int(path.name)] = process_identity(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    owned = {}
    pending = {parent}
    while pending:
        next_level = {pid for pid, identity in entries.items() if identity.parent in pending}
        if len(owned) + len(next_level) > 32:
            raise RuntimeError("shell_fixture_tree_limit")
        owned.update({pid: entries[pid] for pid in next_level})
        pending = next_level
    return owned


def bind_descendant_pidfd(pid: int, expected: ProcessIdentity) -> int:
    if process_identity(pid) != expected:
        raise RuntimeError("shell_fixture_ancestry_changed")
    descriptor = os.pidfd_open(pid)
    try:
        if process_identity(pid) != expected or select.select([descriptor], [], [], 0)[0]:
            raise RuntimeError("shell_fixture_ancestry_changed")
        # Kernel fdinfo binds this descriptor to the still-live sampled task.
        lines = Path(f"/proc/self/fdinfo/{descriptor}").read_text().splitlines()
        identities = [int(line.split(":", 1)[1]) for line in lines if line.startswith("Pid:")]
        if identities != [pid]:
            raise RuntimeError("shell_fixture_pidfd_identity_changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


async def crash_fence(root: Path, identity: ShellIdentity) -> dict:
    directory = root / "crash"
    directory.mkdir(mode=0o700)
    parent = None
    descriptors = []
    stderr = None
    try:
        parent = await spawn_owned_process(
            sys.executable,
            "-m",
            "scripts.qualify_shell_owner",
            "--allow-native-shell-probes",
            "--fixture-parent",
            cwd=REPO,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            deadline=asyncio.get_running_loop().time() + 2,
        )
        parent.stdin.write(json.dumps({"root": str(root)}).encode() + b"\n")
        await parent.stdin.drain()
        parent.stdin.close()

        async def bounded_stderr():
            total = 0
            while chunk := await parent.stderr.read(4096):
                total += len(chunk)
                if total > 16384:
                    raise RuntimeError("shell_fixture_stderr_limit")

        stderr = asyncio.create_task(bounded_stderr())
        async with asyncio.timeout(2):
            line = await parent.stdout.readline()
        value = json.loads(line)
        if (
            set(value) != {"launcher_pid", "state_cleanup_confirmed"}
            or type(value["launcher_pid"]) is not int
            or value["state_cleanup_confirmed"] is not True
        ):
            raise RuntimeError("shell_fixture_ready_invalid")
        launcher = value["launcher_pid"]
        async with asyncio.timeout(1):
            while True:
                tree = descendants(parent.pid)
                if launcher in tree and any(value.name == "dash" for value in tree.values()):
                    break
                await asyncio.sleep(0.005)
        # Bind every currently running fixed fixture descendant before killing
        # only its directly owned parent. Never signal numeric descendant PIDs.
        for pid, sampled in tree.items():
            descriptors.append(bind_descendant_pidfd(pid, sampled))
        if descendants(parent.pid) != tree:
            raise RuntimeError("shell_fixture_ancestry_changed")
        parent.signal_group(signal.SIGKILL)
        await stop_process(parent)
        async with asyncio.timeout(2):
            while any(not select.select([fd], [], [], 0)[0] for fd in descriptors):
                await asyncio.sleep(0.005)
        await stderr
        try:
            await ShellOwner(directory, identity).run_probe("printf forbidden\n")
        except AdmissionDenied:
            pass
        else:
            raise RuntimeError("shell_crash_fence_missing")
        if not (directory / "active.marker").stat().st_size:
            raise RuntimeError("shell_crash_marker_cleared")
        return {
            "case": "owner_death_preserves_fence_and_death_bound_tree_exits",
            "passed": True,
            "state_guest_cleanup_confirmed": True,
            "parent_reaped": parent.reaped,
            "descendant_pidfds_exited": len(descriptors),
            "marker_preserved": True,
        }
    finally:
        try:
            if parent is not None and not parent.reaped:
                await stop_process(parent)
        finally:
            try:
                if stderr is not None:
                    if not stderr.done():
                        stderr.cancel()
                    await asyncio.gather(stderr, return_exceptions=True)
            finally:
                errors = []
                for descriptor in descriptors:
                    try:
                        os.close(descriptor)
                    except OSError as error:
                        errors.append(error)
                if errors:
                    raise RuntimeError("shell_fixture_descriptor_close_failed") from None


async def exercise(root: Path, report: dict, identity: ShellIdentity) -> None:
    directory = root / "admission"
    directory.mkdir(mode=0o700)
    owners = []

    async def probe(script, *, timeout=2.0):
        owner = ShellOwner(directory, identity, timeout=timeout)
        owners.append(owner)
        try:
            return await owner.run_probe(script)
        except QualificationError as error:
            if owner.cleanup_confirmed and isinstance(error.__cause__, DeveloperError):
                raise error.__cause__ from None
            raise

    await boundary_proof.exercise(root, report, probe=probe)
    if len(owners) != 14 or not all(owner.cleanup_confirmed for owner in owners):
        raise RuntimeError("shell_owner_cleanup_unproven")
    report["boundary_native_launchers"] = report.pop("native_launchers")
    report["boundary_native_launchers_reaped"] = report.pop("native_launchers_reaped")
    report["owned_boundary_cases"] = 14
    report["all_boundary_admission_released_after_cleanup"] = True
    busy = ShellOwner(directory, identity)
    operation = asyncio.create_task(busy.run_probe("while :; do :; done\n"))
    try:
        process = await wait_spawn(busy)
        try:
            await ShellOwner(directory, identity).run_probe("printf forbidden\n")
        except AdmissionDenied:
            report["cases"].append({"case": "independent_owner_contention_denied", "passed": True})
        else:
            raise RuntimeError("shell_busy_admission_missing")
        if busy.cleanup_confirmed:
            raise RuntimeError("shell_early_cleanup_ack")
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        try:
            await operation
        except asyncio.CancelledError:
            pass
        else:
            raise RuntimeError("shell_cancel_missing")
        await busy.aclose()
        if not process.reaped or not busy.cleanup_confirmed:
            raise RuntimeError("shell_cancel_cleanup_unproven")
        fresh_owner = ShellOwner(directory, identity)
        fresh = await fresh_owner.run_probe("printf READMITTED\n")
        if fresh.exit_code or fresh.stdout != b"READMITTED" or fresh.stderr:
            raise RuntimeError("shell_readmission_failed")
        report["cases"].append(
            {
                "case": "repeated_cancel_cleanup_and_fresh_readmission",
                "passed": True,
                "busy_launcher_reaped": process.reaped,
                "fresh_cleanup_confirmed": fresh_owner.cleanup_confirmed,
            }
        )
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await busy.aclose()
    report["cases"].append(await crash_fence(root, identity))


def qualify() -> tuple[Path, bool]:
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_directory(BASE)
    root = Path(tempfile.mkdtemp(prefix="owner-proof-", dir=BASE)).resolve()
    report = {
        "status": "incomplete",
        "cases": [],
        "core_worker_activated": False,
        "external_network_used": False,
        "source_artifacts_are_independent_provenance": False,
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        identity = ShellIdentity.current()
        report["git_commit"] = command(
            ["/usr/bin/git", "-C", str(REPO), "rev-parse", "HEAD"]
        ).strip()
        report["worktree_clean"] = not bool(
            command(["/usr/bin/git", "-C", str(REPO), "status", "--porcelain"])
        )
        report["identity"] = asdict(identity)
        report["identity_digest"] = identity.digest
        asyncio.run(exercise(root, report, identity))
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        data = json.dumps(report, indent=2).encode() + b"\n"
        if len(data) > 131072:
            raise RuntimeError("shell_report_limit")
        path = root / "result.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    return path, report["status"] == "passed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-native-shell-probes", action="store_true")
    parser.add_argument("--fixture-parent", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.allow_native_shell_probes:
        parser.error("explicit --allow-native-shell-probes is required")
    if args.fixture_parent:
        raw = sys.stdin.buffer.readline(8193)
        if len(raw) > 8192:
            raise SystemExit("fixture input limit")
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {"root"} or type(value["root"]) is not str:
            raise SystemExit("fixture input invalid")
        root = Path(value["root"])
        if not root.is_absolute() or root.resolve() != root:
            raise SystemExit("fixture path invalid")
        asyncio.run(fixture_parent(root))
        return
    path, passed = qualify()
    print(f"EVIDENCE={path}")
    if not passed:
        raise SystemExit("native Shell owner qualification failed; inspect private report")


if __name__ == "__main__":
    main()
