#!/usr/bin/env python3
"""Explicit fixed synthetic USTAR isolation proof; no acquired-package execution."""

import argparse
import asyncio
import hashlib
import json
import os
import resource
import stat
import sys
import tempfile
import time
from pathlib import Path


def _operator_limits():
    for kind, bound in (
        (resource.RLIMIT_AS, 536870912),
        (resource.RLIMIT_CPU, 60),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_FSIZE, 2097152),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if any(value != resource.RLIM_INFINITY and value < bound for value in (soft, hard)):
            raise ValueError("ustar_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("ustar_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import NativeExecutionObservation, run_process  # noqa: E402
from app.developer_sandbox import python_compute_sandbox, python_compute_sandbox_plan  # noqa: E402
from qualification import media_tar_isolation as m  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_elf_version_metadata import (  # noqa: E402
    _helper,
    _open,
    _read_fd,
    _release,
    _State,
    _Store,
)
from scripts.qualify_media_image import _repeated_cancel  # noqa: E402

ADMISSION = "runtime/media-elf-metadata-admission"
PROOF_BASE = "runtime/media-ustar-isolation"
PREFIX = "ustar-proof-"
WHOLE_SECONDS = 120
MAX_ATTEMPTS = 2
_RETAINED = []


def _deadline(deadline):
    if time.monotonic() >= deadline:
        raise ValueError("ustar_operator_deadline")


def policy():
    return {
        "schema": "zelo-ustar-synthetic-isolation-policy-v1",
        "sandbox": python_compute_sandbox_plan(),
        "capabilities": "all-dropped",
        "source_files": m.SOURCE_FILES,
        "guest_sha256": hashlib.sha256(m.GUEST_BOOTSTRAP.encode()).hexdigest(),
        "cases": [
            {
                "case": case,
                "fixture_bytes": len(m.fixture(case)),
                "fixture_sha256": hashlib.sha256(m.fixture(case)).hexdigest(),
            }
            for case in m.MATRIX
        ],
        "guest_limits": m.LIMITS,
        "environment": m.ENV,
        "stdin_bytes": m.FIXTURE_MAX,
        "stdout_bytes": 65536,
        "stderr_bytes": 16384,
        "source_file_bytes": 262144,
        "source_hash_read_bytes": 524288,
        "source_copy_bytes": len(m.MATRIX) * sum(row[2] for row in m.SOURCE_FILES),
        "case_seconds": 10,
        "timeout_case_seconds": 1,
        "whole_seconds": WHOLE_SECONDS,
        "attempt_limit": MAX_ATTEMPTS,
        "proof_scope": PROOF_BASE + "/" + PREFIX + "*",
        "retained_entries": 8,
        "retained_bytes": 5242880,
        "json_file_bytes": 2097152,
        "ready_fifos": 2,
        "parent_buffer_reservation": 33554432,
        "host_assumption": "trusted-interpreter-other-stdlib-loader-libraries-bwrap-and-ancestors",
        "filesystem_deadline": "not-hard",
        "nproc_limit": "supplementary-not-aggregate-cgroup-proof",
        "aggregate_cgroup_verified": False,
        "package_provenance_verified": False,
        "full_runtime_closure_verified": False,
        "runtime_authority": False,
        "admission_close_failure": "no-receipt-marker-may-already-be-empty",
    }


class _Evidence:
    """Three fixed JSON outputs; separate from unchanged persistence helper."""

    def __init__(self, path, state):
        self.store = _Store(path, state)
        self.names = set()
        self.reserved = 0

    def json(self, name, value):
        limits = {"policy.json": 524288, "sources.json": 524288, "cases.json": 2097152}
        if name not in limits or name in self.names:
            raise ValueError("ustar_evidence_name")
        self.names.add(name)
        self.reserved += limits[name]
        if self.reserved > 5242880:
            raise ValueError("ustar_evidence_budget")
        raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        if len(raw) > limits[name]:
            raise ValueError("ustar_evidence_budget")
        self.store.save(name, raw)
        return raw


def _attempt_directory():
    base = ROOT / PROOF_BASE
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    if stat.S_IMODE(base.lstat().st_mode) != 0o700:
        raise ValueError("ustar_proof_private")
    names = {
        "policy.json",
        "sources.json",
        "cases.json",
        "cancel-ready.fifo",
        "cancel_repeated-ready.fifo",
    }
    with os.scandir(base) as attempts:
        for number, entry in enumerate(attempts, 1):
            if number >= MAX_ATTEMPTS:
                raise ValueError("ustar_attempt_limit")
            path = base / entry.name
            shared._private_path(path, directory=True)
            if stat.S_IMODE(path.lstat().st_mode) != 0o700:
                raise ValueError("ustar_proof_private")
            if not entry.name.startswith(PREFIX):
                raise ValueError("ustar_old_attempt")
            retained = 0
            with os.scandir(path) as files:
                for count, item in enumerate(files, 1):
                    info = item.stat(follow_symlinks=False)
                    fifo = item.name.endswith("-ready.fifo")
                    if (
                        count > 8
                        or item.name not in names
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_nlink != 1
                        or not (stat.S_ISFIFO(info.st_mode) if fifo else stat.S_ISREG(info.st_mode))
                        or not 0 <= info.st_size <= (0 if fifo else 2097152)
                    ):
                        raise ValueError("ustar_old_entry")
                    retained += info.st_size
                    if retained > 5242880:
                        raise ValueError("ustar_retained_bound")
    return Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))


