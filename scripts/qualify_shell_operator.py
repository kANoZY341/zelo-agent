"""Opt-in fixed Shell tasks through private unmodified Core application and ASGI."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import ROOT, Settings
from app.database import Database
from app.main import create_app
from app.models import Task
from app.policy import action_hash
from app.schemas import ShellRunResult, TaskResponse
from app.shell_worker import ShellRuntimeError
from qualification.admission import AdmissionController
from qualification.shell_compute import ShellIdentity
from scripts.qualify_browser_renderer import command
from scripts.qualify_shell_owner import private_directory

BASE = ROOT / "runtime/shell-operator-qualification"
TOKEN = "diagnostic-placeholder-not-a-credential"
REPORT_LIMIT = 131072
SCRIPTS = (
    ("builtin_compute", "n=6; printf 'CORE_OPERATOR_%s' \"$((n * 7))\"\n", "CORE_OPERATOR_42"),
    ("readmission", "printf READMISSION_OK\n", "READMISSION_OK"),
)


class QualificationSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


class RejectModelRequests:
    def __init__(self):
        self.generate_calls = 0
        self.models_calls = 0

    async def generate(self, request):
        self.generate_calls += 1
        raise RuntimeError("operator_model_request_forbidden")

    async def models(self):
        self.models_calls += 1
        raise RuntimeError("operator_model_request_forbidden")

    def confirm_none(self):
        if self.generate_calls or self.models_calls:
            raise RuntimeError("operator_model_request_observed")


def settings_for(root: Path, case: str, *, enabled: bool) -> Settings:
    directory = root / case
    directory.mkdir(mode=0o700)
    admission = directory / "admission"
    admission.mkdir(mode=0o700)
    return QualificationSettings(
        _env_file=None,
        operator_token=TOKEN,
        runtime_dir=directory / "core",
        workspace_root=ROOT / "workspaces" / ("shell-operator-" + root.name + "-" + case),
        scheduler_enabled=False,
        runner_poll_seconds=0.01,
        shell_worker_enabled=enabled,
        shell_worker_admission=admission,
    )


def migrated(settings):
    database = Database(settings.database_path)
    try:
        database.migrate()
    finally:
        database.close()


def admission_clear(directory):
    marker = directory / "active.marker"
    return not marker.exists() or marker.stat().st_size == 0


def validate_task(value, runtime, supervisor, script, expected):
    task = TaskResponse.model_validate(value)
    if task.status != "succeeded" or task.result is None or task.shell_spec is None:
        raise RuntimeError("operator_task_failed")
    spec = task.shell_spec
    result = ShellRunResult.model_validate(task.result)
    if (
        task.kind != "shell_run"
        or task.payload.model_dump(mode="json") != {"script": script}
        or task.action_version != 1
        or len(task.attempts) != 1
        or task.attempts[0].outcome != "succeeded"
        or spec.script_digest != hashlib.sha256(script.encode()).hexdigest()
        or spec.policy_version != runtime.policy.digest
        or spec.operator_identity != runtime.identity
        or not supervisor.matches(spec.worker_selection)
        or not runtime.request_matches(spec, {"script": script}, task.timeout_seconds)
        or result.policy_version != spec.policy_version
        or result.instruction_trust != "none"
        or result.exit_code != 0
        or result.stdout != expected
        or result.stderr
    ):
        raise RuntimeError("operator_evidence_mismatch")
    return task, result.model_dump(mode="json")


def validate_audit(events, task):
    names = [event["event"] for event in events]
    required = {
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
    }
    if not required.issubset(names):
        raise RuntimeError("operator_audit_incomplete")
    if any(event["task_id"] != task.id for event in events):
        raise RuntimeError("operator_audit_task_mismatch")
    if any(names.count(name) != 1 for name in required):
        raise RuntimeError("operator_audit_duplicate_execution")
    terminal = [event for event in events if event["event"] == "shell_completed"]
    if (
        len(terminal) != 1
        or terminal[0]["details"]["instruction_trust"] != "none"
        or terminal[0]["attempt_id"] != task.attempts[0].id
        or terminal[0]["details"]["shell_policy_version"] != task.shell_spec.policy_version
    ):
        raise RuntimeError("operator_audit_trust_mismatch")
    bound = [event for event in events if event["event"] == "shell_policy_bound"]
    if (
        len(bound) != 1
        or bound[0]["details"]["operator_identity"] != task.shell_spec.operator_identity
    ):
        raise RuntimeError("operator_audit_identity_mismatch")
    return sorted(required)


async def wait_task(client, task_id):
    async with asyncio.timeout(8):
        while True:
            response = await client.get("/tasks/" + task_id)
            if response.status_code != 200:
                raise RuntimeError("operator_task_read_failed")
            value = response.json()
            if value["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                return value
            await asyncio.sleep(0.01)


async def exercise(root, report):
    provider = RejectModelRequests()
    disabled = settings_for(root, "disabled", enabled=False)
    migrated(disabled)
    application = create_app(disabled, provider=provider)
    async with application.router.lifespan_context(application):
        assert application.state.shell_runtime is None
        assert not application.state.shell_worker_registry.list()
        assert not (disabled.shell_worker_admission / "active.marker").exists()
        provider.confirm_none()
    report["cases"].append({"case": "default_disabled_application", "passed": True})

    fenced = settings_for(root, "fenced", enabled=True)
    migrated(fenced)
    identity = ShellIdentity.current()
    lease = AdmissionController(fenced.shell_worker_admission).acquire(identity.digest)
    lease.finish(cleanup_confirmed=False)
    marker = fenced.shell_worker_admission / "active.marker"
    before = marker.read_bytes()
    application = create_app(fenced, provider=provider)
    try:
        async with application.router.lifespan_context(application):
            raise RuntimeError("operator_fence_ignored")
    except ShellRuntimeError as error:
        if error.code != "shell_admission_denied":
            raise
    assert marker.read_bytes() == before
    provider.confirm_none()
    report["cases"].append({"case": "enabled_startup_preserves_unknown_fence", "passed": True})

    enabled = settings_for(root, "enabled", enabled=True)
    migrated(enabled)
    application = create_app(enabled, provider=provider)
    runtime = None
    report["runtime_cleanup_confirmed"] = False
    try:
        async with application.router.lifespan_context(application):
            runtime = application.state.shell_runtime
            supervisor = application.state.shell_supervisor
            assert runtime is not None and len(application.state.shell_worker_registry.list()) == 1
            report["policy_digest"] = runtime.policy.digest
            report["operator_runtime_identity"] = runtime.identity
            assert application.state.runner.shell_supervisor is supervisor
            assert application.state.tasks.shell_supervisor is supervisor
            assert not any(
                "shell_run" in item.allowed_task_kinds
                for item in application.state.agent_registry.list_agents()
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://diagnostic.local",
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client:
                body = {
                    "kind": "shell_run",
                    "payload": {"script": SCRIPTS[0][1]},
                    "timeout_seconds": 2,
                }
                denied = await client.post(
                    "/tasks", json=body, headers={"Authorization": "Bearer invalid-diagnostic"}
                )
                assert denied.status_code == 401
                hostile = await client.post(
                    "/tasks",
                    json={**body, "payload": {**body["payload"], "env": {"HOST": "forbidden"}}},
                )
                assert hostile.status_code == 422
                assert (await client.get("/tasks")).json()["total"] == 0
                report["cases"].append(
                    {"case": "auth_and_native_override_rejected_without_tasks", "passed": True}
                )
                for index, (case, script, expected) in enumerate(SCRIPTS):
                    body = {
                        "kind": "shell_run",
                        "payload": {"script": script},
                        "timeout_seconds": 2,
                    }
                    headers = {"Idempotency-Key": "fixed-shell-operator-" + str(index)}
                    response = await client.post("/tasks", json=body, headers=headers)
                    assert response.status_code == 201
                    created = response.json()
                    task, evidence = validate_task(
                        await wait_task(client, created["id"]),
                        runtime,
                        supervisor,
                        script,
                        expected,
                    )
                    with application.state.database.read() as session:
                        persisted = session.get(Task, task.id)
                        assert persisted is not None
                        assert persisted.shell_spec == task.shell_spec.model_dump(mode="json")
                        assert persisted.action_hash == action_hash(
                            persisted.kind,
                            persisted.payload,
                            persisted.timeout_seconds,
                            persisted.action_version,
                            application.state.tasks._action_selection(persisted),
                        )
                    assert not runtime._pending() and admission_clear(runtime.admission_directory)
                    audit_response = await client.get("/tasks/" + task.id + "/events")
                    assert audit_response.status_code == 200
                    events = validate_audit(audit_response.json(), task)
                    replay = await client.post("/tasks", json=body, headers=headers)
                    assert replay.status_code == 200 and replay.json()["id"] == task.id
                    assert len(replay.json()["attempts"]) == 1
                    assert (await client.get("/tasks")).json()["total"] == index + 1
                    report["cases"].append(
                        {
                            "case": case,
                            "passed": True,
                            "task_id": task.id,
                            "persisted_evidence": evidence,
                            "audit_events": events,
                            "idempotency_single_attempt": True,
                        }
                    )
                provider.confirm_none()
    finally:
        if runtime is not None:
            assert runtime._closing and not runtime._pending()
            assert admission_clear(runtime.admission_directory)
            report["runtime_cleanup_confirmed"] = True
        report["model_generate_calls"] = provider.generate_calls
        report["model_inventory_calls"] = provider.models_calls
    report["cases"].append({"case": "application_runner_and_runtime_shutdown", "passed": True})
    provider.confirm_none()


def save_report(root, report):
    data = (
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    )
    if len(data) > REPORT_LIMIT:
        raise ValueError("operator_report_limit")
    path = root / "result.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return path


def qualify():
    if not __debug__:
        raise ValueError("operator_assertions_required")
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_directory(BASE)
    root = Path(tempfile.mkdtemp(prefix="operator-proof-", dir=BASE)).resolve()
    commit = command(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("operator_revision_invalid")
    report = {
        "status": "incomplete",
        "cases": [],
        "git_commit": commit,
        "worktree_clean": not bool(
            command(["/usr/bin/git", "-C", str(ROOT), "status", "--porcelain"])
        ),
        "live_core_enabled": False,
        "source_artifacts_are_independent_provenance": False,
        "external_network_used": False,
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        asyncio.run(exercise(root, report))
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
    report["finished_at"] = datetime.now(UTC).isoformat()
    path = save_report(root, report)
    return path, report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-native-shell-probes", action="store_true")
    args = parser.parse_args()
    if not args.allow_native_shell_probes:
        parser.error("explicit --allow-native-shell-probes is required")
    if not __debug__:
        parser.error("assertion-enabled Python is required")
    path, passed = qualify()
    print("EVIDENCE=" + str(path))
    if not passed:
        raise SystemExit("Shell operator qualification failed; inspect private report")


if __name__ == "__main__":
    main()
