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
from qualification.admission import AdmissionController  # noqa: E402
from qualification.media_elf_metadata_guest import GUEST_BOOTSTRAP, SOURCE_PINS  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_image import _repeated_cancel  # noqa: E402

TARGET_LOCK_SHA = "53ccb8107b27a9141dd3267df44970426ef76eb6248e1b47984df26fe6c4e30c"
FIXTURE_SHA = "3e8d3c2efcfc2037f460288107e722d6c49688871a51717082ff4fb84a925c73"
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
    "elf-class",
    "byte-order",
    "padding",
    "program-flags",
    "program-extent",
    "load-alignment",
    "extended-section-names",
    "section-link",
    "compressed-section",
    "missing-dynamic-null",
    "string-offset",
    "string-termination",
    "string-encoding",
    "string-mapping",
    "duplicate-tag",
)
FIXTURE_LAYOUT_SHA = "8918cabc1e8a4dd823a45138083a23f82986bc3316b9d1aef85cbc690e7827cb"

ENV = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/tmp"}
_RETAINED = []  # Unknown owned cleanup retains support descriptors until operator exit.


class QualificationRefused(ValueError):
    pass


def policy():
    return {
        "schema": "zelo-media-elf-metadata-confinement-v1",
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
        "processes": 37,
        "attempts": 4,
        "retained_bytes_per_attempt": 8388608,
        "retained_entries_per_attempt": 96,
        "source_files": 4,
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
                not path.name.startswith("metadata-proof-")
                or stat.S_IMODE(path.lstat().st_mode) != 0o700
            ):
                raise QualificationRefused("media_elf_attempt_identity")
    path = Path(tempfile.mkdtemp(prefix="metadata-proof-", dir=base))
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
    value = json.loads(stdout)
    expected = {
        "schema",
        "source_sha256",
        "source_bytes",
        "header",
        "osabi",
        "interpreter",
        "programs",
        "sections",
        "dynamic",
        "uninterpreted_bodies",
        "symbol_version_content_verified",
        "abi_qualified",
        "platform_complete",
        "runtime_authority",
        "instruction_trust",
    }
    if (
        type(value) is not dict
        or set(value) != expected
        or value["schema"] != "zelo-media-elf-loader-topology-v1"
        or type(value["source_bytes"]) is not int
        or value["source_bytes"] != len(raw)
        or value["source_sha256"] != hashlib.sha256(raw).hexdigest()
        or value["instruction_trust"] != "none"
    ):
        raise QualificationRefused("media_elf_result_binding")
    if any(
        value[key] is not False
        for key in (
            "symbol_version_content_verified",
            "abi_qualified",
            "platform_complete",
            "runtime_authority",
        )
    ):
        raise QualificationRefused("media_elf_result_claim")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )
    if (
        stdout != canonical + b"\n"
        or len(canonical) > 65536
        or type(value["programs"]) is not list
        or not 1 <= len(value["programs"]) <= 64
        or type(value["sections"]) is not list
        or not 2 <= len(value["sections"]) <= 512
        or type(value["dynamic"]) is not list
        or len(value["dynamic"]) > 1023
    ):
        raise QualificationRefused("media_elf_result_shape")
    return value


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
    if case in ("fixture", "input"):
        if result is None or result.exit_code != 0 or result.stderr:
            raise QualificationRefused("media_elf_metadata_process")
        value = _metadata_result(result.stdout, raw)
        if case == "fixture" and (
            value["interpreter"] != "/lib64/ld-linux-x86-64.so.2"
            or len(value["sections"]) != 9
            or [item.get("string") for item in value["dynamic"] if item["tag"] == "DT_NEEDED"]
            != ["libc.so.6"]
        ):
            raise QualificationRefused("media_elf_fixture_oracle")
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
        store.json(label + "-failed.json", record)
        raise QualificationRefused("media_elf_case_failed")
    return record


def _malformed(fixture, layout, name):
    raw = bytearray(fixture)
    sections = layout["sections"]
    shoff = layout["section_table"]
    dynamic = sections[".dynamic"]
    if name == "elf-class":
        raw[4] = 1
    elif name == "byte-order":
        raw[5] = 2
    elif name == "padding":
        raw[9] = 1
    elif name == "program-flags":
        struct.pack_into("<I", raw, 68, 8)
    elif name == "program-extent":
        struct.pack_into("<Q", raw, 96, 2**64 - 1)
    elif name == "load-alignment":
        struct.pack_into("<Q", raw, 80, 0x400001)
    elif name == "extended-section-names":
        struct.pack_into("<H", raw, 62, 0xFFFF)
    elif name == "section-link":
        struct.pack_into("<I", raw, shoff + 6 * 64 + 40, 9)
    elif name == "compressed-section":
        struct.pack_into("<Q", raw, shoff + 64 + 8, 0x800)
    elif name == "missing-dynamic-null":
        struct.pack_into("<q", raw, dynamic + 11 * 16, 30)
    elif name == "string-offset":
        struct.pack_into("<Q", raw, dynamic + 8, 99999)
    elif name == "string-termination":
        begin = sections[".dynstr"] + 1
        end = sections[".dynsym"]
        raw[begin:end] = b"x" * (end - begin)
    elif name == "string-encoding":
        raw[sections[".dynstr"] + 1] = 255
    elif name == "string-mapping":
        struct.pack_into("<Q", raw, dynamic + 2 * 16 + 8, 0x400000 + sections[".dynstr"] + 1)
    elif name == "duplicate-tag":
        struct.pack_into("<q", raw, dynamic + 3 * 16, 5)
    else:
        raise QualificationRefused("media_elf_malformed_case")
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
            _helper(state, _proof_directory, ROOT / "runtime/media-elf-metadata-proofs"), state
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
        fixture_fd = _open(state, ROOT / "qualification/fixtures/media-elf-loader-positive.bin")
        fixture = _read_fd(fixture_fd, 65536, FIXTURE_SHA, 1328)
        lock_fd = _open(state, ROOT / "qualification/fixtures/media-elf-target-lock.json")
        lock = json.loads(_read_fd(lock_fd, 16384, TARGET_LOCK_SHA))
        if len(lock["files"]) != 7 or lock["aggregate_bytes"] > 33554432:
            raise QualificationRefused("media_elf_target_lock")
        if time.monotonic() > started + 30:
            raise QualificationRefused("media_elf_setup_deadline")
        matrix_deadline = min(started + 420, time.monotonic() + 180)
        records = []
        for case in MATRIX:
            raw = fixture
            if case == "malformed":
                changed = bytearray(raw)
                struct.pack_into("<H", changed, 60, 513)
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
        layout_fd = _open(state, ROOT / "qualification/fixtures/media-elf-loader-positive.json")
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
        if len(records) != 37 or total != lock["aggregate_bytes"]:
            raise QualificationRefused("media_elf_job_count")
        for (_name, size, sha), fd in zip(SOURCE_PINS, source_fds, strict=True):
            _read_fd(fd, 32768, sha, size)
        if (
            _helper(state, m.capture_manifest, tool_path, m.TOOL_ROLES, m.TOOL_POLICY).digest
            != m.TOOL_MANIFEST_DIGEST
        ):
            raise QualificationRefused("media_elf_tool_drift")
        result = {
            "schema": "zelo-media-elf-metadata-operator-v1",
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
        "status": "qualified-metadata-tool",
        "proof": str(proof),
        "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
        "cases": 37,
        "metadata_inputs": 7,
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
