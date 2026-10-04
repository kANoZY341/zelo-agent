#!/usr/bin/env python3
"""Fixed historical Release proof, operator-only; no package or Core authority."""

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
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_NOFILE, 256),
        (resource.RLIMIT_FSIZE, 8388608),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if (soft != resource.RLIM_INFINITY and soft < bound) or (
            hard != resource.RLIM_INFINITY and hard < bound
        ):
            raise ValueError("media_origin_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("media_origin_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import NativeExecutionObservation, run_process  # noqa: E402
from app.developer_sandbox import python_compute_sandbox, python_compute_sandbox_plan  # noqa: E402
from qualification import media_origin as origin  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from qualification.media_origin_guest import (  # noqa: E402
    GUEST_BOOTSTRAP,
    INPUTS,
    KEYRING,
    NEGATIVES,
    TOOL,
)
from scripts import qualify_media_elf_version_metadata as helpers  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_image import _repeated_cancel  # noqa: E402

QualificationRefused = helpers.QualificationRefused
_State = helpers._State
_Store = helpers._Store
_open = helpers._open
_read_fd = helpers._read_fd
_helper = helpers._helper
_release = helpers._release
MATRIX = (
    "network",
    "write",
    "host_file",
    "imports",
    "memory",
    "output",
    "cpu",
    "timeout",
    "cancel",
    "cancel_repeated",
    "setup_failure",
    "tool_pin",
    "keyring_pin",
    "input_pin",
)
ENV = {
    "PATH": "/usr/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TMPDIR": "/tmp",
    "HOME": "/tmp/zelo-gnupg",
    "GNUPGHOME": "/tmp/zelo-gnupg",
}
PROOF_BASE = "runtime/media-origin-proofs"
PROOF_PREFIX = "origin-proof-"
ADMISSION = "runtime/media-elf-metadata-admission"


def policy():
    return {
        "schema": "zelo-media-fixed-origin-confinement-v1",
        "base_namespace": python_compute_sandbox_plan(),
        "guest_bootstrap_sha256": hashlib.sha256(GUEST_BOOTSTRAP.encode()).hexdigest(),
        "tool": TOOL,
        "keyring": KEYRING,
        "inputs": INPUTS,
        "negative_inputs": NEGATIVES,
        "primary_fingerprint": origin.FINGERPRINT,
        "guest_ids": [1000, 1000],
        "capabilities": "all-dropped",
        "environment": ENV,
        "matrix": MATRIX,
        "processes": 18,
        "whole_seconds": 120,
        "setup_seconds": 30,
        "case_seconds": 15,
        "timeout_control_seconds": 2,
        "guest_cpu_seconds": 10,
        "guest_as_bytes": 268435456,
        "guest_fds": 64,
        "guest_file_core_bytes": 0,
        "nproc_supplementary": 0,
        "source_input_bytes": 524288,
        "stdout_input_bytes": 524288,
        "stdout_control_bytes": 65536,
        "stderr_bytes": 16384,
        "parent_buffers_reservation_bytes": 16777216,
        "attempts": 4,
        "proof_scope": PROOF_BASE + "/" + PROOF_PREFIX + "*",
        "admission_scope": ADMISSION,
        "retained_entries_per_attempt": 96,
        "retained_bytes_per_attempt": 8388608,
        "retained_ledger_entries": 44,
        "retained_ledger_bytes": 5533357,
        "host_tool_trust": "conditional-operator-baseline-not-platform-origin",
        "host_source_assumption": "trusted-ancestors-mounts-and-no-concurrent-mutator",
        "filesystem_deadline": "not-hard",
        "admission_release_error": "receipt-refused-marker-may-already-be-empty",
        "current_key_validity_verified": False,
        "freshness_verified": False,
        "package_member_origin_verified": False,
        "platform_complete": False,
        "abi_qualified": False,
        "aggregate_cgroup_verified": False,
        "runtime_authority": False,
        "instruction_trust": "none",
    }


def _proof_directory(base):
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    with os.scandir(base) as entries:
        for count, entry in enumerate(entries, 1):
            if count >= 4:
                raise QualificationRefused("media_origin_attempt_limit")
            path = base / entry.name
            shared._private_path(path, directory=True)
            if (
                not path.name.startswith(PROOF_PREFIX)
                or stat.S_IMODE(path.lstat().st_mode) != 0o700
            ):
                raise QualificationRefused("media_origin_attempt_identity")
    path = Path(tempfile.mkdtemp(prefix=PROOF_PREFIX, dir=base))
    os.chmod(path, 0o700)
    return path


def _argv(fds, case, sentinel, host_namespace, index, ready_fd):
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
        str(index),
    ]
    argv = python_compute_sandbox(command)
    mounts = [
        "--cap-drop",
        "ALL",
        "--uid",
        "1000",
        "--gid",
        "1000",
        "--dir",
        "/opt",
        "--dir",
        "/tmp/zelo-gnupg",
    ]
    for role, destination in (
        ("tool", "/opt/zelo-origin-gpgv"),
        ("keyring", "/opt/zelo-origin-keyring.gpg"),
        ("input", "/opt/zelo-origin.InRelease"),
    ):
        selected = fds["bad_" + role] if case == role + "_pin" else fds[role]
        mounts += ["--ro-bind", f"/proc/self/fd/{selected}", destination]
    if ready_fd >= 0:
        mounts += ["--bind-fd", str(ready_fd), "/opt/zelo-origin-ready"]
    if case == "setup_failure":
        mounts += ["--ro-bind", str(sentinel.parent / "fixed-missing"), "/opt/zelo-missing"]
    point = argv.index("--remount-ro")
    argv[point:point] = mounts
    return argv


