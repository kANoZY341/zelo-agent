import asyncio
import json
import os
import signal
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from qualification.admission import AdmissionController, AdmissionDenied
from qualification.dns import QualificationError
from qualification.resource_owner import ResourceOwner
from qualification.runtime_contracts import ExecutionPolicy, ResourcePolicy

DIGEST = "a" * 64


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


def policy():
    return ExecutionPolicy(
        ResourcePolicy(128 * 1024**2, 64, 100000, 100000, 0, 16 * 1024**2, 2),
        "sha256:" + DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
    )


def test_lease_flood_identity_release_and_fd_cleanup(directory):
    controller = AdmissionController(directory)
    before = len(os.listdir("/proc/self/fd"))
    first = controller.acquire(DIGEST)
    marker = json.loads((directory / "active.marker").read_text())
    assert marker["lease"] == first.identity
    assert marker["policy"] == first.policy_digest == DIGEST
    inode = (directory / "admission.lock").stat().st_ino
    for _ in range(100):
        with pytest.raises(AdmissionDenied):
            AdmissionController(directory).acquire(DIGEST)
    first.finish(cleanup_confirmed=True)
    first.finish(cleanup_confirmed=True)
    second = controller.acquire(DIGEST)
    assert second.identity != first.identity
    assert (directory / "admission.lock").stat().st_ino == inode
    second.finish(cleanup_confirmed=False)
    with pytest.raises(AdmissionDenied, match="fenced"):
        controller.acquire(DIGEST)
    assert len(os.listdir("/proc/self/fd")) == before


CHILD = """
import sys
from pathlib import Path
from qualification.admission import AdmissionController, AdmissionDenied
try:
    lease = AdmissionController(Path(sys.argv[1])).acquire('a' * 64)
except AdmissionDenied:
    print('denied', flush=True)
    sys.exit(0)
print('acquired', flush=True)
sys.stdin.readline()
lease.finish(cleanup_confirmed=True)
"""


def child(directory):
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_independent_process_contention_and_sigkill_fence(directory):
    owner = child(directory)
    try:
        assert owner.stdout.readline().strip() == "acquired"
        competitor = child(directory)
        assert competitor.communicate(timeout=5)[0].strip() == "denied"
        os.kill(owner.pid, signal.SIGKILL)  # Only this test's directly owned child.
        owner.communicate(timeout=5)
        restarted = child(directory)
        assert restarted.communicate(timeout=5)[0].strip() == "denied"
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=5)


def test_independent_process_clean_release(directory):
    for _ in range(2):
        process = child(directory)
        try:
            assert process.stdout.readline().strip() == "acquired"
            assert process.communicate("close\n", timeout=5)[1] == ""
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


@pytest.mark.parametrize("name", ["admission.lock", "active.marker"])
@pytest.mark.parametrize("attack", ["symlink", "hardlink", "permissions", "directory", "fifo"])
def test_file_attacks_deny_without_fd_leak(directory, name, attack):
    target = directory / name
    other = directory / "other"
    other.write_text("preserve")
    other.chmod(0o600)
    if attack == "symlink":
        target.symlink_to(other)
    elif attack == "hardlink":
        os.link(other, target)
    elif attack == "permissions":
        target.touch(mode=0o644)
    elif attack == "directory":
        target.mkdir()
    else:
        os.mkfifo(target, 0o600)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(AdmissionDenied):
        AdmissionController(directory).acquire(DIGEST)
    assert len(os.listdir("/proc/self/fd")) == before
    assert other.read_text() == "preserve"


@pytest.mark.parametrize("attack", ["symlink", "permissions"])
def test_directory_attacks(directory, attack):
    path = directory
    if attack == "symlink":
        path = directory.parent / "alias"
        path.symlink_to(directory)
    else:
        directory.chmod(0o755)
    with pytest.raises(AdmissionDenied):
        AdmissionController(path).acquire(DIGEST)


class Handle:
    def __init__(self):
        self.cleanup_started = asyncio.Event()
        self.cleanup_allowed = asyncio.Event()
        self.calls = 0
        self.confirm = True

    async def stop_and_confirm_cleanup(self):
        self.calls += 1
        self.cleanup_started.set()
        await self.cleanup_allowed.wait()
        return self.confirm


