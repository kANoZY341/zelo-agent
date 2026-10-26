"""Stopped-container diagnostic orchestration, with external Docker calls controlled."""

import asyncio
import json
from pathlib import Path

import pytest

from qualification import media_reader_container as container
from qualification import media_reader_image as image
from qualification.admission import AdmissionDenied
from qualification.media_reader_process import QualificationError, ReaderDockerCommandError
from scripts import diagnose_media_native_reader_build as build_diagnostic
from scripts import diagnose_media_reader_stopped as diagnostic


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    (runtime / "media-elf-metadata-admission").mkdir(mode=0o700)
    proofs = runtime / "media-native-reader"
    proofs.mkdir(mode=0o700)
    (proofs / "native-reader-proof-history").mkdir(mode=0o700)
    return runtime


def _external_docker(monkeypatch, *, create_error=None, cleanup=True):
    from scripts import qualify_media_native_reader as reader

    observed = []
    monkeypatch.setattr(reader, "_docker_readiness", lambda runtime: observed.append("ready"))
    monkeypatch.setattr(reader, "_require_empty_lease", lambda kind, identity, env: None)

    def build(attempt, identity, environment, root):
        observed.append("build")
        assert Path(attempt).is_dir()
        assert Path(root).is_dir()
        assert environment["DOCKER_CONFIG"] == str(Path(attempt) / "docker-config")
        contract = container.ReaderContainerContract("sha256:" + "a" * 64, "b" * 64)
        return object(), image.ImageManifest((), "b" * 64), contract, "c" * 64

    monkeypatch.setattr(reader, "_build_and_attest", build)
    monkeypatch.setattr(
        reader,
        "_cleanup_owned",
        lambda identity, environment: observed.append("cleanup_image") or cleanup,
    )

    class FakeHandle:
        def __init__(self, lease, contract, runtime, profile_bytes):
            observed.append("handle")
            assert lease.identity and contract.image_identity.startswith("sha256:")

        async def prepare_stopped(self, payload, deadline):
            observed.append("prepare_stopped")
            if create_error:
                raise create_error
            return "positive"

        async def stop_and_confirm_cleanup(self):
            observed.append("cleanup_container")
            return cleanup

    monkeypatch.setattr(diagnostic, "DockerReaderHandle", FakeHandle)
    return observed


