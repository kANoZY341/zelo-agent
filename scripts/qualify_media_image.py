#!/usr/bin/env python3
"""Explicit sealed-file/guest-copy operator proof; never import copied packages."""

import argparse
import asyncio
import dataclasses
import errno
import hashlib
import json
import mmap
import os
import resource
import stat
import sys
import tempfile
import time
from pathlib import Path

if not __debug__:
    raise SystemExit("media_image_optimized_python_refused")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import NativeExecutionObservation, run_process  # noqa: E402
from app.developer_sandbox import python_compute_sandbox, python_compute_sandbox_plan  # noqa: E402
from qualification import media_image as m  # noqa: E402
from qualification.media_image_guest import (  # noqa: E402
    GUEST_BOOTSTRAP,
    GUEST_CASES,
    IMAGE_ROOT,
    READY_PATH,
    SOURCE_ROOT,
)
from qualification.media_wheel import WHEEL_FILENAME, WHEEL_SHA256  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402

SOURCE_FILES = (
    "__init__.py",
    "artifacts.py",
    "runtime_contracts.py",
    "media_preparation.py",
    "media_wheel.py",
)
MUTATIONS = (
    "write",
    "truncate",
    "chmod_file",
    "create",
    "unlink",
    "rename",
    "mkdir",
    "rmdir",
    "chmod_directory",
)


class QualificationRefused(ValueError):
    pass


def confinement_policy() -> dict:
    return {
        "schema": "zelo-media-image-copy-confinement-v1",
        "base_namespace": python_compute_sandbox_plan(),
        "image_root": IMAGE_ROOT,
        "source_root": SOURCE_ROOT,
        "trusted_source_files": SOURCE_FILES,
        "transfer": "explicit-perms-file-then-directory-modes-and-read-only-root",
        "capabilities": "all-dropped",
        "bootstrap_sha256": hashlib.sha256(GUEST_BOOTSTRAP.encode()).hexdigest(),
        "package_path_on_sys_path": False,
        "flags": ("-I", "-S", "-B"),
        "environment": shared.confinement_policy()["environment"],
        "guest_added_environment": {"PWD": "/workspace"},
        "mutation_denials": {
            "write_truncate": (errno.EACCES, errno.EROFS),
            "metadata": (errno.EROFS,),
        },
        "setup_inherited_limits": {
            "fds": 256,
            "cpu_seconds": 30,
            "address_space_bytes": 512 * 1024**2,
            "file_bytes": m.FILE_BYTES,
            "core_bytes": 0,
        },
        "scanner_limits": {
            "fds": 64,
            "cpu_seconds": 10,
            "address_space_bytes": 512 * 1024**2,
            "file_core_bytes": 0,
            "nproc_supplementary": 0,
        },
        "stdout_bytes": 65536,
        "stderr_bytes": 16384,
        "native_seconds": 30,
        "timeout_control_seconds": 2,
        "matrix_native_seconds": 120,
        "attempts": 4,
        "cancel_channel": "one-byte-exclusive-private-fifo",
        "hard_filesystem_deadline": False,
        "aggregate_host_tmpfs_memory_enforced": False,
        "package_import_or_execution": False,
        "instruction_trust": "none",
        "runtime_authority": False,
    }


def _argv(handles, manifest, case: str, sentinel: Path, ready_fd: int = -1) -> list[str]:
    command = [
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-B",
        "-c",
        GUEST_BOOTSTRAP,
        "copy" if case == "setup_failure" else case,
        str(sentinel),
        os.readlink("/proc/self/ns/net"),
    ]
    argv = python_compute_sandbox(command)
    index = argv.index("--remount-ro")
    additions = ["--cap-drop", "ALL", "--dir", SOURCE_ROOT, "--dir", SOURCE_ROOT + "/qualification"]
    for name in SOURCE_FILES:
        additions += [
            "--ro-bind",
            str(ROOT / "qualification" / name),
            SOURCE_ROOT + "/qualification/" + name,
        ]
    for path, _ in sorted(manifest.directories, key=lambda pair: (pair[0].count("/"), pair[0])):
        additions += ["--perms", "0700", "--dir", IMAGE_ROOT + ("/" + path if path else "")]
    for item, fd in handles:
        additions += [
            "--perms",
            f"{item.mode:04o}",
            "--file",
            str(fd),
            IMAGE_ROOT + "/" + item.path,
        ]
    for path, mode in reversed(
        sorted(manifest.directories, key=lambda pair: (pair[0].count("/"), pair[0]))
    ):
        additions += ["--chmod", f"{mode:04o}", IMAGE_ROOT + ("/" + path if path else "")]
    if case == "setup_failure":
        additions[:0] = [
            "--ro-bind",
            str(sentinel.parent / "absent-setup-source"),
            "/opt/absent-setup-source",
        ]
    if ready_fd >= 0:
        additions += ["--bind-fd", str(ready_fd), READY_PATH]
    argv[index:index] = additions
    return argv


