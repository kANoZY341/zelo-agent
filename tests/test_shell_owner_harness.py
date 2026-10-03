"""Native ownership harness opt-in and reporting checks, without guests."""

import json
import os
import sys
from pathlib import Path

import pytest

from scripts import qualify_shell_owner as proof


@pytest.fixture(autouse=True)
def offline_operator_identity(monkeypatch):
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


@pytest.mark.parametrize("fixture", [False, True])
def test_every_harness_entry_requires_native_opt_in(monkeypatch, fixture):
    monkeypatch.setattr(sys, "argv", ["proof"] + (["--fixture-parent"] if fixture else []))
    monkeypatch.setattr(proof, "qualify", lambda *a: pytest.fail("native qualify"))
    monkeypatch.setattr(proof, "fixture_parent", lambda *a: pytest.fail("native fixture"))
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2


def test_report_is_private_bounded_and_does_not_include_exception_details(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "BASE", tmp_path / "owner-proof-base")
    monkeypatch.setattr(proof, "command", lambda args: "0" * 40 if "rev-parse" in args else "")

    async def fail(*args):
        raise RuntimeError("sensitive fixture details")

    monkeypatch.setattr(proof, "exercise", fail)
    path, passed = proof.qualify()
    assert not passed and path.stat().st_size < 131072
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    value = json.loads(path.read_text())
    assert value["status"] == "failed" and value["failure_type"] == "RuntimeError"
    assert "sensitive" not in path.read_text()


def test_core_assembly_uses_adapter_without_direct_probe_or_owner_import():
    root = Path(__file__).resolve().parents[1]
    for relative in ("app/main.py", "app/api.py", "app/config.py"):
        text = (root / relative).read_text()
        assert "qualification.shell_owner" not in text
        assert "scripts.qualify_shell" not in text


@pytest.mark.parametrize("changed", ["start_ticks", "parent"])
def test_descendant_identity_revalidated_after_pidfd_acquisition(monkeypatch, changed):
    from dataclasses import replace

    expected = proof.ProcessIdentity(100, 200, 200, 1234, "dash")
    later = replace(expected, **{changed: getattr(expected, changed) + 1})
    samples = iter([expected, later])
    closed = []
    monkeypatch.setattr(proof, "process_identity", lambda pid: next(samples))
    monkeypatch.setattr(proof.os, "pidfd_open", lambda pid: 321)
    monkeypatch.setattr(proof.os, "close", lambda fd: closed.append(fd))
    with pytest.raises(RuntimeError, match="shell_fixture_ancestry_changed"):
        proof.bind_descendant_pidfd(456, expected)
    assert closed == [321]


def test_kernel_pidfd_identity_mismatch_is_not_exit_evidence(monkeypatch):
    expected = proof.ProcessIdentity(100, 200, 200, 1234, "dash")
    closed = []
    monkeypatch.setattr(proof, "process_identity", lambda pid: expected)
    monkeypatch.setattr(proof.os, "pidfd_open", lambda pid: 321)
    monkeypatch.setattr(proof.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(proof.select, "select", lambda *a: ([], [], []))
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: "Pid:\t457\n")
    with pytest.raises(RuntimeError, match="shell_fixture_pidfd_identity_changed"):
        proof.bind_descendant_pidfd(456, expected)
    assert closed == [321]
