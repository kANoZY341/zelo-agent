"""Offline policy/harness checks never launch optional native Shell guests."""

import asyncio
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import developer_process
from app.developer_process import DeveloperError, ProcessResult
from qualification import shell_compute as boundary
from scripts import qualify_shell_compute as proof


@pytest.fixture(autouse=True)
def no_native_guest(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("native guest launched by offline qualification tests")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(developer_process, "spawn_owned_process", forbidden)
    monkeypatch.setattr(boundary.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(boundary.os, "getegid", lambda: 1000)


def test_host_harness_requires_opt_in_before_native_action(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["proof"])
    monkeypatch.setattr(proof, "qualify", lambda *a: pytest.fail("native qualification reached"))
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2


def test_root_is_rejected_before_source_or_process_actions(monkeypatch):
    monkeypatch.setattr(boundary.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        boundary, "verify_sources", lambda *a: pytest.fail("root source scan reached")
    )
    with pytest.raises(DeveloperError, match="shell_nonroot_required"):
        asyncio.run(boundary.run_probe("printf harmless"))


def test_policy_mounts_only_fixed_runtime_artifacts_with_no_authority_inputs():
    args = boundary.arguments()
    assert args[:7] == [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
    ]
    assert "--disable-userns" in args and "--clearenv" in args
    mounts = [(args[i + 1], args[i + 2]) for i, arg in enumerate(args) if arg == "--ro-bind"]
    assert mounts == [
        (source, destination) for source, _, destination in boundary.SOURCES if destination
    ]
    assert not any(arg in args for arg in ("--bind", "--bind-try", "--ro-bind-try", "--share-net"))
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "--remount-ro"] == [
        "/proc",
        "/dev",
        "/tmp",
        "/",
    ]
    assert "--nproc=0:0" in args and "--cpu=2:2" in args and "--nofile=32:32" in args
    assert "--as=268435456:268435456" in args and "--fsize=0:0" in args and "--core=0:0" in args
    assert args[-2:] == ["/usr/bin/dash", "-s"]
    assert all(len(digest) == 64 for _, digest, _ in boundary.SOURCES)


@pytest.mark.parametrize(
    "script,timeout",
    [
        ("", 1),
        ("x\0y", 1),
        ("é" * 40000, 1),
        ("echo x", True),
        ("echo x", 0),
        ("echo x", 3),
        ("echo x", float("nan")),
    ],
)
def test_invalid_inputs_fail_before_source_verification(script, timeout, monkeypatch):
    monkeypatch.setattr(
        boundary, "verify_sources", lambda *a: pytest.fail("invalid input source scan")
    )
    with pytest.raises(DeveloperError, match="shell_probe_input_invalid"):
        asyncio.run(boundary.run_probe(script, timeout=timeout))


def test_verified_source_precedes_closed_process_environment(monkeypatch):
    events = []
    monkeypatch.setenv("ZELO_OPERATOR_TOKEN", "ambient-sentinel")
    monkeypatch.setenv("ENV", "/live/profile")
    monkeypatch.setattr(boundary, "verify_sources", lambda *a: events.append("verify"))

    artifacts = [SimpleNamespace(fd=100 + i) for i in range(len(boundary.SOURCES))]

    @contextmanager
    def pins(*args):
        events.append("pin")
        yield artifacts
        events.append("close_pins")

    monkeypatch.setattr(boundary, "_pin_sources", pins)

    async def run(argv, **kwargs):
        events.append("spawn")
        assert argv == boundary._pinned_arguments(artifacts)
        assert argv[0] == "/proc/self/fd/100"
        assert "--ro-bind" not in argv and argv.count("--ro-bind-data") == 5
        assert kwargs["pass_fds"] == tuple(range(100, 106))
        assert kwargs["env"] == {
            "PATH": "/usr/bin",
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
        }
        assert kwargs["stdin"] == b"printf harmless"
        assert kwargs["stdout_limit"] == 65536 and kwargs["stderr_limit"] == 16384
        assert 0 < kwargs["timeout"] <= 1
        return ProcessResult(0, b"harmless", b"", 0.1)

    monkeypatch.setattr(boundary, "run_process", run)
    assert asyncio.run(boundary.run_probe("printf harmless", timeout=1)).stdout == b"harmless"
    assert events == ["verify", "pin", "spawn", "close_pins"]


def test_unavailable_source_has_no_host_shell_fallback(monkeypatch):
    def fail(*args):
        raise DeveloperError("shell_source_changed")

    monkeypatch.setattr(boundary, "verify_sources", fail)
    monkeypatch.setattr(
        boundary, "run_process", lambda *a, **kw: pytest.fail("source failure fallback")
    )
    with pytest.raises(DeveloperError, match="shell_source_changed"):
        asyncio.run(boundary.run_probe("printf harmless"))


def test_verify_file_checks_digest_owner_and_symlink_without_process(tmp_path):
    path = tmp_path / "artifact"
    path.write_bytes(b"trusted fixture")
    path.chmod(0o600)
    owner = path.stat().st_uid
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    boundary.verify_file(path, digest, owner=owner)
    with pytest.raises(DeveloperError, match="shell_source_invalid"):
        boundary.verify_file(path, digest, owner=owner + 1)
    with pytest.raises(DeveloperError, match="shell_source_changed"):
        boundary.verify_file(path, "0" * 64, owner=owner)
    link = tmp_path / "alias"
    link.symlink_to(path)
    with pytest.raises(DeveloperError, match="shell_source_invalid"):
        boundary.verify_file(link, digest, owner=owner)
    path.chmod(0o622)
    with pytest.raises(DeveloperError, match="shell_source_invalid"):
        boundary.verify_file(path, digest, owner=owner)


def test_verify_file_detects_change_between_reads(tmp_path, monkeypatch):
    path = tmp_path / "artifact"
    path.write_bytes(b"initial fixture")
    path.chmod(0o600)
    owner = path.stat().st_uid
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original = os.fstat
    calls = []

    def fstat(fd):
        calls.append(fd)
        if len(calls) == 2:
            path.write_bytes(b"changed fixture")
        return original(fd)

    monkeypatch.setattr(boundary.os, "fstat", fstat)
    with pytest.raises(DeveloperError, match="shell_source_changed"):
        boundary.verify_file(path, digest, owner=owner)


def guest_status():
    return (
        "Uid:\t1000\t1000\t1000\t1000\nGid:\t1000\t1000\t1000\t1000\n"
        "CapInh:\t0000000000000000\nCapPrm:\t0000000000000000\n"
        "CapEff:\t0000000000000000\nCapBnd:\t0000000000000000\n"
        "CapAmb:\t0000000000000000\nNoNewPrivs:\t1\n__LIMITS__\n"
        "Max processes 0 0 processes\nMax open files 32 32 files\n"
        "Max file size 0 0 bytes\nMax core file size 0 0 bytes\n"
        "Max address space 268435456 268435456 bytes\nMax cpu time 2 2 seconds\n"
        "__NETWORK__\nlo: 0 0 0\n"
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("Uid:\t1000", "Uid:\t0"),
        ("CapEff:\t0000000000000000", "CapEff:\t0000000000000001"),
        ("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
        ("Max processes 0 0", "Max processes 0 1"),
        ("Max address space 268435456 268435456", "Max address space 268435456 unlimited"),
        ("lo: 0", "eth0: 0"),
    ],
)
def test_effective_guest_evidence_rejects_privilege_limit_or_network_mismatch(old, new):
    proof.check_status(guest_status())
    with pytest.raises(RuntimeError):
        proof.check_status(guest_status().replace(old, new))


def test_native_failure_report_is_private_bounded_and_does_not_echo_error(tmp_path, monkeypatch):
    async def fail(root, report):
        raise RuntimeError("private error output must not be persisted")

    monkeypatch.setattr(proof, "exercise", fail)
    monkeypatch.setattr(
        proof, "command", lambda args: "a" * 40 + "\n" if "rev-parse" in args else ""
    )
    path, passed = proof.qualify(tmp_path / "proofs")
    report = json.loads(path.read_bytes())
    assert not passed and report["status"] == "failed"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert len(path.read_bytes()) < 131072
    assert "private error output" not in path.read_text()
    assert not report["core_worker_activated"] and not report["external_network_used"]


def test_qualification_module_is_not_imported_by_core():
    assert not any(
        "qualification.shell_compute" in path.read_text() for path in Path("app").rglob("*.py")
    )


def test_pins_close_previous_artifacts_when_later_identity_is_rejected(monkeypatch):
    events = []

    @contextmanager
    def pin(path, *, approved_path, approved_digest, name, cleanup_observation):
        cleanup_observation._claim()
        index = next(i for i, source in enumerate(boundary.SOURCES) if source[0] == str(path))
        assert approved_path == str(path) and approved_digest == boundary.SOURCES[index][1]
        if index == 2:
            cleanup_observation._confirmed = cleanup_observation._terminal = True
            raise DeveloperError("worker_configuration_changed")
        events.append(("open", index))
        try:
            yield SimpleNamespace(fd=index + 100, verify=lambda: events.append(("verify", index)))
        finally:
            events.append(("close", index))
            cleanup_observation._confirmed = cleanup_observation._terminal = True

    monkeypatch.setattr(boundary, "pin_artifact", pin)
    with pytest.raises(DeveloperError, match="worker_configuration_changed"):
        with boundary._pin_sources():
            pytest.fail("bad pinned identity accepted")
    assert events == [
        ("open", 0),
        ("verify", 0),
        ("open", 1),
        ("verify", 1),
        ("close", 1),
        ("close", 0),
    ]


def test_pinned_fds_remain_owned_until_process_cleanup_returns(monkeypatch):
    events = []
    monkeypatch.setattr(boundary, "verify_sources", lambda *a, **kw: None)
    artifacts = [SimpleNamespace(fd=100 + i) for i in range(len(boundary.SOURCES))]

    @contextmanager
    def pins(*args):
        events.append("pins_open")
        try:
            yield artifacts
        finally:
            events.append("pins_closed")

    monkeypatch.setattr(boundary, "_pin_sources", pins)

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def run(*args, **kwargs):
            entered.set()
            await release.wait()
            events.append("process_cleanup_confirmed")
            raise DeveloperError("process_timeout")

        monkeypatch.setattr(boundary, "run_process", run)
        operation = asyncio.create_task(boundary.run_probe("printf harmless"))
        await entered.wait()
        assert events == ["pins_open"] and not operation.done()
        release.set()
        with pytest.raises(DeveloperError, match="process_timeout"):
            await operation

    asyncio.run(exercise())
    assert events == ["pins_open", "process_cleanup_confirmed", "pins_closed"]


@pytest.mark.parametrize(
    "case,exit_code,stderr",
    [
        ("read_only_guest", 1, b"File size limit exceeded"),
        ("read_only_guest", 0, b""),
        ("process_creation_denied", 1, b"read reached EOF"),
        ("process_creation_denied", 0, b""),
    ],
)
def test_restriction_probes_reject_incidental_failure_or_success(
    tmp_path, monkeypatch, case, exit_code, stderr
):
    calls = []

    async def run(script, **kwargs):
        calls.append(script)
        if script == proof.STATUS:
            return ProcessResult(0, guest_status().encode(), b"", 0.1)
        if "COMPUTE_" in script:
            return ProcessResult(0, b"COMPUTE_42\n", b"", 0.1)
        if "printf CLEAN" in script:
            return ProcessResult(0, b"CLEAN", b"", 0.1)
        if "printf HIDDEN" in script:
            return ProcessResult(0, b"HIDDEN", b"", 0.1)
        if script == ": >/tmp/new-file\n":
            if case == "read_only_guest":
                return ProcessResult(exit_code, b"", stderr, 0.1)
            return ProcessResult(2, b"", b"Read-only file system", 0.1)
        assert script == "printf 'harmless\\n' | read value\n"
        return ProcessResult(exit_code, b"", stderr, 0.1)

    monkeypatch.setattr(proof, "run_probe", run)
    with pytest.raises(RuntimeError, match="shell_probe_restriction_failed"):
        asyncio.run(proof._exercise(tmp_path, {"cases": []}, asyncio.Event(), []))
    assert calls[-1] == (
        ": >/tmp/new-file\n" if case == "read_only_guest" else "printf 'harmless\\n' | read value\n"
    )