def _host_file(state, path, size, sha, mode):
    path = Path(path)
    if path.resolve(strict=True) != path:
        raise QualificationRefused("media_origin_resolved_path")
    fd = _open(state, path)
    info = os.fstat(fd)
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode:
        raise QualificationRefused("media_origin_host_identity")
    raw = _read_fd(fd, 524288, sha, size)
    return fd, raw


def _negative(raw, name):
    if name == "altered_text":
        if raw.count(b"Origin: Ubuntu\n") != 1:
            raise QualificationRefused("media_origin_negative_source")
        result = raw.replace(b"Origin: Ubuntu\n", b"Origin: Ubuntx\n")
    else:
        raise QualificationRefused("media_origin_negative_case")
    pin = NEGATIVES[name]
    if len(result) != pin["bytes"] or hashlib.sha256(result).hexdigest() != pin["sha256"]:
        raise QualificationRefused("media_origin_negative_pin")
    return result


async def _native(state, store, fds, case, raw, label, index, job_deadline):
    deadline = min(job_deadline, time.monotonic() + (2 if case == "timeout" else 15))
    if deadline <= time.monotonic():
        raise QualificationRefused("media_origin_deadline")
    sentinel = store.path / "host-only-sentinel"
    host_namespace = os.readlink("/proc/self/ns/net")
    ready_fd = -1
    if case in ("cancel", "cancel_repeated"):
        if store.entries >= 96:
            raise QualificationRefused("media_origin_retained_entries")
        store.entries += 1
        fifo = store.path / (case + "-ready.fifo")
        os.mkfifo(fifo, mode=0o600)
        ready_fd = state.hold(
            os.open(fifo, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        )
    observation = NativeExecutionObservation()
    state.observations.append(observation)
    passed_fds = tuple(fds.values()) + ((ready_fd,) if ready_fd >= 0 else ())
    operation = asyncio.create_task(
        run_process(
            _argv(fds, case, sentinel, host_namespace, index, ready_fd),
            cwd=ROOT,
            timeout=max(0.001, deadline - time.monotonic()),
            deadline=deadline,
            stdout_limit=524288 if case in ("input", *NEGATIVES) else 65536,
            stderr_limit=16384,
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
            cancel_wait=(lambda: shared._ready(ready_fd, deadline)) if case == "cancel" else None,
        )
    if not observation.cleanup_confirmed:
        raise QualificationRefused("media_origin_cleanup_unproven")
    if result is not None:
        store.save(label + "-stdout.bin", result.stdout)
        store.save(label + "-stderr.bin", result.stderr)
    value = None
    passed = False
    if case == "input":
        if result is not None:
            try:
                value = origin.accept_release(
                    raw,
                    result.stdout,
                    result.stderr,
                    suite=INPUTS[index]["suite"],
                    exit_code=result.exit_code,
                    cleanup_confirmed=observation.cleanup_confirmed,
                )
            except origin.OriginError as refusal:
                error = str(refusal)
            else:
                passed = error is None
    elif case in NEGATIVES:
        if result is not None and error is None and result.exit_code in (1, 2):
            try:
                value = origin.validate_bad_signature_status(result.stderr)
            except origin.OriginError as refusal:
                error = str(refusal)
            else:
                passed = True
    elif case.endswith("_pin"):
        value = (
            json.loads(result.stdout)
            if result is not None and result.exit_code == 0 and not result.stderr
            else None
        )
        passed = value == {"status": case.removesuffix("_pin") + "_refused"} and error is None
    elif case == "setup_failure":
        passed = (
            result is not None
            and result.exit_code == 1
            and result.stderr.startswith(b"bwrap:")
            and str(store.path / "fixed-missing").encode() in result.stderr
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
        passed = (
            shared._network_accepted(value, host_namespace)
            if case == "network"
            else value == {"status": "probe", "case": case, "enforced": True}
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
        "evidence": value,
    }
    if not passed:
        store.json("cases.json", record)
        raise QualificationRefused("media_origin_case_failed")
    return record


async def _qualify():
    if os.getresuid() != (1000, 1000, 1000) or os.getresgid() != (1000, 1000, 1000):
        raise QualificationRefused("media_origin_operator_ids")
    started = time.monotonic()
    admission_path = ROOT / ADMISSION
    admission_path.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission_path, directory=True)
    digest = shared._digest(policy())
    lease = AdmissionController(admission_path).acquire(digest)
    state = _State()
    complete = False
    result = None
    try:
        store = _Store(_helper(state, _proof_directory, ROOT / PROOF_BASE), state)
        store.save("host-only-sentinel", b"fixed-private-origin-sentinel")
        fds = {}
        for role, pin in (("tool", TOOL), ("keyring", KEYRING)):
            fds[role], _ = _host_file(state, *pin)
        inputs = []
        for pin in INPUTS:
            fd, raw = _host_file(state, pin["source"], pin["bytes"], pin["sha256"], pin["mode"])
            origin.validate_envelope(raw)  # Syntax only; no unsigned Release parsing.
            inputs.append((fd, raw))
        for role in ("tool", "keyring", "input"):
            name = "bad-" + role
            store.save(name, b"fixed-invalid-origin-pin\n")
            (store.path / name).chmod(0o755 if role == "tool" else 0o644)
            fds["bad_" + role] = _open(state, store.path / name)
        negative_inputs = {}
        for name in NEGATIVES:
            raw = _negative(inputs[0][1], name)
            store.save(name + ".InRelease", raw)
            (store.path / (name + ".InRelease")).chmod(0o644)
            negative_inputs[name] = (_open(state, store.path / (name + ".InRelease")), raw)
        if time.monotonic() > started + 30:
            raise QualificationRefused("media_origin_setup_deadline")
        records = []
        fds["input"] = inputs[0][0]
        for case in MATRIX:
            records.append(
                await _native(state, store, fds, case, inputs[0][1], case, 0, started + 120)
            )
        for index, (fd, raw) in enumerate(inputs):
            fds["input"] = fd
            records.append(
                await _native(
                    state, store, fds, "input", raw, f"release-{index}", index, started + 120
                )
            )
        for case, (fd, raw) in negative_inputs.items():
            fds["input"] = fd
            records.append(await _native(state, store, fds, case, raw, case, 0, started + 120))
        if len(records) != 18:
            raise QualificationRefused("media_origin_job_count")
        for role, pin in (("tool", TOOL), ("keyring", KEYRING)):
            _read_fd(fds[role], 524288, pin[2], pin[1])
        for (fd, _raw), pin in zip(inputs, INPUTS, strict=True):
            _read_fd(fd, 524288, pin["sha256"], pin["bytes"])
        result = {
            "schema": "zelo-media-origin-operator-v1",
            "policy_digest": digest,
            "records": records,
            "owned_process_cleanup_confirmed": state.processes_settled,
            "admission_release_confirmed": False,
            "status": "cases-passed-pending-admission-release",
            "platform_complete": False,
            "package_member_origin_verified": False,
            "current_key_validity_verified": False,
            "freshness_verified": False,
            "abi_qualified": False,
            "aggregate_cgroup_verified": False,
            "runtime_authority": False,
            "instruction_trust": "none",
        }
        proof_raw = store.json("cases.json", result)
        complete = state.processes_settled and state.close_all()
        if not complete:
            raise QualificationRefused("media_origin_support_cleanup")
    finally:
        if not _release(state, lease):
            complete = False
    if not complete or result is None:
        raise QualificationRefused("media_origin_job_unproven")
    return {
        "status": "qualified-fixed-historical-release-declarations",
        "proof": str(store.path / "cases.json"),
        "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
        "cases": 18,
        "release_inputs": 3,
        "admission_release_confirmed": True,
        "platform_complete": False,
        "package_member_origin_verified": False,
        "current_key_validity_verified": False,
        "freshness_verified": False,
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