@pytest.mark.parametrize("during_creation", [True, False])
def test_repeated_cancel_delayed_handle_and_retained_cleanup(directory, during_creation):
    async def scenario():
        controller = AdmissionController(directory)
        owner = ResourceOwner(controller, policy(), cleanup_grace=0.02)
        handle = Handle()
        creating = asyncio.Event()
        allow_creation = asyncio.Event()
        working = asyncio.Event()

        async def factory(lease, bound):
            assert (directory / "active.marker").stat().st_size > 0
            assert lease.policy_digest == bound.digest
            creating.set()
            await allow_creation.wait()
            return handle

        async def work(resource):
            working.set()
            await asyncio.Event().wait()
            return "forbidden"

        task = asyncio.create_task(owner.run(factory, work))
        await creating.wait()
        if not during_creation:
            allow_creation.set()
            await working.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        retained = owner._lifecycle
        assert not retained.done()
        with pytest.raises(AdmissionDenied):
            controller.acquire(DIGEST)
        allow_creation.set()
        await handle.cleanup_started.wait()
        results = await asyncio.gather(owner.aclose(), owner.aclose(), return_exceptions=True)
        assert all(isinstance(item, QualificationError) for item in results)
        assert owner._lifecycle is retained
        assert handle.calls == 1
        handle.cleanup_allowed.set()
        await owner.aclose()
        lease = controller.acquire(DIGEST)
        lease.finish(cleanup_confirmed=True)
        with pytest.raises(QualificationError, match="terminal"):
            await owner.run(factory, work)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["create", "cleanup_false", "cleanup_raise", "work"])
def test_failure_fencing_and_work_failure_cleanup(directory, failure):
    async def scenario():
        controller = AdmissionController(directory)
        owner = ResourceOwner(controller, policy())

        class Fixture:
            async def stop_and_confirm_cleanup(self):
                if failure == "cleanup_raise":
                    raise RuntimeError("cleanup failed")
                return failure != "cleanup_false"

        async def factory(*args):
            if failure == "create":
                raise RuntimeError("handle unknown")
            return Fixture()

        async def work(handle):
            if failure == "work":
                raise RuntimeError("work failed")
            return "tentative"

        with pytest.raises(QualificationError):
            await owner.run(factory, work)
        if failure == "work":
            controller.acquire(DIGEST).finish(cleanup_confirmed=True)
        else:
            with pytest.raises(AdmissionDenied, match="fenced"):
                AdmissionController(directory).acquire(DIGEST)

    asyncio.run(scenario())


def test_success_is_tentative_and_concurrent_close_cannot_publish(directory):
    async def scenario():
        owner = ResourceOwner(AdmissionController(directory), policy())
        handle = Handle()

        async def factory(*args):
            return handle

        async def work(resource):
            return "evidence"

        task = asyncio.create_task(owner.run(factory, work))
        await handle.cleanup_started.wait()
        assert not task.done()
        closers = [asyncio.create_task(owner.aclose()) for _ in range(5)]
        await asyncio.sleep(0)
        handle.cleanup_allowed.set()
        await asyncio.gather(*closers)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert handle.calls == 1

    asyncio.run(scenario())


def test_success_and_negative_admission_never_invokes_factory(directory):
    async def scenario():
        calls = 0
        handle = Handle()
        handle.cleanup_allowed.set()

        async def factory(*args):
            nonlocal calls
            calls += 1
            return handle

        async def work(resource):
            return "evidence"

        controller = AdmissionController(directory)
        lease = controller.acquire(DIGEST)
        for _ in range(20):
            with pytest.raises(AdmissionDenied):
                await ResourceOwner(controller, policy()).run(factory, work)
        assert calls == 0
        lease.finish(cleanup_confirmed=True)
        assert await ResourceOwner(controller, policy()).run(factory, work) == "evidence"
        assert calls == 1
        lease = controller.acquire(DIGEST)
        lease.finish(cleanup_confirmed=False)
        with pytest.raises(AdmissionDenied):
            await ResourceOwner(controller, policy()).run(factory, work)
        assert calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [True, -1, 0, float("nan"), float("inf"), 10**20, 10**1000])
def test_resource_bounds(value):
    base = policy().resources
    for item in fields(base):
        if item.name == "swap_bytes" and value == 0 and type(value) is int:
            continue
        with pytest.raises(ValueError):
            replace(base, **{item.name: value})