def _sources(state):
    fds = []
    records = []
    for relative, destination, size, digest in m.SOURCE_FILES:
        path = ROOT / relative
        if path.resolve(strict=True) != path:
            raise ValueError("ustar_source_path")
        fd = _helper(state, _open, state, path)
        if os.fstat(fd).st_uid != os.geteuid():
            raise ValueError("ustar_source_owner")
        raw = _helper(state, _read_fd, fd, 262144, digest, size)
        records.append(
            {"source": relative, "destination": destination, "bytes": len(raw), "sha256": digest}
        )
        fds.append(fd)
    return tuple(fds), records


def _source_readback(state, fds):
    if len(fds) != len(m.SOURCE_FILES):
        raise ValueError("ustar_source_count")
    for fd, (_, _, size, digest) in zip(fds, m.SOURCE_FILES, strict=True):
        if os.fstat(fd).st_uid != os.geteuid():
            raise ValueError("ustar_source_owner")
        _helper(state, _read_fd, fd, 262144, digest, size)


def _argv(fds, case, host_namespace, run_id, digest, ready_fd=None):
    if case not in m.MATRIX or len(fds) != 3:
        raise ValueError("ustar_launch_identity")
    if (ready_fd is not None) != (case in ("cancel", "cancel_repeated")):
        raise ValueError("ustar_ready_identity")
    binding = m.expected_binding(case, run_id, digest)
    command = [
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-B",
        "-c",
        m.GUEST_BOOTSTRAP,
        case,
        host_namespace,
        m.canonical(binding).decode(),
    ]
    argv = python_compute_sandbox(command)
    additions = [
        "--cap-drop",
        "ALL",
        "--tmpfs",
        "/opt",
        "--dir",
        "/opt/zelo-ustar",
        "--dir",
        "/opt/zelo-ustar/qualification",
    ]
    for fd, (_, destination, _, _) in zip(fds, m.SOURCE_FILES, strict=True):
        additions.extend(("--perms", "0400", "--file", str(fd), destination))
    if ready_fd is not None:
        additions.extend(("--bind-fd", str(ready_fd), m.READY_PATH))
    additions.extend(("--remount-ro", "/opt"))
    index = argv.index("--remount-ro")
    argv[index:index] = additions
    return argv


