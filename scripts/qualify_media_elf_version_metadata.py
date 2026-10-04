#!/usr/bin/env python3
"""Closed operator metadata qualification; no target-selected execution or Core route."""

import argparse
import asyncio
import hashlib
import json
import os
import resource
import stat
import struct
import sys
import tempfile
import time
from pathlib import Path


def _operator_limits():
    for kind, bound in (
        (resource.RLIMIT_AS, 536870912),
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_NOFILE, 256),
        (resource.RLIMIT_FSIZE, 8388608),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if (soft != resource.RLIM_INFINITY and soft < bound) or (
            hard != resource.RLIM_INFINITY and hard < bound
        ):
            raise ValueError("media_elf_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()  # This explicit CLI only; imports/tests never change shared limits.
if not __debug__:
    raise SystemExit("media_elf_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import NativeExecutionObservation, run_process  # noqa: E402
from app.developer_sandbox import python_compute_sandbox, python_compute_sandbox_plan  # noqa: E402
from qualification import media_elf_metadata as m  # noqa: E402
from qualification import media_elf_version_metadata as version  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from qualification.media_elf_version_metadata_guest import (  # noqa: E402
    GUEST_BOOTSTRAP,
    SOURCE_PINS,
)
from scripts import qualify_media_elf_metadata as prior  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_image import _repeated_cancel  # noqa: E402

TARGET_LOCK_SHA = "e6aa07b1e59342dc4b202f1f4a81f6c49a3768289d0569c6d2a9e6eb6a2f7018"
FIXTURE_SHA = "f96dfe4c8c0ba5920c55e0e65aa762e409f6c0a23082468034dbab0ad8d0be17"
TOOL_RECEIPT_SHA = "a41e2c4252dffafe8ab851f9dc2316a0830eb67eb1fe3b46683505b2a23daa52"
MATRIX = (
    "fixture",
    "malformed",
    "network",
    "write",
    "host_file",
    "imports",
    "memory",
    "output",
    "cpu",
    "cancel",
    "cancel_repeated",
    "timeout",
    "setup_failure",
    "source_pin",
    "tool_pin",
)
MALFORMED = (
    "need-revision",
    "definition-revision",
    "parent-count",
    "aux-count",
    "parent-range",
    "aux-range",
    "parent-cycle",
    "aux-cycle",
    "parent-overlap",
    "aux-overlap",
    "nonzero-padding",
    "unknown-flags",
    "reserved-index",
    "duplicate-index",
    "missing-index",
    "invalid-string",
)
FIXTURE_LAYOUT_SHA = "4537ae3138d5fdee12a10e63faf7ec64f4a1f057afbb1f375ed38d9ccbb6193a"
UNVERSIONED_SHA = "c0237b900cec0fa9b7181f8978d019a42b773150cddbf879d6bf6274b3bf1137"

ENV = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/tmp"}
_RETAINED = []  # Unknown owned cleanup retains support descriptors until operator exit.


class QualificationRefused(ValueError):
    pass


def policy():
    return {
        "schema": "zelo-media-elf-version-confinement-v1",
        "base_namespace": python_compute_sandbox_plan(),
        "capabilities": "all-dropped",
        "source_pins": SOURCE_PINS,
        "guest_bootstrap_sha256": hashlib.sha256(GUEST_BOOTSTRAP.encode()).hexdigest(),
        "tool_manifest": m.TOOL_MANIFEST_DIGEST,
        "input_lock_sha256": TARGET_LOCK_SHA,
        "input_bytes": m.INPUT_BYTES,
        "input_aggregate_bytes": 33554432,
        "stdout_bytes": 65536,
        "stderr_bytes": 16384,
        "guest_cpu_seconds": 10,
        "guest_as_bytes": 536870912,
        "guest_fds": 64,
        "guest_file_core_bytes": 0,
        "nproc_supplementary": 0,
        "whole_seconds": 420,
        "setup_seconds": 30,
        "matrix_seconds": 180,
        "metadata_seconds": 210,
        "case_seconds": 30,
        "timeout_control_seconds": 2,
        "processes": 44,
        "attempts": 4,
        "proof_scope": "runtime/media-elf-version-proofs/version-proof-*",
        "version_work_limits": version.LIMITS,
        "fixture_sha256": FIXTURE_SHA,
        "fixture_layout_sha256": FIXTURE_LAYOUT_SHA,
        "unversioned_sha256": UNVERSIONED_SHA,
        "retained_bytes_per_attempt": 8388608,
        "retained_entries_per_attempt": 96,
        "source_files": 5,
        "source_file_bytes": 32768,
        "source_aggregate_bytes": 65536,
        "environment": ENV,
        "parent_buffers_reservation_bytes": 134217728,
        "host_source_assumption": "trusted-ancestors-and-no-concurrent-mutator",
        "filesystem_deadline": "not-hard",
        "admission_release_error": "receipt-refused-marker-may-already-be-empty",
        "platform_complete": False,
        "abi_qualified": False,
        "aggregate_cgroup_verified": False,
        "runtime_authority": False,
        "instruction_trust": "none",
    }


class _State:
    def __init__(self):
        self.fds = []
        self.observations = []
        self.close_ok = True

    def hold(self, fd):
        try:
            self.fds.append(fd)
        except BaseException:
            self.close_ok = False
            try:
                os.close(fd)
            finally:
                raise
        return fd

    def close_all(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                self.close_ok = False
        self.fds.clear()  # Each original FD attempted once; never retry numbers.
        return self.close_ok

    @property
    def processes_settled(self):
        return all(observation.cleanup_confirmed is True for observation in self.observations)


def _release(state, lease):
    if not state.processes_settled:
        _RETAINED.append(state)
        cleanup_confirmed = False
    else:
        cleanup_confirmed = state.close_all()
    # Release exceptions refuse receipt; unchanged helper may have cleared marker.
    lease.finish(cleanup_confirmed=cleanup_confirmed)
    return cleanup_confirmed


def _helper(state, operation, *args, **kwargs):
    # Shared helpers can hide descriptor close errors inside generic refusals.
    # Conservatively fence every helper failure; never infer/retry an internal FD.
    try:
        return operation(*args, **kwargs)
    except BaseException:
        state.close_ok = False
        raise


class _Store:
    def __init__(self, path, state):
        self.path = path
        self.state = state
        self.bytes = 0
        self.entries = 0

    def save(self, name, value):
        if (
            type(value) is not bytes
            or len(value) > 2097152
            or self.bytes + len(value) > 8388608
            or self.entries >= 96
        ):
            raise QualificationRefused("media_elf_retained_bound")
        self.bytes += len(value)
        self.entries += 1
        try:
            shared._save(self.path / name, value)
        except BaseException:
            self.state.close_ok = False  # Conservative: failure may include unknown close.
            raise

    def directory(self, name):
        if self.entries >= 96:
            raise QualificationRefused("media_elf_retained_entries")
        self.entries += 1
        (self.path / name).mkdir(mode=0o700)

    def json(self, name, value):
        raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        self.save(name, raw)
        return raw


def _read_fd(fd, bound, digest=None, size=None):
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_mode & 0o022
        or not 0 < before.st_size <= bound
        or (size is not None and before.st_size != size)
    ):
        raise QualificationRefused("media_elf_source_file")
    os.lseek(fd, 0, os.SEEK_SET)
    value = bytearray()
    while chunk := os.read(fd, min(65536, before.st_size - len(value) + 1)):
        value.extend(chunk)
        if len(value) > before.st_size:
            raise QualificationRefused("media_elf_source_size")
    after = os.fstat(fd)
    if len(value) != before.st_size or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mode,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mode,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise QualificationRefused("media_elf_source_drift")
    raw = bytes(value)
    if digest is not None and hashlib.sha256(raw).hexdigest() != digest:
        raise QualificationRefused("media_elf_source_pin")
    return raw


def _open(state, path, *, directory=False):
    return state.hold(
        os.open(
            path,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | os.O_CLOEXEC
            | (os.O_DIRECTORY if directory else 0),
        )
    )


def _proof_directory(base):
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    with os.scandir(base) as entries:
        for count, entry in enumerate(entries, 1):
            if count >= 4:
                raise QualificationRefused("media_elf_attempt_limit")
            path = base / entry.name
            shared._private_path(path, directory=True)
            if (
                not path.name.startswith("version-proof-")
                or stat.S_IMODE(path.lstat().st_mode) != 0o700
            ):
                raise QualificationRefused("media_elf_attempt_identity")
    path = Path(tempfile.mkdtemp(prefix="version-proof-", dir=base))
    os.chmod(path, 0o700)
    return path


def _argv(
    tool_fd, source_fds, case, sentinel, host_namespace, bad_source_fd, bad_tool_fd, ready_fd
):
    command = [
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-B",
        "-c",
        GUEST_BOOTSTRAP,
        case,
        str(sentinel),
        host_namespace,
    ]
    argv = python_compute_sandbox(command)
    mounts = [
        "--cap-drop",
        "ALL",
        "--dir",
        "/opt/zelo-elf-source",
        "--dir",
        "/opt/zelo-elf-source/qualification",
        "--ro-bind",
        f"/proc/self/fd/{bad_tool_fd if case == 'tool_pin' else tool_fd}",
        m.TOOL_ROOT,
    ]
    for index, ((name, _, _), fd) in enumerate(zip(SOURCE_PINS, source_fds, strict=True)):
        selected = bad_source_fd if case == "source_pin" and index == 0 else fd
        mounts += ["--ro-bind", f"/proc/self/fd/{selected}", "/opt/zelo-elf-source/" + name]
    if ready_fd >= 0:
        mounts += ["--bind-fd", str(ready_fd), "/opt/zelo-elf-ready"]
    if case == "setup_failure":
        mounts += ["--ro-bind", str(sentinel.parent / "fixed-missing"), "/opt/zelo-elf-missing"]
    argv[argv.index("--remount-ro") : argv.index("--remount-ro")] = mounts
    return argv


def _metadata_result(stdout, raw):
    try:
        if type(stdout) is not bytes or len(stdout) > 65536:
            raise QualificationRefused("media_elf_version_result_size")
        value = json.loads(stdout)
        canonical = version.validate_report(value, raw)
        prior._metadata_result(version.canonical(value["loader_report"]) + b"\n", raw)
        if stdout != canonical + b"\n":
            raise QualificationRefused("media_elf_version_result_canonical")
        return value
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
        raise QualificationRefused("media_elf_version_result_refused") from None


def _fixture_oracle(value, versioned):
    bound = value["loader_report"]
    expected_counts = (5, 5, 3, 3, 1, 1, 4, 4, 1, 1, 1) if versioned else (5,) + (0,) * 10
    if (
        bound["interpreter"] != "/lib64/ld-linux-x86-64.so.2"
        or len(bound["sections"]) != (9 if versioned else 6)
        or [item.get("string") for item in bound["dynamic"] if item["tag"] == "DT_NEEDED"]
        != ["libc.so.6"]
        or value["versioned"] is not versioned
        or value["counts"] != dict(zip(version.COUNT_FIELDS, expected_counts, strict=True))
    ):
        raise QualificationRefused("media_elf_version_fixture_oracle")
    if versioned:
        expected_definitions = [
            {
                "index": 1,
                "flags": 1,
                "hash": 0x12340001,
                "name": "fixture.so",
                "dependency_names": [],
                "aux_declared": 1,
                "aux_decoded": 1,
            },
            {
                "index": 2,
                "flags": 0,
                "hash": 0x12340002,
                "name": "FIXTURE_1.0",
                "dependency_names": [],
                "aux_declared": 1,
                "aux_decoded": 1,
            },
            {
                "index": 3,
                "flags": 0,
                "hash": 0x12340003,
                "name": "FIXTURE_2.0",
                "dependency_names": ["FIXTURE_1.0"],
                "aux_declared": 2,
                "aux_decoded": 2,
            },
        ]
        expected_needs = [
            {
                "provider": "libc.so.6",
                "aux_declared": 1,
                "aux_decoded": 1,
                "versions": [{"index": 4, "flags": 0, "hash": 0x56780004, "name": "GLIBC_2.34"}],
            }
        ]
        expected_frequencies = [
            {"raw": r, "index": r & 0x7FFF, "hidden": bool(r & 0x8000), "count": 1}
            for r in (0, 1, 2, 4, 0x8003)
        ]
        if (
            value["definitions"] != expected_definitions
            or value["needs"] != expected_needs
            or value["frequencies"] != expected_frequencies
        ):
            raise QualificationRefused("media_elf_version_fixture_oracle")


async def _native(
    state, store, tool_fd, source_fds, bad_source_fd, bad_tool_fd, case, raw, label, phase_deadline
):
    deadline = min(phase_deadline, time.monotonic() + (2 if case == "timeout" else 30))
    if deadline <= time.monotonic():
        raise QualificationRefused("media_elf_deadline")
    host_namespace = os.readlink("/proc/self/ns/net")
    sentinel = store.path / "host-only-sentinel"
    ready_fd = -1
    if case in ("cancel", "cancel_repeated"):
        fifo = store.path / (case + "-ready.fifo")
        if store.entries >= 96:
            raise QualificationRefused("media_elf_retained_entries")
        store.entries += 1
        os.mkfifo(fifo, mode=0o600)
        ready_fd = state.hold(
            os.open(fifo, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        )
    observation = NativeExecutionObservation()
    state.observations.append(observation)  # Register before task/pending creation.
    passed_fds = (tool_fd, *source_fds, bad_source_fd, bad_tool_fd) + (
        (ready_fd,) if ready_fd >= 0 else ()
    )
    operation = asyncio.create_task(
        run_process(
            _argv(
                tool_fd,
                source_fds,
                case,
                sentinel,
                host_namespace,
                bad_source_fd,
                bad_tool_fd,
                ready_fd,
            ),
            cwd=ROOT,
            timeout=max(0.001, deadline - time.monotonic()),
            deadline=deadline,
            stdout_limit=65536,
            stderr_limit=16384,
            stdin=raw,
            env=ENV,
            pass_fds=passed_fds,
            observation=observation,
        )
    )
    if case == "cancel_repeated":
        result, error = await _repeated_cancel(operation, ready_fd, deadline)
    else:
        result, error = await shared._own_operation(
            operation,
            cancel_wait=(lambda: shared._ready(ready_fd, deadline)) if ready_fd >= 0 else None,
        )
    if not observation.cleanup_confirmed:
        raise QualificationRefused("media_elf_cleanup_unproven")
    value = None
    if result is not None:
        store.save(label + "-stdout.bin", result.stdout)
        store.save(label + "-stderr.bin", result.stderr)
    if case in ("fixture", "unversioned_fixture", "input"):
        if result is None or result.exit_code != 0 or result.stderr:
            raise QualificationRefused("media_elf_metadata_process")
        value = _metadata_result(result.stdout, raw)
        if case in ("fixture", "unversioned_fixture"):
            _fixture_oracle(value, case == "fixture")
        passed = True
    elif case in ("malformed", "source_pin", "tool_pin"):
        expected_status = {
            "malformed": "metadata_refused",
            "source_pin": "source_refused",
            "tool_pin": "tool_refused",
        }[case]
        passed = (
            result is not None
            and result.exit_code == 0
            and not result.stderr
            and json.loads(result.stdout) == {"status": expected_status}
        )
    elif case == "setup_failure":
        missing = str(store.path / "fixed-missing").encode()
        passed = (
            result is not None
            and result.exit_code == 1
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
        value = (
            json.loads(result.stdout)
            if result is not None and result.exit_code == 0 and not result.stderr
            else None
        )
        if case == "network":
            passed = (
                type(value) is dict
                and value.get("status") == "bounded_probe"
                and value.get("probe") == "network"
                and shared._network_accepted(value, host_namespace)
            )
        else:
            passed = (
                value == {"status": "probe", "case": case, "enforced": True}
                and type(value.get("enforced")) is bool
            )
    record = {
        "label": label,
        "case": case,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_bytes": len(raw),
        "passed": bool(passed),
        "cleanup_confirmed": observation.cleanup_confirmed,
        "error": error,
        "exit_code": result.exit_code if result is not None else None,
        "cancel_requests": 2 if case == "cancel_repeated" else 1 if case == "cancel" else 0,
        "metadata": value,
    }
    if not passed:
        store.json("cases.json", record)
        raise QualificationRefused("media_elf_case_failed")
    return record


def _malformed(fixture, layout, name):
    raw = bytearray(fixture)
    sections = layout["sections"]
    definition = sections[".gnu.version_d"]
    need = sections[".gnu.version_r"]
    mutations = {
        "need-revision": (need, "<H", 2),
        "definition-revision": (definition, "<H", 2),
        "parent-count": (sections[".dynamic"] + 8 * 16 + 8, "<Q", 129),
        "aux-count": (definition + 6, "<H", 65),
        "parent-range": (definition + 16, "<I", 65536),
        "aux-range": (need + 8, "<I", 65536),
        "parent-cycle": (definition + 16, "<I", 0),
        "aux-cycle": (definition + 88, "<I", 0),
        "parent-overlap": (definition + 16, "<I", 4),
        "aux-overlap": (definition + 12, "<I", 4),
        "nonzero-padding": (definition + 28, "<B", 1),
        "unknown-flags": (definition + 2, "<H", 4),
        "reserved-index": (sections[".gnu.version"] + 8, "<H", 0xFF00),
        "duplicate-index": (definition + 32 + 4, "<H", 1),
        "missing-index": (sections[".gnu.version"] + 8, "<H", 5),
        "invalid-string": (sections[".dynstr"] + layout["strings"]["FIXTURE_1.0"], "<B", 255),
    }
    if name not in mutations:
        raise QualificationRefused("media_elf_version_malformed_case")
    offset, form, value = mutations[name]
    struct.pack_into(form, raw, offset, value)
    return bytes(raw)


async def _qualify():
    started = time.monotonic()
    admission_path = ROOT / "runtime/media-elf-metadata-admission"
    admission_path.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission_path, directory=True)
    digest = shared._digest(policy())
    lease = AdmissionController(admission_path).acquire(digest)
    state = _State()
    complete = False
    result = None
    try:
        store = _Store(
            _helper(state, _proof_directory, ROOT / "runtime/media-elf-version-proofs"), state
        )
        store.save("host-only-sentinel", b"fixed-private-metadata-sentinel")
        store.save("bad-source.py", b"raise RuntimeError('UNVERIFIED_INITIALIZER_EXECUTED')\n")
        bad_source_fd = _open(state, store.path / "bad-source.py")
        bad_tool_path = store.path / "bad-tool"
        store.directory("bad-tool")
        store.save("bad-tool/inventory-anchor", b"# invalid tool tree\n")
        (bad_tool_path / "inventory-anchor").chmod(0o500)
        bad_tool_path.chmod(0o500)
        bad_tool_fd = _open(state, bad_tool_path, directory=True)
        tool_receipt = json.loads(
            _helper(
                state,
                shared._read,
                ROOT / "runtime/media-elf-tool-real-source-copy.json",
                65536,
                TOOL_RECEIPT_SHA,
            )
        )
        tool_path = (
            ROOT
            / "runtime/media-elf-tool-preparation"
            / tool_receipt["receipt"]["relative_payload"]
        )
        shared._private_path(tool_path, directory=True)
        tool_fd = _open(state, tool_path, directory=True)
        if (
            tuple(tool_receipt["receipt"]["root_identity"])
            != (os.fstat(tool_fd).st_dev, os.fstat(tool_fd).st_ino)
            or stat.S_IMODE(os.fstat(tool_fd).st_mode) != 0o500
        ):
            raise QualificationRefused("media_elf_tool_identity")
        if (
            _helper(state, m.capture_manifest, tool_path, m.TOOL_ROLES, m.TOOL_POLICY).digest
            != m.TOOL_MANIFEST_DIGEST
        ):
            raise QualificationRefused("media_elf_tool_pin")
        source_fds = []
        for name, size, sha in SOURCE_PINS:
            fd = _open(state, ROOT / name)
            if os.fstat(fd).st_uid != os.getuid():
                raise QualificationRefused("media_elf_source_owner")
            _read_fd(fd, 32768, sha, size)
            source_fds.append(fd)
        fixture_fd = _open(state, ROOT / "qualification/fixtures/media-elf-version-positive.bin")
        fixture = _read_fd(fixture_fd, 65536, FIXTURE_SHA, 1456)
        unversioned_fd = _open(
            state, ROOT / "qualification/fixtures/media-elf-version-unversioned.bin"
        )
        unversioned = _read_fd(unversioned_fd, 65536, UNVERSIONED_SHA, 984)
        lock_fd = _open(state, ROOT / "qualification/fixtures/media-platform-elf-target-lock.json")
        lock = json.loads(_read_fd(lock_fd, 16384, TARGET_LOCK_SHA))
        if len(lock["files"]) != 12 or lock["aggregate_bytes"] > 33554432:
            raise QualificationRefused("media_elf_target_lock")
        if time.monotonic() > started + 30:
            raise QualificationRefused("media_elf_setup_deadline")
        matrix_deadline = min(started + 420, time.monotonic() + 180)
        records = []
        for case in (*MATRIX, "unversioned_fixture"):
            raw = unversioned if case == "unversioned_fixture" else fixture
            if case == "malformed":
                changed = bytearray(raw)
                changed[4] = 1
                raw = bytes(changed)
            records.append(
                await _native(
                    state,
                    store,
                    tool_fd,
                    source_fds,
                    bad_source_fd,
                    bad_tool_fd,
                    case,
                    raw,
                    case,
                    matrix_deadline,
                )
            )
        layout_fd = _open(state, ROOT / "qualification/fixtures/media-elf-version-positive.json")
        layout = json.loads(_read_fd(layout_fd, 16384, FIXTURE_LAYOUT_SHA))["layout"]
        for name in MALFORMED:
            raw = _malformed(fixture, layout, name)
            records.append(
                await _native(
                    state,
                    store,
                    tool_fd,
                    source_fds,
                    bad_source_fd,
                    bad_tool_fd,
                    "malformed",
                    raw,
                    "malformed-" + name,
                    matrix_deadline,
                )
            )
        metadata_deadline = min(started + 420, time.monotonic() + 210)
        total = 0
        for index, target in enumerate(lock["files"]):
            path = Path(target["source"]).resolve(strict=True)
            if str(path) != target["resolved_source"] or not path.is_relative_to(Path("/usr")):
                raise QualificationRefused("media_elf_target_path")
            fd = _open(state, path)
            info = os.fstat(fd)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != target["mode"]:
                raise QualificationRefused("media_elf_target_owner")
            raw = _read_fd(fd, m.INPUT_BYTES, target["sha256"], target["bytes"])
            total += len(raw)
            if total > 33554432:
                raise QualificationRefused("media_elf_target_aggregate")
            records.append(
                await _native(
                    state,
                    store,
                    tool_fd,
                    source_fds,
                    bad_source_fd,
                    bad_tool_fd,
                    "input",
                    raw,
                    f"target-{index:02d}",
                    metadata_deadline,
                )
            )
        if len(records) != 44 or total != lock["aggregate_bytes"]:
            raise QualificationRefused("media_elf_job_count")
        for (_name, size, sha), fd in zip(SOURCE_PINS, source_fds, strict=True):
            _read_fd(fd, 32768, sha, size)
        if (
            _helper(state, m.capture_manifest, tool_path, m.TOOL_ROLES, m.TOOL_POLICY).digest
            != m.TOOL_MANIFEST_DIGEST
        ):
            raise QualificationRefused("media_elf_tool_drift")
        result = {
            "schema": "zelo-media-elf-version-operator-v1",
            "policy_digest": digest,
            "tool_manifest_digest": m.TOOL_MANIFEST_DIGEST,
            "source_pins": SOURCE_PINS,
            "target_lock_sha256": TARGET_LOCK_SHA,
            "input_bytes": total,
            "records": records,
            "owned_process_cleanup_confirmed": state.processes_settled,
            "admission_release_confirmed": False,
            "status": "cases-passed-pending-admission-release",
            "platform_complete": False,
            "abi_qualified": False,
            "aggregate_cgroup_verified": False,
            "runtime_authority": False,
            "instruction_trust": "none",
        }
        proof_raw = store.json(
            "cases.json", result
        )  # Close report FD before successful lease release.
        complete = state.processes_settled and state.close_all()
        if not complete:
            raise QualificationRefused("media_elf_support_cleanup")
    finally:
        # Known refusal releases only after every owned observation/support close.
        if not _release(state, lease):
            complete = False
    if not complete or result is None:
        raise QualificationRefused("media_elf_job_unproven")
    proof = store.path / "cases.json"
    return {
        "status": "qualified-version-graph-tool",
        "proof": str(proof),
        "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
        "cases": 44,
        "metadata_inputs": 12,
        "admission_release_confirmed": True,
        "platform_complete": False,
        "abi_qualified": False,
        "runtime_authority": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(asyncio.run(_qualify()), sort_keys=True))


if __name__ == "__main__":
    main()
