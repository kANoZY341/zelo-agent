#!/usr/bin/env python3
"""Explicit closed-wheel operator proof; no app assembly or runtime activation."""

import argparse
import asyncio
import base64
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

if not __debug__:
    raise SystemExit("media_publisher_optimized_python_refused")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import (  # noqa: E402
    DeveloperError,
    NativeExecutionObservation,
    run_process,
    uncancellable,
)
from app.developer_sandbox import python_compute_sandbox, python_compute_sandbox_plan  # noqa: E402
from qualification import media_publisher as m  # noqa: E402
from qualification.artifacts import capture_manifest  # noqa: E402
from qualification.media_publisher_guest import GUEST_BOOTSTRAP, GUEST_CASES  # noqa: E402


class QualificationRefused(ValueError):
    pass


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def confinement_policy() -> dict:
    return {
        "schema": "zelo-media-publisher-confinement-v1",
        "base_namespace": python_compute_sandbox_plan(),
        "fixed_read_only_mounts": (
            "/opt/zelo-publisher-tools",
            "/opt/zelo-publisher-source/qualification",
            m.ROOT_FILE,
        ),
        "capabilities": "all-dropped",
        "cancel_readiness_channel": "one-byte-private-fifo-only-in-cancel-control",
        "guest_bootstrap_sha256": hashlib.sha256(GUEST_BOOTSTRAP.encode()).hexdigest(),
        "interpreter_flags": ("-I", "-S", "-B"),
        "environment": {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": "/tmp",
        },
        "cpu_seconds": 10,
        "address_space_bytes": 512 * 1024**2,
        "open_descriptors": 64,
        "file_core_bytes": 0,
        "nproc_supplementary": 0,
        "stdout_bytes": 65536,
        "stderr_bytes": 16384,
        "native_absolute_seconds": 30,
        "filesystem_preflight": "bounded-local-filesystem-no-hard-io-deadline",
        "owned_process": "pidfd-reserved-identity-until-group-cleanup-and-final-reap",
        "instruction_trust": "none",
        "runtime_authority": False,
    }


def _private_path(path: Path, *, directory: bool) -> Path:
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise QualificationRefused("media_publisher_path")
    runtime = ROOT / "runtime"
    try:
        relative = path.relative_to(runtime)
    except ValueError:
        raise QualificationRefused("media_publisher_path") from None
    initial_runtime = runtime.lstat()
    if (
        not stat.S_ISDIR(initial_runtime.st_mode)
        or initial_runtime.st_uid != os.getuid()
        or initial_runtime.st_mode & 0o022
    ):
        raise QualificationRefused("media_publisher_runtime")
    current = runtime
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise QualificationRefused("media_publisher_path")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise QualificationRefused("media_publisher_path")
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise QualificationRefused("media_publisher_path")
    return path


