#!/usr/bin/env python3
"""Explicit fenced native-reader lease reconciliation; no qualification execution."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMISSION = ROOT / "runtime/media-elf-metadata-admission"
FAILED_ATTEMPT = ROOT / "runtime/media-native-reader/native-reader-proof-uvjet91z"
RECON_BASE = ROOT / "runtime/media-native-reader-reconciliation"
FAILED_LEASE = "9dd76284f54fe91361c4646fafc97a78c98f5179437345d54f9c815b8ea8bd0f"
FAILED_POLICY_DIGEST = "d111c5d7a7e6ef4f2cece7579f29367a37bbeaea2317f60a10e5676a2e81e999"
FAILED_POLICY_SHA256 = "a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3"
FAILED_PROOF_SHA256 = "027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378"
FAILED_MARKER_SHA256 = "f88573e617e66156815491696f8a51fed078e07df85658800cc1cb259bb34472"
FAILED_SOURCE_HEAD = "c6127669cda7923c851febe1b4f8530402f58ef8"
FAILED_SOURCE_TREE = "d6eaa8a1fa029cd049384f66d27de5218ba7c8cd"
DOCKER = "/usr/bin/docker"
LABEL = "org.zelo.media-reader.lease"
MAX_OUTPUT = 131072
MAX_ERROR = 16384
QUIET_SECONDS = 2


def _private(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("reader_reconcile_private")


def _read_fd(fd, bound):
    before = os.fstat(fd)
    _private(before)
    if not 0 <= before.st_size <= bound:
        raise ValueError("reader_reconcile_bound")
    os.lseek(fd, 0, os.SEEK_SET)
    raw = bytearray()
    while True:
        chunk = os.read(fd, min(65536, bound - len(raw) + 1))
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > bound:
            raise ValueError("reader_reconcile_bound")
    after = os.fstat(fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_uid,
        before.st_gid,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_uid,
        after.st_gid,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or len(raw) != before.st_size:
        raise ValueError("reader_reconcile_drift")
    return bytes(raw)


def _read_path(path, bound):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        return _read_fd(fd, bound)
    finally:
        os.close(fd)


def _command(args, config, timeout=6):
    if type(args) not in (tuple, list) or not args or args[0] != DOCKER:
        raise ValueError("reader_reconcile_command")
    env = {
        "DOCKER_CONFIG": str(config),
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    assert process.stdout is not None and process.stderr is not None
    buffers = {"out": bytearray(), "err": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ, "out")
            ready.register(process.stderr, selectors.EVENT_READ, "err")
            while ready.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("reader_reconcile_docker_timeout")
                for key, _ in ready.select(min(remaining, 0.5)):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        ready.unregister(key.fileobj)
                        continue
                    target = buffers[key.data]
                    bound = MAX_OUTPUT if key.data == "out" else MAX_ERROR
                    if len(target) + len(chunk) > bound:
                        raise ValueError("reader_reconcile_docker_output")
                    target.extend(chunk)
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise RuntimeError("reader_reconcile_docker_failed")
        return bytes(buffers["out"])
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def _discover(kind, config):
    if kind not in ("container", "image"):
        raise ValueError("reader_reconcile_kind")
    prefix = ("container", "ls", "--all") if kind == "container" else ("image", "ls")
    raw = _command(
        (
            DOCKER,
            "--host=unix:///var/run/docker.sock",
            *prefix,
            "--no-trunc",
            "--filter",
            f"label={LABEL}={FAILED_LEASE}",
            "--format",
            "{{.ID}}",
        ),
        config,
    )
    try:
        rows = raw.decode("ascii", "strict").splitlines()
    except UnicodeError:
        raise ValueError("reader_reconcile_discovery") from None
    pattern = r"[0-9a-f]{64}" if kind == "container" else r"sha256:[0-9a-f]{64}"
    if len(rows) > 1 or any(re.fullmatch(pattern, row) is None for row in rows):
        raise ValueError("reader_reconcile_discovery")
    return tuple(rows)


def _quiet_window(config):
    start = datetime.now(UTC).replace(microsecond=0)
    end = start + timedelta(seconds=QUIET_SECONDS)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    raw = _command(
        (
            DOCKER,
            "--host=unix:///var/run/docker.sock",
            "events",
            "--since",
            start.strftime(fmt),
            "--until",
            end.strftime(fmt),
            "--format",
            "{{json .}}",
        ),
        config,
        timeout=QUIET_SECONDS + 4,
    )
    if raw != b"":
        raise ValueError("reader_reconcile_daemon_busy")
    return start.strftime(fmt), end.strftime(fmt)


def _validate_failed_attempt():
    info = FAILED_ATTEMPT.lstat()
    _private(info, directory=True)
    entries = sorted(os.scandir(FAILED_ATTEMPT), key=lambda item: item.name)
    if [entry.name for entry in entries] != ["cases.json", "docker-config", "policy.json"]:
        raise ValueError("reader_reconcile_attempt_inventory")
    config_entry = entries[1]
    config_info = config_entry.stat(follow_symlinks=False)
    _private(config_info, directory=True)
    if list(os.scandir(config_entry.path)):
        raise ValueError("reader_reconcile_config_nonempty")
    policy_raw = _read_path(FAILED_ATTEMPT / "policy.json", 2097152)
    proof_raw = _read_path(FAILED_ATTEMPT / "cases.json", 2097152)
    if hashlib.sha256(policy_raw).hexdigest() != FAILED_POLICY_SHA256:
        raise ValueError("reader_reconcile_policy_pin")
    if hashlib.sha256(proof_raw).hexdigest() != FAILED_PROOF_SHA256:
        raise ValueError("reader_reconcile_proof_pin")
    policy = json.loads(policy_raw)
    proof = json.loads(proof_raw)
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest() != FAILED_POLICY_DIGEST:
        raise ValueError("reader_reconcile_policy_digest")
    if proof != {
        "runtime_authority": False,
        "stage": "lease-preflight",
        "status": "refused",
        "support_release_confirmed": False,
    }:
        raise ValueError("reader_reconcile_refusal")
    return policy_raw, proof_raw


def _new_evidence_directory():
    RECON_BASE.mkdir(mode=0o700, exist_ok=True)
    _private(RECON_BASE.lstat(), directory=True)
    with os.scandir(RECON_BASE) as existing:
        if any(True for _ in existing):
            raise ValueError("reader_reconcile_attempt_exists")
    path = Path(tempfile.mkdtemp(prefix="reconcile-", dir=RECON_BASE))
    os.chmod(path, 0o700)
    return path


def _write_pending(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(raw) > 65536:
        raise ValueError("reader_reconcile_evidence_bound")
    target = path / "pending.json"
    fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise ValueError("reader_reconcile_evidence_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private(os.fstat(directory), directory=True)
        os.fsync(directory)
    finally:
        os.close(directory)
    return raw, target


def reconcile(*, docker_discover=_discover, quiet_window=_quiet_window):
    directory = os.open(ADMISSION, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    lock = marker = None
    evidence_path = None
    try:
        _private(os.fstat(directory), directory=True)
        lock = os.open(
            "admission.lock",
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory,
        )
        _private(os.fstat(lock))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("reader_reconcile_live_lock") from exc
        marker = os.open(
            "active.marker",
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory,
        )
        marker_raw = _read_fd(marker, 4096)
        if hashlib.sha256(marker_raw).hexdigest() != FAILED_MARKER_SHA256:
            raise ValueError("reader_reconcile_marker_pin")
        marker_value = json.loads(marker_raw)
        if marker_value != {
            "version": 1,
            "lease": FAILED_LEASE,
            "policy": FAILED_POLICY_DIGEST,
        }:
            raise ValueError("reader_reconcile_marker_identity")

        policy_raw, proof_raw = _validate_failed_attempt()
        retained_config = FAILED_ATTEMPT / "docker-config"
        config_info = retained_config.lstat()
        _private(config_info, directory=True)
        if list(os.scandir(retained_config)):
            raise ValueError("reader_reconcile_config_nonempty")

        runtime = ROOT / "runtime"
        runtime_info = runtime.lstat()
        if (
            not stat.S_ISDIR(runtime_info.st_mode)
            or runtime_info.st_uid != os.geteuid()
            or runtime_info.st_gid != os.getegid()
            or runtime_info.st_mode & 0o022
        ):
            raise ValueError("reader_reconcile_runtime")
        with tempfile.TemporaryDirectory(
            prefix="reader-reconcile-docker-", dir=runtime
        ) as raw_config:
            config = Path(raw_config)
            os.chmod(config, 0o700)
            if docker_discover("container", config) or docker_discover("image", config):
                raise ValueError("reader_reconcile_resources_present")
            quiet_start, quiet_end = quiet_window(config)
            if docker_discover("container", config) or docker_discover("image", config):
                raise ValueError("reader_reconcile_resources_present")
            if list(os.scandir(config)):
                raise ValueError("reader_reconcile_fresh_config_dirty")
        policy_after, proof_after = _validate_failed_attempt()
        if policy_after != policy_raw or proof_after != proof_raw:
            raise ValueError("reader_reconcile_proof_drift")

        os.lseek(marker, 0, os.SEEK_SET)
        if _read_fd(marker, 4096) != marker_raw:
            raise ValueError("reader_reconcile_marker_drift")
        evidence_dir = _new_evidence_directory()
        pending = {
            "schema": "zelo-media-native-reader-reconciliation-v1",
            "status": "pending-marker-clear",
            "failed_source_head": FAILED_SOURCE_HEAD,
            "failed_source_tree": FAILED_SOURCE_TREE,
            "failed_attempt": FAILED_ATTEMPT.name,
            "failed_lease": FAILED_LEASE,
            "policy_sha256": hashlib.sha256(policy_raw).hexdigest(),
            "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
            "marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
            "docker_container_count_before": 0,
            "docker_image_count_before": 0,
            "docker_container_count_after": 0,
            "docker_image_count_after": 0,
            "quiet_start": quiet_start,
            "quiet_end": quiet_end,
            "quiet_seconds": QUIET_SECONDS,
            "attempts_used": 1,
            "attempt_limit": 2,
            "proof_rewritten": False,
            "proof_deleted": False,
            "attempt_reset": False,
            "runtime_authority": False,
        }
        pending_raw, evidence_path = _write_pending(evidence_dir, pending)
        os.fsync(directory)
        os.ftruncate(marker, 0)
        os.fsync(marker)
        os.fsync(directory)
    finally:
        if marker is not None:
            os.close(marker)
        if lock is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
        os.close(directory)
    if evidence_path is None:
        raise ValueError("reader_reconcile_no_evidence")
    return {
        "status": "reconciled-native-reader-admission",
        "evidence": str(evidence_path),
        "evidence_sha256": hashlib.sha256(pending_raw).hexdigest(),
        "failed_attempt": FAILED_ATTEMPT.name,
        "failed_lease": FAILED_LEASE,
        "attempts_used": 1,
        "attempt_limit": 2,
        "admission_marker_cleared": True,
        "runtime_authority": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        result = reconcile()
    except Exception:
        print("reader_reconcile_refused", file=os.sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
