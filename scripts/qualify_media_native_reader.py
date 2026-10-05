#!/usr/bin/env python3
"""One-shot synthetic native Media reader qualification; explicit --run only."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import resource
import shutil
import stat
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _operator_limits():
    for kind, bound in (
        (resource.RLIMIT_AS, 1073741824),
        (resource.RLIMIT_CPU, 120),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_FSIZE, 536870912),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if any(value != resource.RLIM_INFINITY and value < bound for value in (soft, hard)):
            raise ValueError("reader_native_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("reader_native_optimized_python_refused")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification import media_reader_cgroup as cgroup  # noqa: E402
from qualification import media_reader_container as container  # noqa: E402
from qualification import media_reader_fixture as fixture  # noqa: E402
from qualification import media_reader_image as image  # noqa: E402
from qualification import media_reader_reconcile as reconcile  # noqa: E402
from qualification import media_reader_seccomp as seccomp  # noqa: E402
from qualification import media_reader_setup as setup  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from qualification.dns import QualificationError  # noqa: E402
from qualification.media_reader_process import DockerReaderHandle  # noqa: E402
from scripts import qualify_browser_renderer as browser_setup  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_elf_version_metadata import _release, _State, _Store  # noqa: E402

ADMISSION = "runtime/media-elf-metadata-admission"
PROOF_BASE = "runtime/media-native-reader"
PREFIX = "native-reader-proof-"
MAX_ATTEMPTS = 2
WHOLE_SECONDS = 240
CASE_SECONDS = 20
BUILD_SECONDS = 90
DOCKER_QUIET_SECONDS = 2
EXPORT_BYTES = browser_setup.MAX_EXPORT_BYTES
_RETAINED = []


def _deadline(value):
    if time.monotonic() >= value:
        raise ValueError("reader_native_deadline")


def policy():
    return {
        "schema": "zelo-media-native-reader-synthetic-policy-v1",
        "bootstrap_proof_sha256": container.BOOTSTRAP_PROOF_SHA256,
        "bootstrap_lock_sha256": container.BOOTSTRAP_LOCK_SHA256,
        "seccomp_sha256": seccomp.SHA256,
        "fixture_cases": list(fixture.CASES),
        "terminal_controls": ["stdout_limit", "timeout", "cancel", "cancel_repeated"],
        "stdin_bytes": fixture.STDIN_BYTES,
        "stdout_bytes": fixture.STDOUT_BYTES,
        "stderr_bytes": fixture.STDERR_BYTES,
        "memory_bytes": container.MEMORY,
        "memory_swap_bytes": container.MEMORY,
        "pids_limit": container.PIDS,
        "cpu_period": container.CPU_PERIOD,
        "cpu_quota": container.CPU_QUOTA,
        "nofile": container.NOFILE,
        "network": "none",
        "read_only_rootfs": True,
        "attempt_limit": MAX_ATTEMPTS,
        "proof_scope": PROOF_BASE + "/" + PREFIX + "*",
        "whole_seconds": WHOLE_SECONDS,
        "case_seconds": CASE_SECONDS,
        "build_seconds": BUILD_SECONDS,
        "export_bytes": EXPORT_BYTES,
        "aggregate_cgroup_required": True,
        "docker_server_readiness_required_before_admission": True,
        "docker_quiet_seconds_before_admission": DOCKER_QUIET_SECONDS,
        "actual_package_provenance": False,
        "image_decoder_authority": False,
        "runtime_authority": False,
    }


def _attempt_directory():
    base = ROOT / PROOF_BASE
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    with os.scandir(base) as attempts:
        entries = list(attempts)
    if len(entries) >= MAX_ATTEMPTS:
        raise ValueError("reader_native_attempt_limit")
    for entry in entries:
        path = base / entry.name
        shared._private_path(path, directory=True)
        if not entry.name.startswith(PREFIX) or stat.S_IMODE(path.lstat().st_mode) != 0o700:
            raise ValueError("reader_native_old_attempt")
        entries = sorted(os.scandir(path), key=lambda item: item.name)
        names = [item.name for item in entries]
        historical = entry.name == reconcile.ATTEMPT_NAME
        expected = (
            ["cases.json", "docker-config", "policy.json"]
            if historical
            else ["cases.json", "policy.json"]
        )
        if names != expected:
            raise ValueError("reader_native_old_entry")
        retained = 0
        for item in entries:
            info = item.stat(follow_symlinks=False)
            if item.name == "docker-config":
                if (
                    not historical
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_gid != os.getegid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                    or list(os.scandir(item.path))
                ):
                    raise ValueError("reader_native_old_entry")
                continue
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 0 < info.st_size <= 2097152
            ):
                raise ValueError("reader_native_old_entry")
            retained += info.st_size
            if retained > 4194304:
                raise ValueError("reader_native_old_bytes")
        if historical:
            if (
                hashlib.sha256(_read_exact(path / "policy.json", 2097152)).hexdigest()
                != reconcile.POLICY_SHA256
                or hashlib.sha256(_read_exact(path / "cases.json", 2097152)).hexdigest()
                != reconcile.CASES_SHA256
            ):
                raise ValueError("reader_native_old_entry")
    path = Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))
    os.chmod(path, 0o700)
    return path


def _read_exact(path, bound, *, digest=None, modes=(0o600,)):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) not in modes
            or before.st_nlink != 1
            or not 0 < before.st_size <= bound
        ):
            raise ValueError("reader_native_input_identity")
        raw = bytearray()
        while True:
            chunk = os.read(fd, min(65536, bound - len(raw) + 1))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > bound:
                raise ValueError("reader_native_input_bound")
        after = os.fstat(fd)
        if (
            identity
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or len(raw) != before.st_size
        ):
            raise ValueError("reader_native_input_drift")
        value = bytes(raw)
        if digest is not None and hashlib.sha256(value).hexdigest() != digest:
            raise ValueError("reader_native_input_hash")
        return value
    finally:
        os.close(fd)


def _copy_context(attempt, lease_identity):
    lock_raw = _read_exact(
        ROOT / "qualification/fixtures/media-reader-elf-target-lock.json",
        16384,
        digest=container.BOOTSTRAP_LOCK_SHA256,
        modes=(0o600, 0o644),
    )
    lock = image.load_lock(lock_raw)
    manifest = image.manifest(lock)
    build = attempt / "build"
    build.mkdir(mode=0o700)
    value = setup.plan(attempt.resolve(), lease_identity, manifest)
    if value.context != build:
        raise ValueError("reader_native_build_path")
    sources = ROOT / "runtime/media-package-reader-bootstrap-20261004"
    total = 0
    for row in manifest.files:
        raw = _read_exact(sources / row.source, 4194304, digest=row.sha256)
        total += len(raw)
        target = build / row.source
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
                    raise ValueError("reader_native_context_write")
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _read_exact(target, 4194304, digest=row.sha256)
    if total != sum(row.bytes for row in manifest.files):
        raise ValueError("reader_native_context_bytes")
    dockerfile = image.dockerfile(manifest)
    value.dockerfile.write_bytes(dockerfile)
    value.dockerfile.chmod(0o600)
    return value, manifest


def _docker(argv, environment, timeout=20):
    if type(argv) not in (tuple, list) or not argv or argv[0] != setup.DOCKER:
        raise ValueError("reader_native_docker_command")
    return browser_setup._command(list(argv), timeout, environment).encode()


def _docker_readiness():
    runtime = ROOT / "runtime"
    info = runtime.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or info.st_mode & 0o022
    ):
        raise ValueError("reader_native_readiness_runtime")
    with tempfile.TemporaryDirectory(prefix="native-reader-readiness-", dir=runtime) as raw_dir:
        config = Path(raw_dir)
        os.chmod(config, 0o700)
        environment = {
            "DOCKER_CONFIG": str(config),
            "HOME": "/nonexistent",
            "LANG": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        }
        raw = _docker(
            (
                setup.DOCKER,
                "--host=unix:///var/run/docker.sock",
                "version",
                "--format",
                "{{json .Server}}",
            ),
            environment,
            10,
        )
        if len(raw) > 16384:
            raise ValueError("reader_native_readiness_output")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError, TypeError):
            raise ValueError("reader_native_readiness_json") from None
        if (
            type(value) is not dict
            or value.get("Os") != "linux"
            or value.get("Arch") != "amd64"
            or type(value.get("Version")) is not str
            or re.fullmatch(r"[0-9A-Za-z.+~_-]{1,128}", value["Version"]) is None
        ):
            raise ValueError("reader_native_readiness_identity")
        components = value.get("Components")
        if type(components) is not list or not 1 <= len(components) <= 16:
            raise ValueError("reader_native_readiness_components")
        runc = [item for item in components if type(item) is dict and item.get("Name") == "runc"]
        if (
            len(runc) != 1
            or type(runc[0].get("Version")) is not str
            or re.fullmatch(r"[0-9A-Za-z.+~_-]{1,128}", runc[0]["Version"]) is None
        ):
            raise ValueError("reader_native_readiness_runc")
        start = datetime.now(UTC).replace(microsecond=0)
        end = start + timedelta(seconds=DOCKER_QUIET_SECONDS)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        events = _docker(
            (
                setup.DOCKER,
                "--host=unix:///var/run/docker.sock",
                "events",
                "--since",
                start.strftime(fmt),
                "--until",
                end.strftime(fmt),
                "--format",
                "{{json .}}",
            ),
            environment,
            DOCKER_QUIET_SECONDS + 4,
        )
        if events != b"":
            raise ValueError("reader_native_daemon_busy")
        if list(os.scandir(config)):
            raise ValueError("reader_native_readiness_config")
        return {
            "schema": "zelo-media-reader-docker-readiness-v1",
            "server_os": "linux",
            "server_arch": "amd64",
            "server_version": value["Version"],
            "runc_version": runc[0]["Version"],
            "quiet_window_seconds": DOCKER_QUIET_SECONDS,
            "daemon_events_observed": 0,
            "mutating_command": False,
            "runtime_authority": False,
        }


def _inspect(kind, identity, environment):
    raw = _docker(
        (
            setup.DOCKER,
            "--host=unix:///var/run/docker.sock",
            kind,
            "inspect",
            identity,
        ),
        environment,
    )
    value = json.loads(raw)
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        raise ValueError("reader_native_inspect")
    return value[0]


def _discover(kind, identity, environment):
    return setup.parse_discovery(
        kind, _docker(setup.discovery_arguments(kind, identity), environment)
    )


def _require_empty_lease(kind, identity, environment):
    if kind not in ("container", "image"):
        raise ValueError("reader_native_lease_kind")
    if _discover(kind, identity, environment):
        raise ValueError("reader_native_lease_collision")


def _cleanup_owned(identity, environment):
    confirmed = True
    try:
        containers = _discover("container", identity, environment)
    except Exception:
        containers = ()
        confirmed = False
    for cid in containers:
        try:
            data = _inspect("container", cid, environment)
            labels = data.get("Config", {}).get("Labels", {})
            if labels.get(container.LEASE_LABEL) != identity:
                raise ValueError("reader_native_cleanup_identity")
            _docker(
                (
                    setup.DOCKER,
                    "--host=unix:///var/run/docker.sock",
                    "container",
                    "rm",
                    "--force",
                    cid,
                ),
                environment,
            )
        except Exception:
            confirmed = False
    try:
        if _discover("container", identity, environment):
            confirmed = False
    except Exception:
        confirmed = False

    try:
        images = _discover("image", identity, environment)
    except Exception:
        images = ()
        confirmed = False
    for iid in images:
        try:
            data = _inspect("image", iid, environment)
            labels = data.get("Config", {}).get("Labels", {})
            if labels.get(container.LEASE_LABEL) != identity:
                raise ValueError("reader_native_cleanup_identity")
            _docker(
                (
                    setup.DOCKER,
                    "--host=unix:///var/run/docker.sock",
                    "image",
                    "rm",
                    iid,
                ),
                environment,
            )
        except Exception:
            confirmed = False
    try:
        if _discover("image", identity, environment):
            confirmed = False
    except Exception:
        confirmed = False
    return confirmed


def _build_and_attest(attempt, lease_identity, environment):
    value, manifest = _copy_context(attempt, lease_identity)
    _docker(setup.build_arguments(value), environment, BUILD_SECONDS)
    raw_id = _read_exact(value.iidfile, 256, modes=(0o600, 0o644))
    try:
        image_identity = raw_id.decode("ascii", "strict").strip()
    except UnicodeError:
        raise ValueError("reader_native_image_id") from None
    contract = image.contract(image_identity, manifest)
    setup.verify_owned_image(
        _inspect("image", image_identity, environment), contract, lease_identity
    )

    cid_raw = _docker(setup.setup_create_arguments(value, image_identity), environment)
    try:
        cid = cid_raw.decode("ascii", "strict").strip()
    except UnicodeError:
        raise ValueError("reader_native_setup_id") from None
    if setup.parse_discovery("container", (cid + "\n").encode()) != (cid,):
        raise ValueError("reader_native_setup_id")
    setup.verify_owned_setup_container(
        _inspect("container", cid, environment), value, image_identity
    )
    archive = attempt / "image-rootfs.tar"
    browser_setup.export_bounded(setup.DOCKER, cid, archive)
    with archive.open("rb") as stream:
        payload_digest = browser_setup.verify_image_archive(stream, setup.expected_export(manifest))
    _docker(
        (
            setup.DOCKER,
            "--host=unix:///var/run/docker.sock",
            "container",
            "rm",
            cid,
        ),
        environment,
    )
    if _discover("container", lease_identity, environment):
        raise ValueError("reader_native_setup_remaining")
    for row in manifest.files:
        _read_exact(value.context / row.source, 4194304, digest=row.sha256)
    setup.verify_owned_image(
        _inspect("image", image_identity, environment), contract, lease_identity
    )
    return value, manifest, contract, payload_digest


async def _run_case(lease, contract_value, attempt, case, *, probe=None):
    handle = DockerReaderHandle(
        lease,
        contract_value,
        attempt,
        seccomp.profile_bytes(),
        runtime_probe=probe,
    )
    result = None
    cleanup = False
    try:
        result = await handle.stream(
            fixture.fixture(case),
            asyncio.get_running_loop().time() + CASE_SECONDS,
        )
    finally:
        cleanup = await handle.stop_and_confirm_cleanup()
    if result is None:
        raise ValueError("reader_native_case_missing")
    return fixture.validate_result(
        case,
        result.stdout,
        result.stderr,
        result.exit_code,
        cleanup,
    )


async def _terminal_control(lease, contract_value, attempt, kind):
    if kind not in ("stdout_limit", "timeout", "cancel", "cancel_repeated"):
        raise ValueError("reader_native_control")
    ready = asyncio.Event()

    async def probe(data):
        pid = data.get("State", {}).get("Pid")
        if type(pid) is not int or pid <= 1:
            raise ValueError("reader_native_runtime_pid")
        ready.set()
        if kind in ("timeout", "cancel", "cancel_repeated"):
            await asyncio.sleep(30)

    handle = DockerReaderHandle(
        lease,
        contract_value,
        attempt,
        seccomp.profile_bytes(),
        runtime_probe=probe,
        stdout_limit=1024 if kind == "stdout_limit" else fixture.STDOUT_BYTES,
    )
    deadline = asyncio.get_running_loop().time() + (1 if kind == "timeout" else CASE_SECONDS)
    task = asyncio.create_task(handle.stream(fixture.fixture("positive"), deadline))
    error = None
    try:
        if kind in ("cancel", "cancel_repeated"):
            await asyncio.wait_for(ready.wait(), 5)
            task.cancel()
            if kind == "cancel_repeated":
                await asyncio.sleep(0)
                task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if kind not in ("cancel", "cancel_repeated"):
                raise
            error = "cancelled"
        except TimeoutError:
            if kind != "timeout":
                raise
            error = "timeout"
        except QualificationError as exc:
            if kind != "stdout_limit" or str(exc) != "process_output_limit":
                raise
            error = "output-limit"
        else:
            raise ValueError("reader_native_control_not_triggered")
    finally:
        cleanup = await handle.stop_and_confirm_cleanup()
    if cleanup is not True or error is None:
        raise ValueError("reader_native_control_cleanup")
    return {
        "case": kind,
        "status": "controlled-refusal",
        "error_family": error,
        "cleanup_confirmed": True,
        "runtime_authority": False,
    }


async def _matrix(lease, contract_value, attempt):
    results = []
    cgroup_evidence = None

    async def probe(data):
        nonlocal cgroup_evidence
        pid = data.get("State", {}).get("Pid")
        if type(pid) is not int or pid <= 1:
            raise ValueError("reader_native_runtime_pid")
        cgroup_evidence = cgroup.snapshot(pid)

    for case in fixture.CASES:
        results.append(
            await _run_case(
                lease,
                contract_value,
                attempt,
                case,
                probe=probe if case == "positive" else None,
            )
        )
    for kind in ("stdout_limit", "timeout", "cancel", "cancel_repeated"):
        results.append(await _terminal_control(lease, contract_value, attempt, kind))
    if cgroup_evidence is None or cgroup_evidence.get("aggregate_cgroup_verified") is not True:
        raise ValueError("reader_native_cgroup_missing")
    return results, cgroup_evidence


async def _qualify():
    started = time.monotonic()
    deadline = started + WHOLE_SECONDS
    readiness = _docker_readiness()
    _deadline(deadline)
    admission = ROOT / ADMISSION
    admission.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission, directory=True)
    spec = policy()
    digest = shared._digest(spec)
    lease = AdmissionController(admission).acquire(digest)
    state = _State()
    store = None
    environment = None
    cleanup_confirmed = False
    report_attempted = False
    stage = "attempt"
    try:
        attempt = _attempt_directory()
        store = _Store(attempt, state)
        store.json("policy.json", spec)
        docker_config = attempt / "docker-config"
        docker_config.mkdir(mode=0o700)
        environment = {"DOCKER_CONFIG": str(docker_config)}
        stage = "lease-preflight-container"
        _require_empty_lease("container", lease.identity, environment)
        stage = "lease-preflight-image"
        _require_empty_lease("image", lease.identity, environment)
        stage = "build"
        plan, manifest, contract_value, image_payload_digest = _build_and_attest(
            attempt, lease.identity, environment
        )
        _deadline(deadline)
        stage = "matrix"
        results, cgroup_evidence = await _matrix(lease, contract_value, attempt)
        _deadline(deadline)
        stage = "final-image"
        setup.verify_owned_image(
            _inspect("image", contract_value.image_identity, environment),
            contract_value,
            lease.identity,
        )
        report_attempted = True
        report = {
            "schema": "zelo-media-native-reader-synthetic-proof-v1",
            "status": "cases-passed",
            "policy_digest": digest,
            "docker_readiness": readiness,
            "lease_identity": lease.identity,
            "image_identity": contract_value.image_identity,
            "bundle_sha256": manifest.digest,
            "image_payload_digest": image_payload_digest,
            "cases": results,
            "cgroup": cgroup_evidence,
            "support_release_confirmed": False,
            "aggregate_cgroup_verified": True,
            "actual_package_provenance": False,
            "image_decoder_authority": False,
            "runtime_authority": False,
        }
        raw = store.json("cases.json", report)
        proof_path = str(attempt / "cases.json")
        proof_sha = hashlib.sha256(raw).hexdigest()
    except Exception:
        if store is not None and not report_attempted and not (store.path / "cases.json").exists():
            store.json(
                "cases.json",
                {
                    "status": "refused",
                    "stage": stage,
                    "support_release_confirmed": False,
                    "runtime_authority": False,
                },
            )
        raise
    finally:
        error_type = sys.exc_info()[0]
        if environment is not None:
            try:
                cleanup_confirmed = _cleanup_owned(lease.identity, environment)
            except BaseException:
                cleanup_confirmed = False
        if cleanup_confirmed:
            try:
                shutil.rmtree(attempt / "docker-config", ignore_errors=False)
                shutil.rmtree(attempt / "build", ignore_errors=False)
                (attempt / "image-rootfs.tar").unlink(missing_ok=True)
                (attempt / "image-id").unlink(missing_ok=True)
            except OSError:
                cleanup_confirmed = False
        if error_type is not None and not issubclass(error_type, Exception):
            _RETAINED.append((state, lease))
        else:
            if cleanup_confirmed:
                try:
                    if _release(state, lease) is not True:
                        cleanup_confirmed = False
                except BaseException:
                    _RETAINED.append((state, lease))
                    raise
            else:
                state.close_ok = False
                try:
                    _release(state, lease)
                except BaseException:
                    _RETAINED.append((state, lease))
    if cleanup_confirmed is not True:
        raise ValueError("reader_native_cleanup_unconfirmed")
    _deadline(deadline)
    return {
        "status": "qualified-synthetic-native-reader",
        "proof": proof_path,
        "proof_sha256": proof_sha,
        "policy_digest": digest,
        "cases": len(results),
        "image_identity": contract_value.image_identity,
        "aggregate_cgroup_verified": True,
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
        print("reader_native_refused", file=sys.stderr)
        return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
