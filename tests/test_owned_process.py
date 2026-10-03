"""PID identity regressions and harmless Linux child ownership checks."""

import asyncio
import os
import signal
import sys
from types import SimpleNamespace

import pytest

from app import developer_process as processes


def fake_owned(*, reaped=False):
    process = object.__new__(processes.OwnedProcess)
    process.pid = 12345
    process._pidfd = 321
    process._code = None
    process.reaped = reaped
    return process


def test_released_numeric_identity_is_never_signalled(monkeypatch):
    process = fake_owned(reaped=True)
    monkeypatch.setattr(processes.os, "killpg", lambda *a: pytest.fail("released PGID signalled"))
    monkeypatch.setattr(
        processes.os, "waitid", lambda *a: pytest.fail("released identity observed")
    )
    with pytest.raises(processes.DeveloperError, match="process_identity_unowned"):
        process.signal_group(signal.SIGKILL)


@pytest.mark.parametrize("failure", ["reaped_elsewhere", "wrong_identity"])
def test_lost_or_reused_identity_never_signalled(monkeypatch, failure):
    process = fake_owned()

    def waitid(*args):
        if failure == "reaped_elsewhere":
            raise ChildProcessError()
        return SimpleNamespace(si_pid=process.pid + 1, si_status=0, si_code=os.CLD_EXITED)

    monkeypatch.setattr(processes.os, "waitid", waitid)
    monkeypatch.setattr(processes.os, "killpg", lambda *a: pytest.fail("unowned PGID signalled"))
    with pytest.raises(processes.DeveloperError, match="process_identity_lost"):
        process.signal_group(signal.SIGTERM)


def test_exit_observation_leaves_exact_child_reserved_until_final_reap(monkeypatch):
    process = fake_owned()
    events = []

    def waitid(idtype, identity, flags):
        assert idtype == os.P_PIDFD and identity == process._pidfd
        assert flags & os.WNOWAIT and flags & os.WNOHANG and flags & os.WEXITED
        events.append("observe_without_reap")
        return SimpleNamespace(si_pid=process.pid, si_status=7, si_code=os.CLD_EXITED)

    process._child = SimpleNamespace(wait=lambda **kw: events.append("reap") or 7)
    processes._RETAINED_IDENTITIES.add(process)
    monkeypatch.setattr(processes.os, "waitid", waitid)
    monkeypatch.setattr(processes.os, "killpg", lambda pid, sig: events.append("signal_owned"))
    monkeypatch.setattr(processes.os, "close", lambda fd: events.append("close_pidfd"))
    assert asyncio.run(process.wait()) == 7 and not process.reaped
    process.signal_group(signal.SIGKILL)
    assert "reap" not in events
    process.reap()
    assert process.reaped and process not in processes._RETAINED_IDENTITIES
    assert events[-2:] == ["reap", "close_pidfd"]
    with pytest.raises(processes.DeveloperError):
        process.signal_group(signal.SIGKILL)
    assert events.count("signal_owned") == 1


def test_already_reaped_asyncio_process_is_rejected_without_numeric_signal(monkeypatch):
    unknown = SimpleNamespace(pid=12345, returncode=0)
    monkeypatch.setattr(processes.os, "killpg", lambda *a: pytest.fail("unknown PGID signalled"))
    with pytest.raises(processes.DeveloperError, match="process_identity_unowned"):
        asyncio.run(processes.stop_process(unknown))


def test_actual_exited_child_is_unreaped_until_group_cleanup(monkeypatch):
    calls = []
    original = os.killpg

    async def exercise():
        process = await processes.spawn_owned_process(
            sys.executable,
            "-I",
            "-S",
            "-c",
            "pass",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={},
            start_new_session=True,
        )
        try:
            assert await asyncio.wait_for(process.wait(), 2) == 0
            assert not process.reaped and process._child.returncode is None
            assert (
                os.waitid(os.P_PIDFD, process._pidfd, os.WEXITED | os.WNOWAIT).si_pid == process.pid
            )

            def killpg(pid, sig):
                assert pid == process.pid and not process.reaped
                assert process._child.returncode is None
                calls.append(sig)
                original(pid, sig)

            monkeypatch.setattr(processes.os, "killpg", killpg)
            await processes.stop_process(process)
            assert process.reaped and process._child.returncode == 0
            await processes.stop_process(process)
            assert calls == [signal.SIGTERM, signal.SIGKILL]
        finally:
            if not process.reaped:
                await processes.stop_process(process)

    asyncio.run(exercise())


def test_creation_cancellation_closes_and_reaps_owned_child(monkeypatch):
    original = processes.OwnedProcess.connect
    owned = []

    async def exercise():
        entered = asyncio.Event()

        async def held(process):
            owned.append(process)
            entered.set()
            await asyncio.Event().wait()
            await original(process)

        monkeypatch.setattr(processes.OwnedProcess, "connect", held)
        operation = asyncio.create_task(
            processes.spawn_owned_process(
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import time;time.sleep(30)",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={},
                start_new_session=True,
            )
        )
        await entered.wait()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert owned[0].reaped and owned[0] not in processes._RETAINED_IDENTITIES

    asyncio.run(exercise())


def test_group_cleanup_handles_background_child_after_leader_exit():
    from pathlib import Path

    code = """import os,signal,time
child=os.fork()
if child:
    print(child,flush=True)
else:
    for fd in (0,1,2):
        os.close(fd)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    time.sleep(30)
"""

    async def exercise():
        process = await processes.spawn_owned_process(
            sys.executable,
            "-I",
            "-S",
            "-c",
            code,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={},
            start_new_session=True,
        )
        try:
            child = int(await asyncio.wait_for(process.stdout.readline(), 2))
            assert await asyncio.wait_for(process.wait(), 2) == 0
            assert not process.reaped and process._child.returncode is None
            await processes.stop_process(process)
            status = Path(f"/proc/{child}/stat")
            assert not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z"
            assert process.reaped
        finally:
            if not process.reaped:
                await processes.stop_process(process)

    asyncio.run(exercise())


def test_unavailable_pidfd_prerequisite_never_spawns_a_child(monkeypatch):
    def unavailable(*args):
        raise PermissionError()

    monkeypatch.setattr(processes.os, "pidfd_open", unavailable)
    monkeypatch.setattr(
        processes.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("unsupported identity spawned child"),
    )
    before = set(processes._RETAINED_IDENTITIES)

    async def exercise():
        with pytest.raises(processes.DeveloperError, match="process_identity_unavailable"):
            await processes.spawn_owned_process("/unused", start_new_session=True)

    asyncio.run(exercise())
    assert processes._RETAINED_IDENTITIES == before
