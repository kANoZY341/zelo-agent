"""Operator proof controls and offline ASGI integration; no optional native guest."""

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.main import create_app
from qualification.shell_compute import ShellIdentity
from scripts import qualify_shell_operator as proof
from tests.developer_helpers import FakeWorker


@pytest.fixture(autouse=True)
def no_optional_guests(monkeypatch):
    monkeypatch.setattr(
        "qualification.shell_owner.run_probe", lambda *_a, **_k: pytest.fail("native guest")
    )


def test_opt_in_before_proof_entry(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["proof"])
    monkeypatch.setattr(proof, "qualify", lambda: pytest.fail("native proof"))
    with pytest.raises(SystemExit) as error:
        proof.main()
    assert error.value.code == 2


def test_optimized_python_refused_before_native_entry():
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-m",
            "scripts.qualify_shell_operator",
            "--allow-native-shell-probes",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2 and b"assertion-enabled Python is required" in result.stderr
    assert b"EVIDENCE=" not in result.stdout


def test_init_only_settings_ignore_ambient(runtime_path, monkeypatch):
    for key, value in {
        "ZELO_SHELL_WORKER_ENABLED": "invalid",
        "ZELO_BROWSER_WORKER_ENABLED": "invalid",
        "ZELO_OPERATOR_TOKEN": "ambient-not-a-credential",
        "ZELO_RUNTIME_DIR": "/hostile",
        "ZELO_SHELL_WORKER_ADMISSION": "/hostile",
        "ZELO_SCHEDULER_ENABLED": "invalid",
    }.items():
        monkeypatch.setenv(key, value)
    settings = proof.settings_for(runtime_path, "enabled", enabled=True)
    assert settings.runtime_dir == runtime_path / "enabled/core"
    assert settings.shell_worker_enabled and not settings.browser_worker_enabled
    assert settings.shell_worker_admission == runtime_path / "enabled/admission"
    assert not settings.scheduler_enabled
    assert settings.operator_token.get_secret_value() == proof.TOKEN


def test_reports_private_exclusive_bounded_and_nonfinite_refused(tmp_path):
    path = proof.save_report(tmp_path, {"status": "fixture"})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        proof.save_report(tmp_path, {})
    with pytest.raises(ValueError, match="report_limit"):
        proof.save_report(tmp_path, {"data": "x" * proof.REPORT_LIMIT})
    with pytest.raises(ValueError):
        proof.save_report(tmp_path, {"duration": float("nan")})


def test_exception_details_not_in_report(runtime_path, monkeypatch):
    monkeypatch.setattr(proof, "BASE", runtime_path / "proof")

    def command(args):
        return "a" * 40 + "\n" if args[-1] == "HEAD" else ""

    async def fail(*_):
        raise RuntimeError("sensitive fixture-only detail")

    monkeypatch.setattr(proof, "command", command)
    monkeypatch.setattr(proof, "exercise", fail)
    path, passed = proof.qualify()
    assert not passed and path.parent.stat().st_mode & 0o777 == 0o700
    value = json.loads(path.read_text())
    assert value["failure_type"] == "RuntimeError" and "sensitive" not in path.read_text()


def test_model_provider_rejects_and_records_both_request_classes():
    async def exercise():
        provider = proof.RejectModelRequests()
        provider.confirm_none()
        with pytest.raises(RuntimeError, match="forbidden"):
            await provider.generate(object())
        with pytest.raises(RuntimeError, match="forbidden"):
            await provider.models()
        assert provider.generate_calls == provider.models_calls == 1
        with pytest.raises(RuntimeError, match="observed"):
            provider.confirm_none()

    asyncio.run(exercise())


