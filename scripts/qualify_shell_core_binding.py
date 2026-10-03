"""Explicit fixed native Shell proof through isolated Core TaskService and Runner."""

import argparse
import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.policy import DomainError
from app.runner import Runner
from app.schemas import ShellRunInput
from app.shell_worker import ShellRuntime, ShellRuntimeError, configured_shell_runtime
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_shell_worker
from qualification.shell_owner import ShellOwner
from scripts.qualify_browser_renderer import command
from scripts.qualify_shell_owner import private_directory, wait_spawn

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "runtime/shell-core-binding"


class QualificationSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


async def exercise(root, report):
    import app.shell_worker as adapter
    import qualification.shell_owner as boundary

    admission = root / "admission"
    core = root / "core"
    for directory in (admission, core):
        directory.mkdir(mode=0o700)
    settings = QualificationSettings(
        _env_file=None,
        operator_token="diagnostic-placeholder-not-a-credential",
        runtime_dir=core,
        scheduler_enabled=False,
        shell_worker_enabled=True,
        shell_worker_admission=admission,
    )
    runtime = configured_shell_runtime(settings)
    assert runtime is not None
    report["policy_digest"] = runtime.policy.digest
    report["operator_runtime_identity"] = runtime.identity
    selected = Supervisor(WorkerRegistry(((descriptor_for_shell_worker(), runtime),)))
    database = Database(settings.database_path)
    database.migrate()
    service = TaskService(database, settings, shell_supervisor=selected)
    runner = Runner(service, shell_supervisor=selected)
    owners, operations = [], []
    original_owner, original_handle = adapter.ShellOwner, boundary._ShellHandle

    class ObservedOwner(ShellOwner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owners.append(self)

    adapter.ShellOwner = ObservedOwner

    def start(script, timeout=2.0):
        task, created = service.submit(
            ShellRunInput(kind="shell_run", payload={"script": script}, timeout_seconds=timeout),
            None,
        )
        assert created and task.shell_spec.policy_version == runtime.policy.digest
        assert task.shell_spec.operator_identity == runtime.identity
        execution = service.claim()
        assert execution is not None and execution.shell_request == task.shell_spec
        operation = asyncio.create_task(runner._run(execution))
        runner.operations[task.id] = operation
        operations.append(operation)
        return task.id, operation

    def clean():
        assert not runtime._pending()
        assert (admission / "active.marker").read_bytes() == b""
        for owner in owners:
            assert owner.cleanup_confirmed
            handle = getattr(owner, "_handle", None)
            if handle is not None:
                assert handle._resources.cleanup_confirmed
                process = handle._resources.native._process
                if process is not None:
                    assert process.reaped and process._pidfd is None

    def record(case, task_id):
        task = service.get(task_id)
        report["cases"].append(
            {
                "case": case,
                "passed": True,
                "task_id": task_id,
                "status": task.status,
                "error": task.error,
            }
        )

    try:
        for name, script, expected in (
            ("built_in_compute", "n=6; printf 'CORE_SHELL_%s' \"$((n * 7))\"\n", "CORE_SHELL_42"),
            (
                "ambient_settings_and_credentials_absent",
                'test -z "${ZELO_OPERATOR_TOKEN-}${ENV-}${LD_PRELOAD-}${DOCKER_HOST-}" '
                "&& printf CLEAN\n",
                "CLEAN",
            ),
        ):
            task_id, operation = start(script)
            await operation
            task = service.get(task_id)
            assert task.status == "succeeded" and task.result["stdout"] == expected
            assert task.result["instruction_trust"] == "none" and task.result["stderr"] == ""
            clean()
            record(name, task_id)
        for name, script, timeout, error in (
            ("nonzero_exit", "exit 7\n", 2.0, "shell_exit_nonzero"),
            ("absolute_deadline", "while :; do :; done\n", 0.2, "shell_timeout"),
            (
                "bounded_output",
                "while :; do printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'; done\n",
                2.0,
                "shell_output_limit",
            ),
            ("read_only_guest", ": >/tmp/new-file\n", 2.0, "shell_exit_nonzero"),
        ):
            task_id, operation = start(script, timeout)
            await operation
            task = service.get(task_id)
            assert task.status == "failed" and task.error == error
            if error != "shell_exit_nonzero":
                assert task.result is None
            if name == "read_only_guest":
                assert "Read-only file system" in task.result["stderr"]
            clean()
            record(name, task_id)
        task_id, operation = start("while :; do :; done\n")
        async with asyncio.timeout(1):
            while not owners or not runtime._owners:
                await asyncio.sleep(0.005)
        process = await wait_spawn(owners[-1])
        service.cancel(task_id)
        operation.cancel()
        await operation
        task = service.get(task_id)
        assert task.status == "cancelled" and task.result is None and process.reaped
        clean()
        record("operator_cancel_reaps_native_child", task_id)
        task_id, operation = start("printf READMITTED\n")
        await operation
        assert service.get(task_id).result["stdout"] == "READMITTED"
        clean()
        record("fresh_readmission_after_terminal_cleanup", task_id)
        report["reaped_launchers"] = len(owners)
        report["native_cleanup_confirmed"] = True
    finally:
        for operation in operations:
            if not operation.done():
                operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
        try:
            await runtime.aclose()
        finally:
            database.close()
            adapter.ShellOwner = original_owner
            boundary._ShellHandle = original_handle
    # Lost trusted cleanup acknowledgement after genuine native settlement retains
    # the durable fence. No marker reset or inferred repair is performed.
    fenced = root / "fenced"
    fenced.mkdir(mode=0o700)
    uncertain = ShellRuntime(fenced)
    selected = Supervisor(WorkerRegistry(((descriptor_for_shell_worker(), uncertain),)))
    database = Database(root / "fenced-core.sqlite3")
    database.migrate()
    service = TaskService(database, settings, shell_supervisor=selected)

    class LostAcknowledgement(original_handle):
        async def stop_and_confirm_cleanup(self):
            assert await super().stop_and_confirm_cleanup() is True
            return False

    boundary._ShellHandle = LostAcknowledgement
    try:
        task, _ = service.submit(
            ShellRunInput(kind="shell_run", payload={"script": "printf TENTATIVE\n"}), None
        )
        execution = service.claim()
        await Runner(service, shell_supervisor=selected)._run(execution)
        finished = service.get(task.id)
        assert finished.status == "failed" and finished.error == "shell_cleanup_unproven"
        assert finished.result is None and uncertain._pending()
        assert (fenced / "active.marker").stat().st_size > 0
        with_denial = False
        try:
            service.submit(ShellRunInput(kind="shell_run", payload={"script": "true\n"}), None)
        except DomainError:
            with_denial = True
        assert with_denial
        try:
            await uncertain.aclose()
        except ShellRuntimeError as error:
            assert error.code == "shell_cleanup_unproven"
        else:
            raise AssertionError("unknown cleanup accepted")
        report["cases"].append(
            {"case": "lost_native_cleanup_ack_retains_fence_and_no_result", "passed": True}
        )
    finally:
        boundary._ShellHandle = original_handle
        database.close()


def qualify():
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_directory(BASE)
    root = Path(tempfile.mkdtemp(prefix="core-proof-", dir=BASE)).resolve()
    report = {
        "status": "incomplete",
        "cases": [],
        "started_at": datetime.now(UTC).isoformat(),
        "live_core_enabled": False,
        "external_network_used": False,
        "git_commit": command(["/usr/bin/git", "-C", str(REPO), "rev-parse", "HEAD"]).strip(),
        "worktree_clean": not bool(
            command(["/usr/bin/git", "-C", str(REPO), "status", "--porcelain"])
        ),
    }
    try:
        asyncio.run(exercise(root, report))
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
    report["finished_at"] = datetime.now(UTC).isoformat()
    data = json.dumps(report, indent=2).encode() + b"\n"
    assert len(data) <= 131072
    path = root / "result.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return path, report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-native-shell-probes", action="store_true")
    args = parser.parse_args()
    if not args.allow_native_shell_probes:
        parser.error("explicit --allow-native-shell-probes is required")
    path, passed = qualify()
    print(f"EVIDENCE={path}")
    if not passed:
        raise SystemExit("native Shell Core qualification failed; inspect private report")


if __name__ == "__main__":
    main()