def _source_identity() -> dict:
    result = {}
    for name in SOURCE_FILES:
        path = ROOT / "qualification" / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_mode & 0o022
                or not 1 <= before.st_size <= 262144
            ):
                raise QualificationRefused("media_image_source")
            digest = hashlib.sha256()
            total = 0
            while block := os.read(fd, min(65536, before.st_size - total + 1)):
                total += len(block)
                if total > before.st_size:
                    raise QualificationRefused("media_image_source")
                digest.update(block)
            after = os.fstat(fd)
            if total != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise QualificationRefused("media_image_source")
            result[name] = digest.hexdigest()
        finally:
            os.close(fd)
    return result


def _accepted(value, manifest, case: str, host_namespace: str) -> bool:
    expected = json.loads(json.dumps(dataclasses.asdict(manifest)))
    if (
        type(value) is not dict
        or set(value)
        != {
            "status",
            "case",
            "manifest",
            "digest",
            "after_manifest",
            "after_digest",
            "probes",
            "imports_absent",
            "environment_closed",
        }
        or value["status"] != "image_scanned"
        or value["case"] != case
        or value["manifest"] != expected
        or value["after_manifest"] != expected
        or shared._digest(value["manifest"]) != manifest.digest
        or shared._digest(value["after_manifest"]) != manifest.digest
        or value["digest"] != manifest.digest
        or value["after_digest"] != manifest.digest
        or value["imports_absent"] is not True
        or value["environment_closed"] is not True
    ):
        return False
    probes = value["probes"]
    if case == "copy":
        return probes == {}
    if case == "mutations":
        return (
            type(probes) is dict
            and set(probes) == set(MUTATIONS)
            and all(
                type(code) is int
                and code
                in (
                    (errno.EACCES, errno.EROFS) if name in ("write", "truncate") else (errno.EROFS,)
                )
                for name, code in probes.items()
            )
        )
    if case == "network":
        return shared._network_accepted(probes, host_namespace)
    if case == "host_file":
        return (
            type(probes) is dict
            and set(probes) == {"inaccessible"}
            and probes["inaccessible"] is True
        )
    if case == "imports":
        return (
            type(probes) is dict
            and set(probes) == {"PIL", "app"}
            and all(v is True for v in probes.values())
        )
    if case == "memory":
        return type(probes) is dict and set(probes) == {"refused"} and probes["refused"] is True
    return False


def _seal_controls(handles) -> dict:
    results = {"files": 0, "write": 0, "grow": 0, "shrink": 0, "shared_write_mmap": 0}
    for item, fd in handles:
        actions = {
            "write": lambda fd=fd: os.pwrite(fd, b"x", 0),
            "grow": lambda fd=fd, size=item.size: os.ftruncate(fd, size + 1),
        }
        if item.size:
            actions["shrink"] = lambda fd=fd, size=item.size: os.ftruncate(fd, size - 1)
            actions["shared_write_mmap"] = lambda fd=fd: mmap.mmap(
                fd, 1, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE
            )
        for name, action in actions.items():
            try:
                value = action()
            except OSError as error:
                if error.errno not in (errno.EPERM, errno.EACCES):
                    raise QualificationRefused("media_image_seal_control") from None
                results[name] += 1
            else:
                if isinstance(value, mmap.mmap):
                    value.close()
                raise QualificationRefused("media_image_seal_control")
        results["files"] += 1
    return results


