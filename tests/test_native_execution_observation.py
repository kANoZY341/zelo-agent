"""Explicit generic process evidence; native fixtures are harmless Python only."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import developer_process as processes


def test_pending_and_unclaimed_records_never_confirm_cleanup():
    record = processes.NativeExecutionObservation()
    assert not record.cleanup_confirmed and not record.creation_started
    record._claim()
    assert not record.cleanup_confirmed
    record._spawn_pending = True
    assert not record.cleanup_confirmed
    record._no_child()
    assert record.cleanup_confirmed
    with pytest.raises(processes.DeveloperError, match="process_observation_reused"):
        record._claim()


@pytest.mark.parametrize(
    "code,limit,timeout,error",
    [
        ("print('fixed')", 65536, 2, None),
        ("print('x'*4096)", 64, 2, "process_output_limit"),
        ("import time;time.sleep(30)", 65536, 0.05, "process_timeout"),
    ],
)
def test_real_bounded_process_confirms_final_pipes_reap_and_io(code, limit, timeout, error):
    record = processes.NativeExecutionObservation()

    async def exercise():
        operation = processes.run_process(
            [sys.executable, "-I", "-S", "-c", code],
            cwd=Path("/"),
            timeout=timeout,
            stdout_limit=limit,
            env={},
            observation=record,
        )
        if error:
            with pytest.raises(processes.DeveloperError, match=error):
                await operation
        else:
            assert (await operation).stdout == b"fixed\n"
        assert record.cleanup_confirmed and record._process.reaped
        assert record._process not in processes._RETAINED_IDENTITIES
        assert all(
            pipe.closed for pipe in (record._process._child.stdout, record._process._child.stderr)
        )

    asyncio.run(exercise())


def test_preflight_failure_records_terminal_no_child(monkeypatch):
    record = processes.NativeExecutionObservation()

    def fail(*args):
        raise OSError("fixture unavailable")

    monkeypatch.setattr(processes.os, "pidfd_open", fail)
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawn"))

    async def exercise():
        with pytest.raises(processes.DeveloperError, match="process_identity_unavailable"):
            await processes.run_process(["/unused"], cwd=Path("/"), timeout=1, observation=record)
        assert record.cleanup_confirmed and record._process is None

    asyncio.run(exercise())


def test_post_spawn_acquisition_failure_has_explicit_cleanup_ack(monkeypatch):
    record = processes.NativeExecutionObservation()
    original = os.pidfd_open

    def acquire(pid):
        if pid == os.getpid():
            return original(pid)
        raise OSError(24, "fixture acquisition failure")

    monkeypatch.setattr(processes.os, "pidfd_open", acquire)

    async def exercise():
        with pytest.raises(processes.DeveloperError, match="process_identity_unavailable"):
            await processes.run_process(
                [sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)"],
                cwd=Path("/"),
                timeout=1,
                env={},
                observation=record,
            )
        assert record.cleanup_confirmed and record._process.reaped

    asyncio.run(exercise())


def test_cancel_partial_connection_waits_for_creation_cleanup_ack(monkeypatch):
    record = processes.NativeExecutionObservation()

    async def exercise():
        entered = asyncio.Event()

        async def connect(process):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(processes.OwnedProcess, "connect", connect)
        task = asyncio.create_task(
            processes.run_process(
                [sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)"],
                cwd=Path("/"),
                timeout=2,
                env={},
                observation=record,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        assert record._process is not None and not record.cleanup_confirmed
        task.cancel()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert record.cleanup_confirmed and record._process.reaped

    asyncio.run(exercise())


def test_unknown_child_cleanup_still_settles_reader_tasks(monkeypatch):
    record = processes.NativeExecutionObservation()
    streams = []

    async def exercise():
        for _ in range(2):
            stream = asyncio.StreamReader()
            stream.feed_eof()
            streams.append(stream)

        async def wait():
            return 0

        process = SimpleNamespace(
            stdout=streams[0],
            stderr=streams[1],
            stdin=None,
            wait=wait,
            reaped=False,
            _pidfd=321,
            _child=SimpleNamespace(
                stdin=None, stdout=SimpleNamespace(closed=True), stderr=SimpleNamespace(closed=True)
            ),
        )

        async def spawn(*a, observation, **kwargs):
            observation._claim()
            observation._process = process
            observation._creation_terminal = True
            return process

        async def stop(*args):
            raise processes.DeveloperError("process_identity_lost")

        monkeypatch.setattr(processes, "spawn_owned_process", spawn)
        monkeypatch.setattr(processes, "stop_process", stop)
        with pytest.raises(processes.DeveloperError, match="process_identity_lost"):
            await processes.run_process(["fixture"], cwd=Path("/"), timeout=1, observation=record)
        assert record._io_settled and not record.cleanup_confirmed
        assert len(asyncio.all_tasks()) == 1  # No abandoned reader/drain tasks.

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["permission", "malformed"])
def test_group_confirmation_refuses_incomplete_proc_observation(tmp_path, monkeypatch, failure):
    entry = tmp_path / "12345"
    entry.mkdir()
    (entry / "stat").write_text("malformed")
    monkeypatch.setattr(processes, "Path", lambda *a: tmp_path)
    if failure == "permission":
        monkeypatch.setattr(
            Path, "read_text", lambda *a, **kw: (_ for _ in ()).throw(PermissionError())
        )
    with pytest.raises(processes.DeveloperError, match="process_group_observation_failed"):
        processes._group_alive(12345)


def test_expired_deadline_after_preflight_refuses_child_creation(monkeypatch):
    record = processes.NativeExecutionObservation()
    monkeypatch.setattr(
        processes.subprocess, "Popen", lambda *a, **kw: pytest.fail("expired spawn")
    )

    async def exercise():
        with pytest.raises(processes.DeveloperError, match="process_timeout"):
            await processes.run_process(
                ["fixture"],
                cwd=Path("/"),
                timeout=1,
                observation=record,
                deadline=asyncio.get_running_loop().time() - 1,
            )
        assert record.cleanup_confirmed and record._process is None

    asyncio.run(exercise())


def test_preflight_descriptor_close_uncertainty_prevents_ack(monkeypatch):
    record = processes.NativeExecutionObservation()
    original_open, original_close = os.pidfd_open, os.close
    support = []
    failed = False

    def acquire(pid):
        fd = original_open(pid)
        support.append(fd)
        return fd

    def close(fd):
        nonlocal failed
        original_close(fd)
        if support and fd == support[0] and not failed:
            failed = True
            raise OSError("fixture support close uncertainty")

    monkeypatch.setattr(processes.os, "pidfd_open", acquire)
    monkeypatch.setattr(processes.os, "close", close)
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawn"))

    async def exercise():
        with pytest.raises(processes.DeveloperError, match="process_identity_unavailable"):
            await processes.run_process(["unused"], cwd=Path("/"), timeout=1, observation=record)
        assert record._process is None and record._support_fd is not None
        assert not record.cleanup_confirmed

    asyncio.run(exercise())


def test_unknown_constructor_interruption_never_claims_no_child(monkeypatch):
    record = processes.NativeExecutionObservation()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(processes.subprocess, "Popen", interrupted)

    async def exercise():
        # Catch within the fixture coroutine; no real child or signal is involved.
        try:
            await processes.run_process(["fixture"], cwd=Path("/"), timeout=1, observation=record)
        except KeyboardInterrupt:
            pass
        else:
            pytest.fail("interruption missing")
        assert record.creation_started and record._spawn_pending
        assert not record.cleanup_confirmed

    asyncio.run(exercise())


def test_second_cancel_during_startup_cleanup_preserves_ack(monkeypatch):
    record = processes.NativeExecutionObservation()
    original_stop = processes.stop_process

    async def exercise():
        connecting, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def connect(process):
            connecting.set()
            await asyncio.Event().wait()

        async def held_stop(process):
            cleaning.set()
            await release.wait()
            await original_stop(process)

        monkeypatch.setattr(processes.OwnedProcess, "connect", connect)
        monkeypatch.setattr(processes, "stop_process", held_stop)
        operation = asyncio.create_task(
            processes.run_process(
                [sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)"],
                cwd=Path("/"),
                timeout=2,
                env={},
                observation=record,
            )
        )
        try:
            await asyncio.wait_for(connecting.wait(), 1)
            operation.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            assert not record.cleanup_confirmed and not record._process.reaped
            operation.cancel()  # Arrives while cleanup is actually suspended.
            await asyncio.sleep(0)
            assert not record.cleanup_confirmed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert record.cleanup_confirmed and record._process.reaped
        finally:
            release.set()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

    asyncio.run(exercise())
