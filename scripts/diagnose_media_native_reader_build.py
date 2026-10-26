#!/usr/bin/env python3
"""Run one bounded, network-disabled Media-reader build diagnostic and retain a safe receipt.

This wrapper does not allocate a qualification attempt or call the proof ledger.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification import media_reader_setup as setup  # noqa: E402
from scripts import qualify_media_native_reader as reader  # noqa: E402

SCHEMA = "zelo-native-reader-build-diagnostic-v1"
BUILD_TIMEOUT_SECONDS = 90
MAX_RECEIPT_BYTES = 8192
RECEIPT_DIRECTORY = "media-native-reader-diagnostics"


def _directory(path: Path, *, create: bool, private: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("diagnostic_path")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or (private and stat.S_IMODE(info.st_mode) != 0o700)
        or (not private and info.st_mode & 0o022)
    ):
        raise ValueError("diagnostic_directory")
    return path


def _write_receipt(directory: Path, receipt: dict) -> tuple[Path, dict]:
    directory = _directory(directory, create=True, private=True)
    raw = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("diagnostic_receipt_bound")
    # Keep an unverified write out of the published JSON namespace.
    path = directory / (".pending-" + receipt["diagnostic_id"])
    published = directory / (receipt["diagnostic_id"] + ".json")
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("diagnostic_receipt_write")
            view = view[written:]
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    read_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(read_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != len(raw)
        ):
            raise ValueError("diagnostic_receipt_identity")
        readback = bytearray()
        while len(readback) <= MAX_RECEIPT_BYTES:
            chunk = os.read(read_fd, min(4096, MAX_RECEIPT_BYTES + 1 - len(readback)))
            if not chunk:
                break
            readback.extend(chunk)
    finally:
        os.close(read_fd)
    if bytes(readback) != raw:
        raise ValueError("diagnostic_receipt_readback")
    decoded = json.loads(readback)
    if type(decoded) is not dict or decoded.get("diagnostic_id") != receipt["diagnostic_id"]:
        raise ValueError("diagnostic_receipt_readback")
    if published.exists() or published.is_symlink():
        raise ValueError("diagnostic_receipt_collision")
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.rename(path, published)
        try:
            os.fsync(dir_fd)
        except BaseException:
            # A failed publication sync must not leave a visible success receipt.
            # The caller keeps admission fenced even if this best-effort removal fails.
            try:
                os.unlink(published)
                os.fsync(dir_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)
    return published, decoded


def run_build_diagnostic(runtime_root: Path, receipt_dir: Path | None = None) -> tuple[Path, dict]:
    """Run one build, clean its context/resources, then persist and read back its receipt."""
    runtime_root = _directory(Path(runtime_root).resolve(), create=False)
    receipt_dir = (
        Path(receipt_dir).resolve() if receipt_dir is not None else runtime_root / RECEIPT_DIRECTORY
    )
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _directory(receipt_dir, create=False, private=True)
    diagnostic_id = secrets.token_hex(32)
    lease_identity = hashlib.sha256(
        ("zelo-native-reader-diagnostic:" + diagnostic_id).encode()
    ).hexdigest()
    environment = None
    build_plan = None
    bundle_sha256 = None
    dockerfile_sha256 = None
    build_invocations = 0
    build_failure = None
    cleanup_confirmed = False
    cleanup_errors = []
    started = time.monotonic()
    temp_context = None
    try:
        temp_context = Path(tempfile.mkdtemp(prefix="native-reader-diagnostic-", dir=runtime_root))
        os.chmod(temp_context, 0o700)
        if temp_context == receipt_dir or temp_context in receipt_dir.parents:
            raise ValueError("diagnostic_receipt_inside_context")
        docker_config = temp_context / "docker-config"
        docker_config.mkdir(mode=0o700)
        environment = reader._docker_environment(docker_config)
        try:
            build_plan, manifest = reader._copy_context(temp_context, lease_identity, runtime_root)
            bundle_sha256 = manifest.digest
            dockerfile_sha256 = hashlib.sha256(build_plan.dockerfile.read_bytes()).hexdigest()
            build_invocations = 1
            reader._docker(setup.build_arguments(build_plan), environment, BUILD_TIMEOUT_SECONDS)
        except Exception as error:
            build_failure = reader._build_failure_evidence(error)
    except Exception as error:
        build_failure = reader._build_failure_evidence(error)
    finally:
        if environment is not None:
            try:
                cleanup_confirmed = reader._cleanup_owned(lease_identity, environment) is True
            except Exception:
                cleanup_confirmed = False
            if not cleanup_confirmed:
                cleanup_errors.append("owned_resource_cleanup_unconfirmed")
        else:
            # No Docker command was configured, so no labeled resource was created.
            cleanup_confirmed = build_invocations == 0
        if temp_context is not None:
            try:
                shutil.rmtree(temp_context)
            except OSError:
                cleanup_errors.append("temporary_context_cleanup_failed")

    temporary_context_removed = temp_context is None or not temp_context.exists()
    receipt = {
        "schema": SCHEMA,
        "diagnostic_id": diagnostic_id,
        "lease_identity": lease_identity,
        "build_timeout_seconds": BUILD_TIMEOUT_SECONDS,
        "build_invocations": build_invocations,
        "build_status": "failed" if build_failure is not None else "succeeded",
        "build_failure": build_failure,
        "bundle_sha256": bundle_sha256,
        "dockerfile_sha256": dockerfile_sha256,
        "network": "none",
        "pull": False,
        "proof_entrypoint_called": False,
        "proof_ledger_touched": False,
        "cleanup_confirmed": cleanup_confirmed,
        "cleanup_errors": cleanup_errors,
        "temporary_context_removed": temporary_context_removed,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    if not temporary_context_removed:
        receipt["cleanup_confirmed"] = False
    return _write_receipt(receipt_dir, receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path)
    args = parser.parse_args()
    try:
        reader._operator_limits()
        path, receipt = run_build_diagnostic(args.runtime_root, args.receipt_dir)
    except Exception:
        print("reader_native_diagnostic_refused", file=sys.stderr)
        return 1
    print(
        json.dumps({"receipt": str(path), "build_status": receipt["build_status"]}, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
