"""Offline ownership fixtures launch no optional native Shell guests."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from app import developer_process
from app.developer_process import DeveloperError, ProcessResult
from qualification import shell_compute, shell_owner
from qualification.admission import AdmissionDenied
from qualification.dns import QualificationError


@pytest.fixture(autouse=True)
def offline_operator_identity(monkeypatch):
    import os

    from qualification import shell_compute as boundary

    if os.geteuid() == 0:
        real_os = boundary.os

        class OperatorOs:
            def geteuid(self):
                return 1000

            def getegid(self):
                return 1000

            def __getattr__(self, name):
                return getattr(real_os, name)

        # Only proof identity is synthetic. Admission checks real file ownership;
        # no optional guest may be launched in these offline tests.
        monkeypatch.setattr(boundary, "os", OperatorOs())


@pytest.fixture(autouse=True)
def no_guest(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("offline Shell ownership launched guest")

    monkeypatch.setattr(developer_process, "spawn_owned_process", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    # Keep real filesystem UID: the fixtures require private directory ownership.


def identity():
    return shell_compute.ShellIdentity.current()


def test_identity_is_frozen_and_distinct_from_browser():
    value = identity()
    with pytest.raises(FrozenInstanceError):
        value.uid = 0
    assert value.schema == "zelo-shell-owner-qualification-v1"
    assert len(value.digest) == 64 and isinstance(value.argv, tuple)
    assert "--nproc=0:0" in value.argv and "--unshare-net" in value.argv
    assert all(isinstance(source, tuple) for source in value.sources)
    assert "browser" not in str(value)


@pytest.mark.parametrize("uid,gid", [(True, 1), (0, 1), (-1, 1), (1, True), (1, -1)])
def test_identity_rejects_invalid_operator_types(uid, gid):
    with pytest.raises(DeveloperError):
        shell_compute.ShellIdentity(uid, gid)


def test_identity_executes_bound_snapshot_not_mutated_globals(monkeypatch):
    value = identity()
    digest, argv = value.digest, value.argv
    monkeypatch.setattr(shell_compute, "SOURCES", (("/host/custom", "0" * 64, "/bin/sh"),))
    monkeypatch.setattr(shell_compute, "ENVIRONMENT", {"TOKEN": "unsafe"})
    assert value.digest == digest and value.argv == argv
    assert shell_compute.ShellIdentity.current() == value


def test_tampered_identity_rejected_before_admission(tmp_path, monkeypatch):
    value = identity()
    object.__setattr__(value, "argv", ("/bin/sh",))
    monkeypatch.setattr(shell_owner, "AdmissionController", lambda *a: pytest.fail("admission"))
    with pytest.raises(DeveloperError, match="shell_identity_invalid"):
        shell_owner.ShellOwner(tmp_path, value)


def private(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path


def acknowledged(resources):
    resources.native._claim()
    resources.native._no_child()  # Trusted resource-free offline backend.
    resources._terminal = True


def test_durable_busy_before_work_and_readmission_after_explicit_cleanup(tmp_path, monkeypatch):
    directory = private(tmp_path)

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def work(script, *, identity, deadline, resources):
            calls.append(script)
            assert deadline > asyncio.get_running_loop().time()
            entered.set()
            await release.wait()
            acknowledged(resources)
            return ProcessResult(0, b"fixed", b"", 0.1)

        monkeypatch.setattr(shell_owner, "run_probe", work)
        first = shell_owner.ShellOwner(directory, identity())
        operation = asyncio.create_task(first.run_probe("fixed"))
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(AdmissionDenied):
            await shell_owner.ShellOwner(directory, identity()).run_probe("contender")
        assert calls == ["fixed"] and not first.cleanup_confirmed
        release.set()
        assert (await operation).stdout == b"fixed" and first.cleanup_confirmed
        assert (directory / "active.marker").stat().st_size == 0
        fresh = shell_owner.ShellOwner(directory, identity())
        assert (await fresh.run_probe("fresh")).stdout == b"fixed"
        with pytest.raises(QualificationError, match="owner_terminal"):
            await first.run_probe("reuse")

    asyncio.run(exercise())


def test_success_without_explicit_cleanup_stays_fenced(tmp_path, monkeypatch):
    directory = private(tmp_path)

    async def unproven(script, **kwargs):
        return ProcessResult(0, b"looks successful", b"", 0.1)

    monkeypatch.setattr(shell_owner, "run_probe", unproven)

    async def exercise():
        owner = shell_owner.ShellOwner(directory, identity())
        with pytest.raises(QualificationError):
            await owner.run_probe("fixed")
        assert not owner.cleanup_confirmed
        with pytest.raises(AdmissionDenied, match="runtime_fenced"):
            await shell_owner.ShellOwner(directory, identity()).run_probe("fresh")

    asyncio.run(exercise())


def test_cancel_grace_retains_admission_until_late_confirmed_cleanup(tmp_path, monkeypatch):
    directory = private(tmp_path)

    async def exercise():
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def work(script, *, resources, **kwargs):
            resources.native._claim()
            resources.native._spawn_pending = True
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()  # Trusted cleanup survives work cancellation.
                resources.native._no_child()
                resources._terminal = True

        monkeypatch.setattr(shell_owner, "run_probe", work)
        owner = shell_owner.ShellOwner(directory, identity(), cleanup_grace=0.01)
        operation = asyncio.create_task(owner.run_probe("fixed"))
        await asyncio.wait_for(entered.wait(), 1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not owner.cleanup_confirmed
        with pytest.raises(AdmissionDenied):
            await shell_owner.ShellOwner(directory, identity()).run_probe("contender")
        release.set()
        await owner.aclose()
        assert owner.cleanup_confirmed and (directory / "active.marker").stat().st_size == 0

    asyncio.run(exercise())


def test_source_setup_failure_with_proven_no_resources_releases(tmp_path, monkeypatch):
    directory = private(tmp_path)

    async def fail(script, *, resources, **kwargs):
        resources._terminal = True
        raise DeveloperError("fixed_source_failure")

    monkeypatch.setattr(shell_owner, "run_probe", fail)

    async def exercise():
        owner = shell_owner.ShellOwner(directory, identity())
        with pytest.raises(QualificationError):
            await owner.run_probe("fixed")
        assert owner.cleanup_confirmed and (directory / "active.marker").stat().st_size == 0

    asyncio.run(exercise())


def test_pin_uncertainty_fences_even_after_native_ack(tmp_path, monkeypatch):
    directory = private(tmp_path)

    async def fail(script, *, resources, **kwargs):
        acknowledged(resources)
        resources._pins_settled = False
        raise OSError("fixture pin close failure")

    monkeypatch.setattr(shell_owner, "run_probe", fail)

    async def exercise():
        owner = shell_owner.ShellOwner(directory, identity())
        with pytest.raises(QualificationError):
            await owner.run_probe("fixed")
        assert not owner.cleanup_confirmed
        with pytest.raises(AdmissionDenied):
            await shell_owner.ShellOwner(directory, identity()).run_probe("fresh")

    asyncio.run(exercise())


def test_real_probe_flow_admits_before_verification_and_closes_pins(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    directory = private(tmp_path)
    events = []

    def verify(value, *, resources):
        assert (directory / "active.marker").stat().st_size > 0
        events.append("verify_after_admission")

    @contextmanager
    def pins(value, *, resources):
        resources._pins_settled = False
        events.append("pins_open")
        try:
            yield [SimpleNamespace(fd=i + 100) for i in range(len(value.sources))]
        finally:
            resources._pins_settled = True
            events.append("pins_closed")

    async def run(argv, *, observation, **kwargs):
        assert kwargs["deadline"] > asyncio.get_running_loop().time()
        observation._claim()
        observation._no_child()
        events.append("native_cleanup_ack")
        return ProcessResult(0, b"fixed", b"", 0.1)

    monkeypatch.setattr(shell_compute, "verify_sources", verify)
    monkeypatch.setattr(shell_compute, "_pin_sources", pins)
    monkeypatch.setattr(shell_compute, "run_process", run)

    async def exercise():
        owner = shell_owner.ShellOwner(directory, identity())
        assert (await owner.run_probe("fixed")).stdout == b"fixed"
        assert owner.cleanup_confirmed and (directory / "active.marker").stat().st_size == 0

    asyncio.run(exercise())
    assert events == ["verify_after_admission", "pins_open", "native_cleanup_ack", "pins_closed"]


def test_source_fd_close_uncertainty_never_becomes_cleanup_evidence(tmp_path, monkeypatch):
    import hashlib

    path = tmp_path / "artifact"
    path.write_bytes(b"fixed source fixture")
    path.chmod(0o600)
    value = shell_compute.ShellProbeResources()
    original = shell_compute.os.close

    def uncertain(fd):
        original(fd)  # Fixture actually closes its own FD, but reports uncertainty.
        raise OSError("fixture close uncertainty")

    monkeypatch.setattr(shell_compute.os, "close", uncertain)
    with pytest.raises(OSError):
        shell_compute.verify_file(
            path,
            hashlib.sha256(path.read_bytes()).hexdigest(),
            owner=path.stat().st_uid,
            resources=value,
        )
    value._terminal = True
    assert not value.cleanup_confirmed and value._verification_pending == 1


def test_partial_pin_creation_closes_entered_resources(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    events = []
    count = 0
    resources = shell_compute.ShellProbeResources()

    @contextmanager
    def pin(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("fixture second pin fails")
        try:
            yield SimpleNamespace(fd=100, verify=lambda: None)
        finally:
            events.append("first_pin_closed")

    monkeypatch.setattr(shell_compute, "pin_artifact", pin)
    with pytest.raises(OSError):
        with shell_compute._pin_sources(identity(), resources):
            pytest.fail("incomplete pins yielded")
    resources._terminal = True
    assert events == ["first_pin_closed"] and resources.cleanup_confirmed


def test_absolute_deadline_expired_before_sources_never_launches(monkeypatch):
    monkeypatch.setattr(shell_compute, "verify_sources", lambda *a, **kw: pytest.fail("sources"))
    resources = shell_compute.ShellProbeResources()

    async def exercise():
        with pytest.raises(DeveloperError, match="process_timeout"):
            await shell_compute.run_probe(
                "fixed",
                identity=identity(),
                resources=resources,
                deadline=asyncio.get_running_loop().time() - 1,
            )
        assert resources.cleanup_confirmed

    asyncio.run(exercise())