def test_stopped_receipt_is_durable_without_proof_allocation(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    observed = _external_docker(monkeypatch)
    path, receipt = asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    assert json.loads(path.read_text()) == receipt
    assert path.parent == runtime / "media-native-reader-stopped-diagnostics"
    assert receipt["status"] == "verified_stopped"
    assert receipt["reader_started"] is False
    assert receipt["proof_ledger_touched"] is False
    assert receipt["cleanup_confirmed"] is True
    assert receipt["failure"] is None
    assert observed == [
        "ready",
        "build",
        "handle",
        "prepare_stopped",
        "cleanup_container",
        "cleanup_image",
    ]
    assert (runtime / "media-elf-metadata-admission" / "active.marker").read_bytes() == b""
    assert sorted(path.name for path in (runtime / "media-native-reader").iterdir()) == [
        "native-reader-proof-history"
    ]
    assert not list(runtime.glob("native-reader-stopped-*"))


def test_create_refusal_is_bounded_and_cleanup_remains_eligible(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _external_docker(monkeypatch, create_error=ReaderDockerCommandError("create"))
    path, receipt = asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    assert receipt["status"] == "refused"
    assert receipt["failure"] == {
        "stage": "stopped_create",
        "kind": "validation",
        "code": "reader_docker_command",
        "operation": "create",
    }
    assert receipt["cleanup_confirmed"] is True
    assert (runtime / "media-elf-metadata-admission" / "active.marker").read_bytes() == b""
    assert "private" not in path.read_text()


def test_limit_refusal_receipt_identifies_fixed_fields_without_daemon_text(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    try:
        raise container.LimitRefused(
            {"Runtime": "/private/token=secret", "Init": None},
            {"Runtime": "runc", "Init": False},
        )
    except container.LimitRefused as cause:
        error = QualificationError("reader_container_refused")
        error.__cause__ = cause
    _external_docker(monkeypatch, create_error=error)
    path, receipt = asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    assert receipt["failure"] == {
        "stage": "stopped_create",
        "kind": "validation",
        "code": "reader_container_refused",
        "cause_code": "reader_container_limits",
        "limit_differences": [
            {"field": "Runtime", "observed_kind": "other_string"},
            {"field": "Init", "observed_kind": "null"},
        ],
    }
    assert "secret" not in path.read_text() and "/private" not in path.read_text()
    assert receipt["cleanup_confirmed"] is True


def test_uncertain_cleanup_fences_shared_admission_and_persists_receipt(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _external_docker(monkeypatch, cleanup=False)
    path, receipt = asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    assert path.is_file()
    assert receipt["status"] == "refused"
    assert receipt["cleanup_confirmed"] is False
    assert receipt["reader_started"] is False
    assert (runtime / "media-elf-metadata-admission" / "active.marker").stat().st_size > 0
    with pytest.raises(AdmissionDenied, match="runtime_fenced"):
        asyncio.run(diagnostic.run_stopped_diagnostic(runtime))


def test_cancellation_retains_receipt_and_finishes_cleanup(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    observed = _external_docker(monkeypatch)
    entered = None

    class PausedHandle:
        def __init__(self, *args):
            pass

        async def prepare_stopped(self, payload, deadline):
            observed.append("prepare_stopped")
            entered.set()
            await asyncio.Event().wait()

        async def stop_and_confirm_cleanup(self):
            observed.append("cleanup_container")
            return True

    monkeypatch.setattr(diagnostic, "DockerReaderHandle", PausedHandle)

    async def scenario():
        nonlocal entered
        entered = asyncio.Event()
        task = asyncio.create_task(diagnostic.run_stopped_diagnostic(runtime))
        await entered.wait()
        task.cancel()
        return await task

    path, receipt = asyncio.run(scenario())
    assert path.is_file()
    assert receipt["status"] == "refused"
    assert receipt["failure"] == {"stage": "stopped_create", "kind": "cancelled"}
    assert receipt["cleanup_confirmed"] is True
    assert "cleanup_container" in observed and "cleanup_image" in observed
    assert (runtime / "media-elf-metadata-admission" / "active.marker").read_bytes() == b""


def test_late_receipt_readback_failure_does_not_publish_success(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _external_docker(monkeypatch)

    def fail_readback(fd, size):
        raise OSError("simulated readback failure")

    monkeypatch.setattr(build_diagnostic.os, "read", fail_readback)
    with pytest.raises(OSError, match="simulated readback failure"):
        asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    receipt_dir = runtime / diagnostic.RECEIPT_DIRECTORY
    assert not list(receipt_dir.glob("*.json"))
    assert (runtime / "media-elf-metadata-admission" / "active.marker").stat().st_size > 0


def test_repeated_cancellation_during_cleanup_still_cleans_image(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    observed = _external_docker(monkeypatch)
    entered = release = None

    class SlowCleanupHandle:
        def __init__(self, *args):
            pass

        async def prepare_stopped(self, payload, deadline):
            observed.append("prepare_stopped")
            return "positive"

        async def stop_and_confirm_cleanup(self):
            observed.append("cleanup_container")
            entered.set()
            await release.wait()
            return True

    monkeypatch.setattr(diagnostic, "DockerReaderHandle", SlowCleanupHandle)

    async def scenario():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(diagnostic.run_stopped_diagnostic(runtime))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        return await task

    path, receipt = asyncio.run(scenario())
    assert path.is_file()
    assert receipt["status"] == "refused"
    assert receipt["failure"] == {"stage": "cleanup", "kind": "cancelled"}
    assert receipt["cleanup_confirmed"] is True
    assert "cleanup_image" in observed
    assert (runtime / "media-elf-metadata-admission" / "active.marker").read_bytes() == b""


def test_post_publish_fsync_failure_removes_success_receipt(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _external_docker(monkeypatch)
    receipt_dir = runtime / diagnostic.RECEIPT_DIRECTORY
    real_fsync = build_diagnostic.os.fsync

    def fail_after_publish(fd):
        if list(receipt_dir.glob("*.json")):
            raise OSError("simulated publication fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(build_diagnostic.os, "fsync", fail_after_publish)
    with pytest.raises(OSError, match="simulated publication fsync failure"):
        asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    assert not list(receipt_dir.glob("*.json"))
    assert (runtime / "media-elf-metadata-admission" / "active.marker").stat().st_size > 0


def test_publication_directory_open_failure_does_not_leave_success(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _external_docker(monkeypatch)
    receipt_dir = runtime / diagnostic.RECEIPT_DIRECTORY
    real_open = build_diagnostic.os.open
    directory_opens = 0

    def fail_after_publish(path, flags, *args, **kwargs):
        nonlocal directory_opens
        if Path(path) == receipt_dir:
            directory_opens += 1
            if directory_opens == 2:
                raise OSError("simulated directory open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(build_diagnostic.os, "open", fail_after_publish)
    with pytest.raises(OSError, match="simulated directory open failure"):
        asyncio.run(diagnostic.run_stopped_diagnostic(runtime))

    assert not list(receipt_dir.glob("*.json"))
    assert (runtime / "media-elf-metadata-admission" / "active.marker").stat().st_size > 0