def test_offline_full_api_workflow_controls(runtime_path, monkeypatch):
    from app.shell_worker import ShellRuntime

    monkeypatch.setattr(ShellIdentity, "current", classmethod(lambda _: ShellIdentity(1000, 1000)))
    monkeypatch.setattr(ShellIdentity, "verify_current", lambda _: None)
    monkeypatch.setattr(ShellRuntime, "_source_metadata", lambda _: ((1, 2, 3),))
    monkeypatch.setattr("app.shell_worker.verify_sources", lambda *_: None)
    monkeypatch.setattr(
        proof,
        "create_app",
        lambda settings, provider: create_app(
            settings, provider=provider, developer_worker=FakeWorker()
        ),
    )
    made = []

    class OfflineOwner:
        cleanup_confirmed = False
        cleanup_required = False

        def __init__(self, *args, **kwargs):
            made.append(self)

        async def run_probe(self, script):
            expected = dict((body, output) for _, body, output in proof.SCRIPTS)
            assert script in expected
            self.cleanup_confirmed = True
            return SimpleNamespace(
                stdout=expected[script].encode(), stderr=b"", exit_code=0, duration=0.01
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("app.shell_worker.ShellOwner", OfflineOwner)
    report = {"cases": []}
    asyncio.run(proof.exercise(runtime_path, report))
    assert len(report["cases"]) == 6 and all(case["passed"] for case in report["cases"])
    assert len(made) == 2 and report["runtime_cleanup_confirmed"]
    assert report["model_generate_calls"] == report["model_inventory_calls"] == 0
    assert (runtime_path / "fenced/admission/active.marker").stat().st_size > 0


@pytest.mark.parametrize(
    "missing", ["shell_completed", "shell_policy_revalidated", "execution_started"]
)
def test_audit_requires_complete_binding_and_terminal_events(missing):
    task = SimpleNamespace(shell_spec=SimpleNamespace(operator_identity="a" * 64))
    names = [
        "supervisor_routing_requested",
        "supervisor_worker_selected",
        "shell_policy_bound",
        "supervisor_worker_revalidated",
        "shell_policy_revalidated",
        "attempt_created",
        "execution_started",
        "shell_execution_started",
        "shell_execution_completed",
        "execution_completed",
        "shell_completed",
    ]
    events = [
        {"event": name, "details": {"operator_identity": "a" * 64, "instruction_trust": "none"}}
        for name in names
        if name != missing
    ]
    with pytest.raises(RuntimeError, match="audit_incomplete"):
        proof.validate_audit(events, task)


@pytest.mark.parametrize(
    "mutation",
    [
        "payload",
        "script_digest",
        "stdout",
        "audit_task",
        "audit_attempt",
        "audit_policy",
        "duplicate",
    ],
)
def test_evidence_and_audit_mutation_rejected(runtime_path, monkeypatch, mutation):
    # Capture actual offline ASGI responses, then mutate only the proof evidence.
    from copy import deepcopy

    from app.shell_worker import ShellRuntime

    monkeypatch.setattr(ShellIdentity, "current", classmethod(lambda _: ShellIdentity(1000, 1000)))
    monkeypatch.setattr(ShellIdentity, "verify_current", lambda _: None)
    monkeypatch.setattr(ShellRuntime, "_source_metadata", lambda _: ((1, 2, 3),))
    monkeypatch.setattr("app.shell_worker.verify_sources", lambda *_: None)
    monkeypatch.setattr(
        proof,
        "create_app",
        lambda settings, provider: create_app(
            settings, provider=provider, developer_worker=FakeWorker()
        ),
    )

    class OfflineOwner:
        cleanup_confirmed = False
        cleanup_required = False

        def __init__(self, *args, **kwargs):
            pass

        async def run_probe(self, script):
            expected = dict((body, output) for _, body, output in proof.SCRIPTS)
            self.cleanup_confirmed = True
            return SimpleNamespace(
                stdout=expected[script].encode(), stderr=b"", exit_code=0, duration=0.01
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("app.shell_worker.ShellOwner", OfflineOwner)
    original_task, original_audit = proof.validate_task, proof.validate_audit

    def validate_task(value, *args):
        value = deepcopy(value)
        if mutation == "payload":
            value["payload"]["script"] = "printf changed"
        elif mutation == "script_digest":
            value["shell_spec"]["script_digest"] = "0" * 64
        elif mutation == "stdout":
            value["result"]["stdout"] = "unexpected"
        return original_task(value, *args)

    def validate_audit(events, task):
        events = deepcopy(events)
        terminal = next(event for event in events if event["event"] == "shell_completed")
        if mutation == "audit_task":
            terminal["task_id"] = "other-task"
        elif mutation == "audit_attempt":
            terminal["attempt_id"] = "other-attempt"
        elif mutation == "audit_policy":
            terminal["details"]["shell_policy_version"] = "0" * 64
        elif mutation == "duplicate":
            events.append(deepcopy(terminal))
        return original_audit(events, task)

    monkeypatch.setattr(proof, "validate_task", validate_task)
    monkeypatch.setattr(proof, "validate_audit", validate_audit)
    with pytest.raises(RuntimeError, match="operator_(evidence|audit)_"):
        asyncio.run(proof.exercise(runtime_path, {"cases": []}))