def test_every_configurable_field_changes_identity():
    base = policy()
    for item in fields(base):
        if not item.init:
            continue
        if item.name == "resources":
            changes = {
                "memory_bytes": 256 * 1024**2,
                "pids": 63,
                "cpu_quota_us": 99000,
                "cpu_period_us": 99000,
                "tmpfs_bytes": 1024,
                "deadline_seconds": 1,
            }
            for key, value in changes.items():
                assert (
                    replace(base, resources=replace(base.resources, **{key: value})).digest
                    != base.digest
                )
        else:
            value = ("sha256:" if item.name == "image_identity" else "") + "b" * 64
            assert replace(base, **{item.name: value}).digest != base.digest
    assert (
        replace(base, resources=replace(base.resources, deadline_seconds=2.0)).digest == base.digest
    )
    with pytest.raises(FrozenInstanceError):
        base.no_network = False
    with pytest.raises(ValueError):
        replace(base, no_network=False)


@pytest.mark.parametrize("value", ["latest", "sha256:abc", "A" * 64, True, "g" * 64])
def test_bad_identities(directory, value):
    with pytest.raises(ValueError):
        AdmissionController(directory).acquire(value)
    for name in (
        "image_identity",
        "browser_digest",
        "controller_digest",
        "broker_digest",
        "renderer_policy_digest",
        "approved_origins_digest",
    ):
        with pytest.raises(ValueError):
            replace(policy(), **{name: value})
    assert list(directory.iterdir()) == []


def test_deadline_during_creation_retains_fence_until_late_cleanup(directory):
    async def scenario():
        bound = policy()
        bound = replace(bound, resources=replace(bound.resources, deadline_seconds=0.01))
        controller = AdmissionController(directory)
        owner = ResourceOwner(controller, bound, cleanup_grace=0.01)
        handle = Handle()
        allow_creation = asyncio.Event()
        worked = False

        async def factory(*args):
            await allow_creation.wait()
            return handle

        async def work(resource):
            nonlocal worked
            worked = True

        with pytest.raises(QualificationError, match="pending"):
            await owner.run(factory, work)
        with pytest.raises(AdmissionDenied):
            controller.acquire(DIGEST)
        allow_creation.set()
        await handle.cleanup_started.wait()
        handle.cleanup_allowed.set()
        await owner.aclose()
        assert not worked
        controller.acquire(DIGEST).finish(cleanup_confirmed=True)

    asyncio.run(scenario())


def test_failure_while_concurrent_close_preserves_restart_fence(directory):
    async def scenario():
        owner = ResourceOwner(AdmissionController(directory), policy())
        handle = Handle()
        handle.confirm = False

        async def factory(*args):
            return handle

        async def work(resource):
            return "tentative"

        task = asyncio.create_task(owner.run(factory, work))
        await handle.cleanup_started.wait()
        closers = [asyncio.create_task(owner.aclose()) for _ in range(3)]
        await asyncio.sleep(0)
        handle.cleanup_allowed.set()
        results = await asyncio.gather(task, *closers, return_exceptions=True)
        assert all(isinstance(result, QualificationError) for result in results)
        assert handle.calls == 1
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(directory).acquire(DIGEST)

    asyncio.run(scenario())


def test_cancelled_factory_is_not_cleanup_proof(directory):
    async def scenario():
        owner = ResourceOwner(AdmissionController(directory), policy())

        async def factory(*args):
            raise asyncio.CancelledError

        async def work(resource):
            pytest.fail("work invoked")

        with pytest.raises(QualificationError, match="unproven"):
            await owner.run(factory, work)
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(directory).acquire(DIGEST)

    asyncio.run(scenario())


@pytest.mark.parametrize("attack", ["lock_symlink", "marker_mode", "directory_mode"])
def test_unsafe_admission_never_calls_factory(directory, attack):
    if attack == "lock_symlink":
        (directory / "admission.lock").symlink_to(directory / "absent")
    elif attack == "marker_mode":
        (directory / "active.marker").touch(mode=0o644)
    else:
        directory.chmod(0o755)

    async def scenario():
        async def forbidden(*args):
            pytest.fail("factory/work invoked after denied admission")

        with pytest.raises(AdmissionDenied):
            await ResourceOwner(AdmissionController(directory), policy()).run(forbidden, forbidden)

    asyncio.run(scenario())


def test_marker_sync_failure_prevents_creation_and_leaves_fence(directory, monkeypatch):
    def fail_sync(fd):
        raise OSError("fixture sync failure")

    async def scenario():
        async def forbidden(*args):
            pytest.fail("factory/work invoked before durable marker")

        before = len(os.listdir("/proc/self/fd"))
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fail_sync)
            with pytest.raises(AdmissionDenied):
                await ResourceOwner(AdmissionController(directory), policy()).run(
                    forbidden, forbidden
                )
        assert len(os.listdir("/proc/self/fd")) == before
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(directory).acquire(DIGEST)

    asyncio.run(scenario())
