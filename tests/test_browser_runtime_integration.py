"""Admission/owner integration with an actual controlled child; no browser or network."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from qualification.admission import AdmissionController, AdmissionDenied
from qualification.resource_owner import ResourceOwner
from qualification.runtime_contracts import ExecutionPolicy, ResourcePolicy


@pytest.mark.parametrize("during_creation", [True, False])
def test_cancel_retains_slot_until_actual_child_is_reaped(tmp_path: Path, during_creation: bool):
    async def scenario():
        directory = tmp_path / "admission"
        directory.mkdir(mode=0o700)
        admission = AdmissionController(directory)
        digest = "a" * 64
        policy = ExecutionPolicy(
            ResourcePolicy(128 * 1024**2, 64, 100000, 100000, 0, 1024**2, 2),
            "sha256:" + digest,
            digest,
            digest,
            digest,
            digest,
            digest,
        )
        owner = ResourceOwner(admission, policy, cleanup_grace=0.02)
        created, allow_handle, working = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cleaning, allow_cleanup = asyncio.Event(), asyncio.Event()
        child = None
        cleanup_calls = 0

        class Handle:
            async def stop_and_confirm_cleanup(self):
                nonlocal cleanup_calls
                cleanup_calls += 1
                cleaning.set()
                await allow_cleanup.wait()
                if child.returncode is None:
                    child.kill()
                await child.wait()
                return child.returncode is not None

        async def factory(lease, bound):
            nonlocal child
            assert bound.digest == lease.policy_digest
            assert (directory / "active.marker").stat().st_size > 0
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import time; time.sleep(10)",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
                close_fds=True,
            )
            created.set()
            await allow_handle.wait()
            return Handle()

        async def work(handle):
            working.set()
            await asyncio.Event().wait()
            return "must never publish"

        run = asyncio.create_task(owner.run(factory, work))
        try:
            await asyncio.wait_for(created.wait(), 3)
            if not during_creation:
                allow_handle.set()
                await asyncio.wait_for(working.wait(), 1)
            run.cancel()
            await asyncio.sleep(0)
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
            assert child.returncode is None
            with pytest.raises(AdmissionDenied):
                AdmissionController(directory).acquire(digest)
            allow_handle.set()
            await asyncio.wait_for(cleaning.wait(), 1)
            with pytest.raises(AdmissionDenied):
                admission.acquire(digest)
            allow_cleanup.set()
            await owner.aclose()
            assert cleanup_calls == 1 and child.returncode is not None
            with pytest.raises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
            admission.acquire(digest).finish(cleanup_confirmed=True)
        finally:
            allow_handle.set()
            allow_cleanup.set()
            if child is not None and child.returncode is None:
                child.kill()
                await child.wait()
            if not run.done():
                run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            await asyncio.gather(owner.aclose(), return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["ftruncate", "fsync"])
def test_release_failure_propagates_and_preserves_safe_admission(tmp_path, monkeypatch, operation):
    from qualification import admission as module

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    controller = AdmissionController(directory)
    lease = controller.acquire("a" * 64)
    before = len(os.listdir("/proc/self/fd"))

    def failed(*args):
        raise OSError("controlled release failure")

    with monkeypatch.context() as context:
        context.setattr(module.os, operation, failed)
        with pytest.raises(OSError):
            lease.finish(cleanup_confirmed=True)
    assert len(os.listdir("/proc/self/fd")) == before - 3
    if operation == "ftruncate":
        with pytest.raises(AdmissionDenied, match="fenced"):
            controller.acquire("a" * 64)
    else:
        # Cleanup was already explicitly confirmed before marker truncation.
        # Disk sync failure propagates; it cannot publish successful work.
        controller.acquire("a" * 64).finish(cleanup_confirmed=True)
