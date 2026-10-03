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


def test_core_assembly_does_not_import_or_register_shell_qualification():
    root = Path(__file__).resolve().parents[1]
    for relative in ("app/main.py", "app/api.py", "app/config.py"):
        text = (root / relative).read_text()
        assert "qualification.shell_owner" not in text and "shell_run" not in text