async def _case(state, fds, case, attempt, run_id, digest, matrix_deadline):
    _deadline(matrix_deadline)
    ready_fd = None
    if case in ("cancel", "cancel_repeated"):
        path = attempt / (case + "-ready.fifo")
        os.mkfifo(path, mode=0o600)
        ready_fd = state.hold(
            os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        )
        info = os.fstat(ready_fd)
        if not (
            stat.S_ISFIFO(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
        ):
            raise ValueError("ustar_ready_file")
    for fd in fds:
        os.lseek(fd, 0, os.SEEK_SET)
    host_namespace = os.readlink("/proc/self/ns/net")
    argv = _argv(fds, case, host_namespace, run_id, digest, ready_fd)
    deadline = min(matrix_deadline, time.monotonic() + (1 if case == "timeout" else 10))
    observation = NativeExecutionObservation()
    state.observations.append(observation)  # Owned before any launch.
    payload = m.fixture(case)
    if len(payload) > m.FIXTURE_MAX:
        raise ValueError("ustar_fixture_bound")
    operation = asyncio.create_task(
        run_process(
            argv,
            cwd=ROOT,
            timeout=max(0.001, deadline - time.monotonic()),
            deadline=deadline,
            stdin=payload,
            stdout_limit=65536,
            stderr_limit=16384,
            env=m.ENV,
            pass_fds=(*fds, *((ready_fd,) if ready_fd is not None else ())),
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
    value = m.validate_result(case, result, error, observation.cleanup_confirmed, run_id, digest)
    _deadline(matrix_deadline)
    return value


async def _qualify():
    deadline = time.monotonic() + WHOLE_SECONDS
    admission = ROOT / ADMISSION
    admission.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission, directory=True)
    spec = policy()
    digest = shared._digest(spec)
    state = _State()
    lease = AdmissionController(admission).acquire(digest)
    store = None
    results = []
    report_attempted = False
    stage = "attempt"
    try:
        attempt = _helper(state, _attempt_directory)
        store = _Evidence(attempt, state)
        store.json("policy.json", spec)
        stage = "sources"
        fds, sources = _sources(state)
        store.json("sources.json", sources)
        run_id = lease.identity[:32]
        for case in m.MATRIX:
            stage = case
            results.append(await _case(state, fds, case, attempt, run_id, digest, deadline))
        stage = "source_readback"
        _source_readback(state, fds)
        _deadline(deadline)
        report_attempted = True
        report = {
            "schema": "zelo-ustar-synthetic-isolation-proof-v1",
            "status": "cases-passed",
            "policy_digest": digest,
            "run_id": run_id,
            "cases": results,
            "support_release_confirmed": False,
            "runtime_authority": False,
            "aggregate_cgroup_verified": False,
            "package_provenance_verified": False,
            "full_runtime_closure_verified": False,
        }
        raw = store.json("cases.json", report)
        proof = str(attempt / "cases.json")
        proof_digest = hashlib.sha256(raw).hexdigest()
    except Exception:
        if store is not None and not report_attempted:
            report_attempted = True
            store.json(
                "cases.json",
                {
                    "status": "refused",
                    "stage": stage,
                    "completed_cases": results,
                    "support_release_confirmed": False,
                    "runtime_authority": False,
                },
            )
        raise
    finally:
        error_type = sys.exc_info()[0]
        if error_type is not None and not issubclass(error_type, Exception):
            _RETAINED.append((state, lease))
        else:
            try:
                released = _release(state, lease)
            except BaseException:
                _RETAINED.append((state, lease))
                raise
    if released is not True:
        raise ValueError("ustar_support_cleanup")
    _deadline(deadline)
    return {
        "status": "qualified-synthetic-ustar-isolation",
        "proof": proof,
        "proof_sha256": proof_digest,
        "policy_digest": digest,
        "cases": len(results),
        "support_release_confirmed": True,
        "runtime_authority": False,
    }, deadline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        receipt, deadline = asyncio.run(_qualify())
        rendered = json.dumps(receipt, sort_keys=True)
        _deadline(deadline)
    except Exception:
        print("ustar_isolation_refused", file=sys.stderr)
        return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
