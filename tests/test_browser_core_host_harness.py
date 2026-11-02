"""The real-host proof must remain explicit and absent from automatic CI."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from qualification.dns import QualificationError
from qualification.renderer_process import DockerHandle
from scripts import qualify_browser_core_binding as proof


def test_core_browser_host_proof_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["proof", "--bundle", "/unused", "--expected-bundle-digest", "0" * 64]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("host preparation was reached without operator opt-in")

    monkeypatch.setattr(proof, "prepare_candidate", forbidden)
    monkeypatch.setattr(proof, "cleanup_owned_setup", forbidden)
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2


def test_held_render_reaches_native_boundary_with_core_authorization(monkeypatch):
    async def native_render(self, *args, **kwargs):
        assert args == ("<main>safe</main>", "text/html", 12.0)
        assert kwargs["authorize_action"]() is True
        return object()

    monkeypatch.setattr(DockerHandle, "render", native_render)

    async def exercise():
        rendered = asyncio.Event()
        handle = proof._HeldDockerHandle(
            SimpleNamespace(identity="owned"),
            None,
            Path("/tmp"),
            Path("/tmp"),
            rendered=rendered,
        )
        operation = asyncio.create_task(
            handle.render(
                "<main>safe</main>",
                "text/html",
                12.0,
                authorize_action=lambda: True,
            )
        )
        await asyncio.wait_for(rendered.wait(), 1)
        assert not operation.done()
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)

    asyncio.run(exercise())


def test_lost_ack_fails_only_after_forwarded_native_create(monkeypatch):
    calls = []

    async def native_command(self, *args, **kwargs):
        assert args == ("create", "--name", "owned")
        assert kwargs["authorize_action"]() is True
        assert kwargs["mark_attempt"] is True
        calls.append("native_create_returned")
        return b"container-id"

    monkeypatch.setattr(DockerHandle, "_command", native_command)

    async def exercise():
        handle = proof._LostAckDockerHandle(
            SimpleNamespace(identity="owned"), None, Path("/tmp"), Path("/tmp")
        )
        with pytest.raises(QualificationError, match="controlled_lost_ack"):
            await handle._command(
                "create",
                "--name",
                "owned",
                authorize_action=lambda: True,
                mark_attempt=True,
            )

    asyncio.run(exercise())
    assert calls == ["native_create_returned"]