async def _repeated_cancel(operation, fd: int, deadline: float):
    ready = asyncio.Event()

    async def wait_ready():
        await shared._ready(fd, deadline)
        ready.set()
        await asyncio.Event().wait()

    owner = asyncio.create_task(shared._own_operation(operation, cancel_wait=wait_ready))
    try:
        await asyncio.wait_for(ready.wait(), max(0.001, deadline - time.monotonic()))
        first = owner.cancel()
        await asyncio.sleep(0)
        second = owner.cancel()
        try:
            await owner
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            if not first or not second:
                raise QualificationRefused("media_image_repeated_cancel_not_delivered") from None
            return None, "operator_repeated_cancelled"
        raise QualificationRefused("media_image_repeated_cancel_not_delivered")
    finally:
        if not owner.done():
            owner.cancel()

        async def settle():
            try:
                await owner
            except asyncio.CancelledError:
                pass

        _, cancelled = await shared.uncancellable(asyncio.create_task(settle()))
        if cancelled:
            raise asyncio.CancelledError


async def _native(image, case: str, proof: Path, label: str, matrix_deadline: float) -> dict:
    deadline = min(matrix_deadline, time.monotonic() + (2 if case == "timeout" else 30))
    source = _source_identity()
    host_namespace = os.readlink("/proc/self/ns/net")
    sentinel = proof / "host-only-sentinel"
    handles = image._begin_consumer()
    observation = NativeExecutionObservation()
    ready_fd = None
    operation = None
    try:
        if case in ("cancel", "cancel_repeated"):
            fifo = proof / (case + "-ready.fifo")
            os.mkfifo(fifo, mode=0o600)
            ready_fd = os.open(fifo, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        raw = json.dumps(
            dataclasses.asdict(image.identity.manifest), sort_keys=True, separators=(",", ":")
        ).encode()
        if len(raw) > 65536:
            raise QualificationRefused("media_image_input_bound")
        operation = asyncio.create_task(
            run_process(
                _argv(
                    handles,
                    image.identity.manifest,
                    case,
                    sentinel,
                    ready_fd if ready_fd is not None else -1,
                ),
                cwd=ROOT,
                timeout=max(0.001, deadline - time.monotonic()),
                deadline=deadline,
                stdin=raw,
                env=confinement_policy()["environment"],
                pass_fds=tuple(fd for _, fd in handles)
                + ((ready_fd,) if ready_fd is not None else ()),
                stdout_limit=65536,
                stderr_limit=16384,
                observation=observation,
            )
        )
        if case == "cancel_repeated":
            result, error = await _repeated_cancel(operation, ready_fd, deadline)
        else:
            result, error = await shared._own_operation(
                operation,
                cancel_wait=(lambda: shared._ready(ready_fd, deadline))
                if ready_fd is not None
                else None,
            )
    finally:
        try:
            if ready_fd is not None:
                os.close(ready_fd)
        finally:
            # No child was scheduled if setup failed before task creation.
            image._end_consumer(operation is None or observation.cleanup_confirmed)
    if _source_identity() != source:
        raise QualificationRefused("media_image_trusted_source_changed")
    value = None
    if result is not None:
        shared._save(proof / (label + "-stdout.log"), result.stdout)
        shared._save(proof / (label + "-stderr.log"), result.stderr)
        try:
            value = shared.m._json(result.stdout, 65536)
        except shared.m.PublisherError:
            pass
    if case == "setup_failure":
        missing = str(sentinel.parent / "absent-setup-source").encode()
        passed = (
            result is not None
            and result.exit_code == 1
            and error is None
            and result.stderr.startswith(b"bwrap:")
            and missing in result.stderr
            and b"No such file or directory" in result.stderr
        )
    elif case in ("output", "timeout", "cancel", "cancel_repeated", "cpu"):
        passed = (
            (case == "output" and error == "process_output_limit")
            or (case == "timeout" and error == "process_timeout")
            or (case == "cancel" and error == "operator_cancelled")
            or (case == "cancel_repeated" and error == "operator_repeated_cancelled")
            or (case == "cpu" and result is not None and result.exit_code in (-9, 137))
        )
    else:
        passed = (
            result is not None
            and result.exit_code == 0
            and _accepted(value, image.identity.manifest, case, host_namespace)
        )
    record = {
        "case": case,
        "label": label,
        "passed": bool(passed),
        "native_cleanup_confirmed": observation.cleanup_confirmed,
        "native_error": error,
        "cancel_requests": 2 if case == "cancel_repeated" else 1 if case == "cancel" else 0,
        "exit_code": result.exit_code if result else None,
        "trusted_source_sha256s": source,
        "result": value,
    }
    if not passed:
        shared._save(
            proof / (label + "-failed.json"), json.dumps(record, indent=2, sort_keys=True).encode()
        )
        raise QualificationRefused("media_image_native_control_failed")
    return record


def _proof_capacity(base: Path) -> None:
    with os.scandir(base) as entries:
        for count, entry in enumerate(entries, 1):
            if count >= 4:
                raise QualificationRefused("media_image_proof_count")
            info = entry.stat(follow_symlinks=False)
            if (
                not entry.name.startswith("image-proof-")
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise QualificationRefused("media_image_proof_entry")


def _operator_limits() -> None:
    for kind, bound in (
        (resource.RLIMIT_NOFILE, 256),
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_AS, 512 * 1024**2),
        (resource.RLIMIT_FSIZE, m.FILE_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if (soft != resource.RLIM_INFINITY and soft < bound) or (
            hard != resource.RLIM_INFINITY and hard < bound
        ):
            raise QualificationRefused("media_image_operator_limits")
        resource.setrlimit(kind, (bound, bound))


async def qualify(wheel: Path) -> Path:
    wheel = shared._private_path(wheel, directory=False)
    if (
        wheel.name != WHEEL_FILENAME
        or wheel.parent.parent != ROOT / "runtime/media-wheel-qualification"
        or not wheel.parent.name.startswith("wheel-proof-")
    ):
        raise QualificationRefused("media_image_wheel_path")
    raw = shared._read(wheel, m.ARCHIVE_BYTES, WHEEL_SHA256)
    base = ROOT / "runtime/media-image-proofs"
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    _proof_capacity(base)
    proof = Path(tempfile.mkdtemp(prefix="image-proof-", dir=base))
    shared._save(proof / "host-only-sentinel", b"public-image-confinement-sentinel")
    records = []
    matrix_deadline = time.monotonic() + 120
    with m.seal_pillow_image(raw) as image:
        identity = image.identity
        handles = image._begin_consumer()
        try:
            seals = _seal_controls(handles)
        finally:
            image._end_consumer(True)  # Only synchronous host probes; no child.
        for index, case in enumerate(("copy", *GUEST_CASES)):
            if time.monotonic() >= matrix_deadline:
                raise QualificationRefused("media_image_matrix_deadline")
            records.append(
                await _native(image, case, proof, f"{index:02d}-{case}", matrix_deadline)
            )
    if not image.cleanup_confirmed:
        raise QualificationRefused("media_image_cleanup_unknown")
    record = {
        "schema": "zelo-media-sealed-image-operator-proof-v1",
        "passed": True,
        "image_identity": dataclasses.asdict(identity),
        "image_digest": identity.digest,
        "image_cleanup_confirmed": image.cleanup_confirmed,
        "confinement_policy": confinement_policy(),
        "confinement_policy_digest": shared._digest(confinement_policy()),
        "seal_controls": seals,
        "cases": records,
        "package_imported_or_executed": False,
        "decoder_qualified": False,
        "publisher_attribution_inherited": False,
        "combined_current_preparation_complete": False,
        "runtime_authority": False,
        "instruction_trust": "none",
        "model_requests": 0,
    }
    result_path = proof / "result.json"
    shared._save(result_path, json.dumps(record, indent=2, sort_keys=True).encode() + b"\n")
    return result_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    try:
        _operator_limits()  # Only this explicit private operator process changes its limits.
        result = asyncio.run(qualify(args.wheel))
    except Exception as error:
        print(
            json.dumps({"qualified": False, "failure_type": type(error).__name__}), file=sys.stderr
        )
        raise SystemExit(1) from None
    print(json.dumps({"qualified": True, "proof": str(result)}))


if __name__ == "__main__":
    main()
