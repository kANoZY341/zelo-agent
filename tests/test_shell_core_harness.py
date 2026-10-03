"""Opt-in proof control tests, without running optional native Shell fixtures."""

import json
import sys

import pytest

from scripts import qualify_shell_core_binding as proof


def test_explicit_opt_in_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["qualification"])
    monkeypatch.setattr(proof, "qualify", lambda: pytest.fail("native proof started"))
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2


def test_init_only_settings_ignore_invalid_ambient(runtime_path, monkeypatch):
    monkeypatch.setenv("ZELO_BROWSER_WORKER_ENABLED", "not-a-bool")
    monkeypatch.setenv("ZELO_SHELL_WORKER_ENABLED", "not-a-bool")
    monkeypatch.setenv("ZELO_SHELL_WORKER_ADMISSION", "/untrusted")
    monkeypatch.setenv("ZELO_OPERATOR_TOKEN", "ambient-not-a-credential")
    settings = proof.QualificationSettings(
        _env_file=None,
        runtime_dir=runtime_path,
        operator_token="diagnostic-placeholder-not-a-credential",
    )
    assert not settings.shell_worker_enabled and settings.shell_worker_admission is None
    assert settings.operator_token.get_secret_value() == "diagnostic-placeholder-not-a-credential"


def test_failed_report_private_and_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "BASE", tmp_path / "proof")
    monkeypatch.setattr(proof, "command", lambda *_: "fixture\n")

    async def fail(*_):
        raise RuntimeError("sensitive fixture exception detail")

    monkeypatch.setattr(proof, "exercise", fail)
    path, passed = proof.qualify()
    assert not passed and path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    report = json.loads(path.read_text())
    assert report["status"] == "failed" and report["failure_type"] == "RuntimeError"
    assert "sensitive" not in path.read_text()