def _read(path: Path, limit: int, expected_sha: str | None = None) -> bytes:
    _private_path(path, directory=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        initial = os.fstat(fd)
        if initial.st_nlink != 1 or not 1 <= initial.st_size <= limit:
            raise QualificationRefused("media_publisher_file")
        result = bytearray()
        while block := os.read(fd, min(65536, initial.st_size - len(result) + 1)):
            result.extend(block)
            if len(result) > initial.st_size:
                raise QualificationRefused("media_publisher_file")
        after = os.fstat(fd)
        if len(result) != initial.st_size or (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
            initial.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise QualificationRefused("media_publisher_file_changed")
    finally:
        os.close(fd)
    value = bytes(result)
    if expected_sha is not None and hashlib.sha256(value).hexdigest() != expected_sha:
        raise QualificationRefused("media_publisher_file_pin")
    return value


def _save(path: Path, value: bytes) -> None:
    if len(value) > 2 * 1024**2:
        raise QualificationRefused("media_publisher_proof_size")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        block = memoryview(value)
        while block:
            count = os.write(fd, block)
            if count <= 0:
                raise QualificationRefused("media_publisher_proof_write")
            block = block[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _proof_capacity(base: Path) -> None:
    with os.scandir(base) as entries:
        for count, entry in enumerate(entries, 1):
            if count >= 32:
                raise QualificationRefused("media_publisher_proof_count")
            info = entry.stat(follow_symlinks=False)
            if (
                not entry.name.startswith("publisher-proof-")
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise QualificationRefused("media_publisher_proof_entry")


def _mutate(raw: bytes, case: str) -> bytes:
    if case not in {
        "signature",
        "certificate",
        "promise",
        "proof_hash",
        "checkpoint",
        "integrated_time",
        "subject_hash",
        "subject_name",
        "extra_attestation",
    }:
        return raw
    value = m._json(raw, m.PROVENANCE_BYTES)
    bundle = value["attestation_bundles"][0]
    attestation = bundle["attestations"][0]
    log = attestation["verification_material"]["transparency_entries"][0]
    if case == "extra_attestation":
        bundle["attestations"].append(attestation)
    elif case in ("signature", "certificate", "promise", "proof_hash"):
        if case == "signature":
            target, key = attestation["envelope"], "signature"
        elif case == "certificate":
            target, key = attestation["verification_material"], "certificate"
        elif case == "promise":
            target, key = log["inclusionPromise"], "signedEntryTimestamp"
        else:
            target, key = log["inclusionProof"]["hashes"], 0
        body = bytearray(base64.b64decode(target[key], validate=True))
        body[-1] ^= 1
        target[key] = base64.b64encode(body).decode()
    elif case == "checkpoint":
        log["inclusionProof"]["checkpoint"]["envelope"] += "x"
    elif case == "integrated_time":
        log["integratedTime"] = "1"
    else:
        statement = json.loads(
            base64.b64decode(attestation["envelope"]["statement"], validate=True)
        )
        if case == "subject_hash":
            statement["subject"][0]["digest"]["sha256"] = "0" * 64
        else:
            statement["subject"][0]["name"] = "invalid.whl"
        attestation["envelope"]["statement"] = base64.b64encode(
            json.dumps(statement, separators=(",", ":")).encode()
        ).decode()
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _argv(tool_fd: int, root_fd: int, case: str, sentinel: Path, ready_fd: int = -1) -> list[str]:
    command = [
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-B",
        "-c",
        GUEST_BOOTSTRAP,
        case,
        str(sentinel),
        os.readlink("/proc/self/ns/net"),
        str(ready_fd),
    ]
    argv = python_compute_sandbox(command)
    index = argv.index("--remount-ro")
    ready_mount = ["--bind-fd", str(ready_fd), "/opt/zelo-publisher-ready"] if ready_fd >= 0 else []
    argv[index:index] = [
        *ready_mount,
        "--cap-drop",
        "ALL",
        "--dir",
        "/opt/zelo-publisher-source",
        "--ro-bind",
        str(ROOT / "qualification"),
        "/opt/zelo-publisher-source/qualification",
        "--ro-bind",
        f"/proc/self/fd/{tool_fd}",
        m.TOOL_ROOT,
        "--ro-bind",
        f"/proc/self/fd/{root_fd}",
        m.ROOT_FILE,
    ]
    return argv


def _accepted(value: dict, raw: bytes) -> bool:
    if (
        type(value) is not dict
        or set(value) != {"status", "attribution", "digest"}
        or value["status"] != "attributed"
    ):
        return False
    _, statement = m._envelope(raw)
    expected = {
        "provenance_sha256": hashlib.sha256(raw).hexdigest(),
        "verified_statement_sha256": hashlib.sha256(statement).hexdigest(),
    }
    for name in m.PublisherAttribution.__dataclass_fields__:
        if name not in expected:
            expected[name] = getattr(m.PublisherAttribution, name)
    return value["attribution"] == expected and value["digest"] == _digest(expected)


async def _ready(fd: int, deadline: float) -> None:
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

    def readable():
        if ready.done():
            return
        try:
            data = os.read(fd, 1)
            if data != b"R":
                raise QualificationRefused("media_publisher_guest_readiness")
        except Exception as error:
            ready.set_exception(error)
        else:
            ready.set_result(None)

    loop.add_reader(fd, readable)
    try:
        await asyncio.wait_for(ready, max(0.001, deadline - time.monotonic()))
    finally:
        loop.remove_reader(fd)


async def _own_operation(operation: asyncio.Task, *, cancel_wait=None):
    try:
        if cancel_wait is not None:
            await cancel_wait()
            operation.cancel()
        try:
            return await operation, None
        except asyncio.CancelledError:
            if cancel_wait is None or asyncio.current_task().cancelling():
                raise
            return None, "operator_cancelled"
        except DeveloperError as error:
            return None, str(error)
    finally:
        if not operation.done():
            operation.cancel()

        async def settle():
            try:
                await operation
            except asyncio.CancelledError:
                pass
            except DeveloperError:
                pass

        _, cancelled = await uncancellable(asyncio.create_task(settle()))
        if cancelled:
            raise asyncio.CancelledError


async def _native(tool_fd, root_fd, case, sentinel, raw, deadline):
    observation = NativeExecutionObservation()
    ready_fds = ()
    if case == "cancel":
        ready_path = sentinel.parent / "cancel-ready.fifo"
        os.mkfifo(ready_path, mode=0o600)
        ready_fds = (os.open(ready_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC),)
    try:
        operation = asyncio.create_task(
            run_process(
                _argv(tool_fd, root_fd, case, sentinel, ready_fds[0] if ready_fds else -1),
                cwd=ROOT,
                timeout=max(0.001, deadline - time.monotonic()),
                deadline=deadline,
                stdout_limit=65536,
                stderr_limit=16384,
                stdin=raw,
                env=confinement_policy()["environment"],
                pass_fds=(tool_fd, root_fd, *ready_fds),
                observation=observation,
            )
        )
        result, error = await _own_operation(
            operation,
            cancel_wait=(lambda: _ready(ready_fds[0], deadline)) if ready_fds else None,
        )
    finally:
        if ready_fds:
            os.close(ready_fds[0])
    if not observation.cleanup_confirmed:
        raise QualificationRefused("media_publisher_cleanup_unknown")
    return result, error, observation


def _negative_accepted(case: str, value) -> bool:
    expected = (
        "media_publisher_root"
        if case == "wrong_root"
        else "media_publisher_count"
        if case == "extra_attestation"
        else "media_publisher_crypto_refused"
    )
    return value == {"status": "refused", "code": expected}


def _network_accepted(value, host_namespace: str) -> bool:
    return (
        type(value) is dict
        and set(value)
        == {"status", "probe", "enforced", "namespace", "interfaces", "route_rows", "connect_errno"}
        and value["status"] == "bounded_probe"
        and value["probe"] == "network"
        and value["enforced"] is True
        and type(value["namespace"]) is str
        and value["namespace"].startswith("net:[")
        and value["namespace"] != host_namespace
        and value["interfaces"] == ["lo"]
        and type(value["route_rows"]) is int
        and value["route_rows"] == 0
        and type(value["connect_errno"]) is int
        and value["connect_errno"] == errno.ENETUNREACH
    )


async def qualify(acquisition: Path, case: str) -> Path:
    if case not in GUEST_CASES:
        raise QualificationRefused("media_publisher_case")
    deadline = time.monotonic() + 30
    acquisition = _private_path(acquisition, directory=True)
    if (
        acquisition.parent != ROOT / "runtime/media-publisher-qualification"
        or not acquisition.name.startswith("acquisition-")
    ):
        raise QualificationRefused("media_publisher_acquisition")
    source = _private_path(acquisition / "tool-venv/lib/python3.12/site-packages", directory=True)
    root_path = acquisition / "sigstore-production-root-snapshot.json"
    root_bytes = _read(root_path, m.ROOT_BYTES, m.ROOT_SHA256)
    original_raw = _read(acquisition / "pillow-provenance-unverified.json", m.PROVENANCE_BYTES)
    raw = _mutate(original_raw, case)
    manifest = await asyncio.to_thread(capture_manifest, source, m.TOOL_ROLES, m.TOOL_SCAN_POLICY)
    if manifest.digest != m.TOOL_MANIFEST_DIGEST or time.monotonic() >= deadline:
        raise QualificationRefused("media_publisher_tool_binding")
    base = ROOT / "runtime/media-publisher-proofs"
    base.mkdir(mode=0o700, exist_ok=True)
    _private_path(base, directory=True)
    _proof_capacity(base)
    proof = Path(tempfile.mkdtemp(prefix="publisher-proof-", dir=base))
    sentinel = proof / "host-only-sentinel"
    _save(sentinel, b"public-confinement-sentinel")
    _save(proof / "input-provenance.json", raw)
    baseline = None
    tool_fd = os.open(
        source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    )
    try:
        root_fd = os.open(root_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if case != "positive":
                baseline_result, baseline_error, _ = await _native(
                    tool_fd, root_fd, "positive", sentinel, original_raw, deadline
                )
                if (
                    baseline_result is None
                    or baseline_error is not None
                    or baseline_result.exit_code != 0
                ):
                    raise QualificationRefused("media_publisher_baseline")
                baseline = m._json(baseline_result.stdout, 65536)
                if not _accepted(baseline, original_raw):
                    raise QualificationRefused("media_publisher_baseline")
                _save(proof / "baseline-stdout.log", baseline_result.stdout)
                _save(proof / "baseline-stderr.log", baseline_result.stderr)
            if case == "wrong_root":
                bad = bytearray(root_bytes)
                bad[-1] ^= 1
                bad_path = proof / "wrong-root.json"
                _save(bad_path, bytes(bad))
                bad_fd = os.open(
                    bad_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                )
                try:
                    result, native_error, observation = await _native(
                        tool_fd, bad_fd, case, sentinel, raw, deadline
                    )
                finally:
                    os.close(bad_fd)
            else:
                result, native_error, observation = await _native(
                    tool_fd, root_fd, case, sentinel, raw, deadline
                )
        finally:
            os.close(root_fd)
    finally:
        os.close(tool_fd)
    after = await asyncio.to_thread(capture_manifest, source, m.TOOL_ROLES, m.TOOL_SCAN_POLICY)
    if after != manifest:
        raise QualificationRefused("media_publisher_tool_changed")
    value = None
    if result is not None:
        _save(proof / "stdout.log", result.stdout)
        _save(proof / "stderr.log", result.stderr)
        try:
            value = m._json(result.stdout, 65536)
        except m.PublisherError:
            pass
    if case == "positive":
        passed = result is not None and result.exit_code == 0 and _accepted(value, raw)
    elif case in ("cpu", "cancel", "output"):
        passed = (
            (case == "cancel" and native_error == "operator_cancelled")
            or (case == "output" and native_error == "process_output_limit")
            or (case == "cpu" and result is not None and result.exit_code in (-9, 137))
        )
    elif case == "network":
        passed = (
            result is not None
            and result.exit_code == 0
            and _network_accepted(value, os.readlink("/proc/self/ns/net"))
        )
    elif case in ("write", "host_file", "imports", "memory"):
        passed = (
            result is not None
            and result.exit_code == 0
            and value == {"status": "bounded_probe", "probe": case, "enforced": True}
        )
    else:
        passed = result is not None and result.exit_code == 10 and _negative_accepted(case, value)
    evidence = {
        "schema": "zelo-media-publisher-operator-proof-v1",
        "case": case,
        "baseline": baseline,
        "baseline_input_sha256": hashlib.sha256(original_raw).hexdigest(),
        "passed": bool(passed),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "tool_manifest_digest": manifest.digest,
        "root_sha256": m.ROOT_SHA256,
        "publisher_policy_digest": m.PUBLISHER_POLICY_DIGEST,
        "confinement_policy_digest": _digest(confinement_policy()),
        "native_cleanup_confirmed": observation.cleanup_confirmed,
        "native_error": native_error,
        "exit_code": result.exit_code if result else None,
        "result": value,
        "cryptographic_attribution_under_pinned_root": case == "positive" and bool(passed),
        "current_tuf_freshness": False,
        "native_media_runtime_qualified": False,
        "combined_current_preparation_complete": False,
        "pillow_imported": False,
        "model_requests": 0,
        "instruction_trust": "none",
    }
    _save(proof / "result.json", json.dumps(evidence, indent=2, sort_keys=True).encode() + b"\n")
    if not passed:
        raise QualificationRefused("media_publisher_qualification_failed")
    return proof / "result.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--case", choices=GUEST_CASES, default="positive")
    args = parser.parse_args()
    try:
        path = asyncio.run(qualify(args.acquisition, args.case))
    except Exception as error:
        print(
            json.dumps({"qualified": False, "failure_type": type(error).__name__}), file=sys.stderr
        )
        raise SystemExit(1) from None
    print(json.dumps({"proof": str(path), "case": args.case, "qualified": True}))


if __name__ == "__main__":
    main()
