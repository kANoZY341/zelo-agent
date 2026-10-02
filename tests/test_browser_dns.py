"""Controlled children only: never invoke the real DNS child against DNS."""

import asyncio
import json
import os
import sys
from functools import wraps

import pytest

from qualification.dns import CHILD_CODE, OwnedResolver, QualificationError


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class ChildFactory:
    def __init__(self, code="import time; time.sleep(60)", creation_gate=None):
        self.code = code
        self.creation_gate = creation_gate
        self.children = []
        self.args = None
        self.kwargs = None
        self.created = asyncio.Event()

    async def __call__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            "-c",
            self.code,
            **kwargs,
        )
        self.children.append(child)
        self.created.set()
        if self.creation_gate is not None:
            await self.creation_gate.wait()
        return child

    def assert_reaped(self):
        assert self.children
        for child in self.children:
            assert child.returncode is not None
            with pytest.raises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


@run_async
async def test_real_timeout_kills_and_reaps_owned_child():
    factory = ChildFactory()
    resolver = OwnedResolver(spawn=factory)
    with pytest.raises(TimeoutError):
        await resolver.resolve("example.com", 443, deadline(0.05))
    factory.assert_reaped()
    assert not resolver._busy and not resolver._poisoned
    await resolver.aclose()


@run_async
@pytest.mark.parametrize("during_creation", [False, True])
async def test_real_repeated_cancellation_kills_and_reaps(during_creation):
    gate = asyncio.Event() if during_creation else None
    factory = ChildFactory(creation_gate=gate)
    resolver = OwnedResolver(spawn=factory)
    task = asyncio.create_task(resolver.resolve("example.com", 443, deadline()))
    await factory.created.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    if gate:
        gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    factory.assert_reaped()
    assert not resolver._busy and not resolver._poisoned


@run_async
async def test_busy_closed_and_close_cleanup():
    factory = ChildFactory()
    resolver = OwnedResolver(spawn=factory)
    task = asyncio.create_task(resolver.resolve("example.com", 443, deadline()))
    await factory.created.wait()
    results = await asyncio.gather(
        *(resolver.resolve("example.com", 443, deadline()) for _ in range(50)),
        return_exceptions=True,
    )
    assert all(isinstance(item, QualificationError) for item in results)
    assert len(factory.children) == 1
    await resolver.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    factory.assert_reaped()
    with pytest.raises(QualificationError):
        await resolver.resolve("example.com", 443, deadline())


@run_async
async def test_fixed_command_scrubbed_environment_and_absolute_fqdn(monkeypatch):
    monkeypatch.setenv("ZELO_SECRET", "never-inherit")
    factory = ChildFactory(
        "import os,sys; "
        "assert 'ZELO_SECRET' not in os.environ; "
        "assert not any(k.endswith('_PROXY') for k in os.environ); "
        "sys.stdout.write('[\"8.8.8.8\"]')"
    )
    resolver = OwnedResolver(spawn=factory)
    assert await resolver.resolve("example.com", 8443, deadline()) == ("8.8.8.8",)
    assert factory.args == (sys.executable, "-I", "-S", "-c", CHILD_CODE, "example.com.", "8443")
    assert factory.kwargs["env"] == {}
    assert factory.kwargs["stderr"] == asyncio.subprocess.DEVNULL
    assert factory.kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert factory.kwargs["close_fds"] is True
    factory.assert_reaped()


@run_async
@pytest.mark.parametrize(
    "output",
    [
        "garbage",
        "{}",
        "[]",
        "[null]",
        '["8.8.8.8", "8.8.8.8"]',
        '["127.0.0.1"]',
        '["8.8.8.8", "10.0.0.1"]',
        '["fe80::1%eth0"]',
        '["2002:808:808::1"]',
        json.dumps(["8.8.8.8"] * 17),
        "x" * 5000,
    ],
)
async def test_malformed_and_capped_child_output(output):
    factory = ChildFactory(f"import sys; sys.stdout.write({output!r})")
    resolver = OwnedResolver(spawn=factory)
    with pytest.raises(QualificationError):
        await resolver.resolve("example.com", 443, deadline())
    factory.assert_reaped()


@run_async
async def test_continuous_output_is_capped_and_reaped():
    factory = ChildFactory("import os\nwhile True: os.write(1, b'x' * 4096)")
    resolver = OwnedResolver(spawn=factory)
    with pytest.raises(QualificationError, match="output_limit"):
        await resolver.resolve("example.com", 443, deadline())
    factory.assert_reaped()


@run_async
async def test_numeric_and_bad_host_never_spawn():
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not spawn")

    resolver = OwnedResolver(spawn=forbidden)
    for host in ("8.8.8.8", "2606:4700:4700::1111"):
        assert await resolver.resolve(host, 8443, deadline()) == (host,)
    for host in ("localhost", "example.com.", "Example.com", "a/b", "127.0.0.1", "224.0.0.1"):
        with pytest.raises(ValueError):
            await resolver.resolve(host, 443, deadline())


@run_async
async def test_unproven_creation_cleanup_poisons_admission(monkeypatch):
    # Shorten the test's cleanup grace only; the child still must eventually be reaped.
    from qualification import dns

    original = dns.settle

    async def short_settle(task):
        return await original(task, grace=0.01)

    monkeypatch.setattr(dns, "settle", short_settle)
    gate = asyncio.Event()
    factory = ChildFactory(creation_gate=gate)
    resolver = OwnedResolver(spawn=factory)
    task = asyncio.create_task(resolver.resolve("example.com", 443, deadline()))
    await factory.created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert resolver._poisoned
    with pytest.raises(QualificationError):
        await resolver.resolve("example.com", 443, deadline())
    gate.set()
    await resolver._cleanup
    factory.assert_reaped()
