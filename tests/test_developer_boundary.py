"""Real Linux namespaces and Git/process tests. No Codex model/account service calls."""

import asyncio
import importlib.util
import json
import marshal
import os
import py_compile
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from app.codex_worker import CodexCliWorker
from app.config import ROOT
from app.developer import DeveloperError, DeveloperRuntime, tree_digest
from app.developer_artifacts import pin_executable
from app.developer_process import environment, run_process
from app.developer_sandbox import VENV, WORKER_EXECUTABLE, external_worker_sandbox, sandbox
from app.developer_validation import python_command
from app.policy import State
from app.runner import Runner
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from app.worker_authority import authority_profile
from tests.developer_helpers import fixture_commit, git

pytestmark = pytest.mark.developer_boundary


@pytest.fixture(autouse=True)
def linux_boundary_required():
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Linux Bubblewrap prerequisite not installed")


def spec(runtime, timeout=60):
    return runtime.request("Make a harmless change", "zelo_core", timeout)


def prepared(runtime):
    request = spec(runtime)
    repo = runtime.path(str(uuid4()))
    repo.parent.mkdir(parents=True)
    asyncio.run(runtime.prepare(repo, request))
    return repo, request


def test_local_presmoke_command_and_validation_path(developer_case):
    """Exercise the real local command sandbox without a Codex model request."""
    if not developer_case.settings.developer_codex_executable.is_file():
        pytest.skip("Installed Codex required for local non-model sandbox probe")
    before_head = git(ROOT, "rev-parse", "HEAD")
    before_status = git(ROOT, "status", "--porcelain=v1", "-z")
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="presmoke-", dir=ROOT / "workspaces") as directory:
        workspace = Path(directory) / "workspaces"
        workspace.mkdir()
        config = developer_case.settings.model_copy(update={"workspace_root": workspace})
        worker = CodexCliWorker(config)
        runtime = DeveloperRuntime(config, worker, developer_case.source_repository)
        request = runtime.request("Create only DEVELOPER_SMOKE.txt", "zelo_core", 60)
        repo = runtime.path(str(uuid4()))
        repo.parent.mkdir(parents=True)
        asyncio.run(runtime.prepare(repo, request))
        with worker.bind(request.worker) as bound:
            asyncio.run(
                bound.local_command_probe(
                    repo,
                    config.developer_codex_auth_file,
                    authority_profile(("workspace-write",)),
                    ROOT,
                )
            )
        assert (repo / "DEVELOPER_SMOKE.txt").read_bytes() == b"Zelo developer worker online\n"
        # The real worker left these empty project directories in the failed smoke.
        # Validation must not interpret them as executable worker configuration.
        (repo / ".codex").mkdir(exist_ok=True)
        (repo / ".agents").mkdir(exist_ok=True)
        validations = asyncio.run(runtime.validate(repo, request))
        assert len(validations) == 4
        assert all(item.exit_code == 0 for item in validations), validations
        review = asyncio.run(runtime.review(repo, request))
        assert "DEVELOPER_SMOKE.txt" in review["changed_files"]
        assert review["ignored_generated_files"]
        assert git(repo, "rev-parse", "HEAD") == request.base_sha
        assert git(repo, "remote") == ""
        assert git(repo, "status", "--porcelain=v1") == "?? DEVELOPER_SMOKE.txt"
    assert git(ROOT, "rev-parse", "HEAD") == before_head
    assert git(ROOT, "status", "--porcelain=v1", "-z") == before_status


def test_clone_objects_not_hardlinked(developer_case):
    repo, _ = prepared(developer_case)

    def inodes(root):
        return {(p.stat().st_dev, p.stat().st_ino) for p in root.rglob("*") if p.is_file()}

    assert not inodes(repo / ".git/objects") & inodes(
        developer_case.source_repository / ".git/objects"
    )


def test_review_rejects_non_utf8_filename(developer_case):
    repo, request = prepared(developer_case)
    descriptor = os.open(os.fsencode(repo) + b"/invalid-\xff", os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with pytest.raises(DeveloperError, match="review_inventory_failed"):
        asyncio.run(developer_case.review(repo, request))


def test_validation_venv_alias_is_trusted_and_symlink_rejected(developer_case):
    repo, _ = prepared(developer_case)
    result = asyncio.run(
        run_process(
            sandbox(
                repo,
                ["/workspace/.venv/bin/python", "-I", "-c", "import pytest, ruff"],
                venv=ROOT / ".venv",
            ),
            cwd=repo,
            timeout=5,
        )
    )
    assert result.exit_code == 0
    # Bubblewrap creates missing mountpoints only in its namespace.
    if (repo / ".venv").exists():
        (repo / ".venv").rmdir()
    (repo / ".venv").symlink_to(ROOT / ".venv", target_is_directory=True)
    with pytest.raises(DeveloperError, match="validation_environment_invalid"):
        sandbox(repo, ["/bin/true"], venv=ROOT / ".venv")


def test_full_runtime_result_review_and_audit(developer_case, database):
    runtime = developer_case
    service = TaskService(database, runtime.settings, developer_runtime=runtime)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    assert service.claim() is None
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))
    result = service.get(task.id)
    assert result.status == State.SUCCEEDED, result.model_dump()
    data = result.result
    assert data["worktree"].startswith(execution.attempt_id)
    assert "DEVELOPER_SMOKE.txt" in data["diff"]
    assert "DEVELOPER_SMOKE.txt" in data["changed_files"]
    assert data["ignored_generated_files"]  # Validator cache files are inventoried too.
    assert len(data["validation"]) == 4
    assert all(
        datetime.fromisoformat(data[key]).tzinfo is not None
        for key in ("worker_completed_at", "validation_completed_at", "completed_at")
    )
    assert data["duration_seconds"] > 0
    repo = runtime.root() / data["worktree"]
    assert repo != runtime.source_repository and (repo / ".git").is_dir()
    assert git(repo, "rev-parse", "HEAD") == data["base_sha"]
    assert git(repo, "remote") == ""
    assert not (repo / ".git/objects/info/alternates").exists()
    events = service.events(task.id, 100, 0)
    names = [event.event for event in events if event.event.startswith("developer_")]
    assert names == [
        "developer_started",
        "developer_workspace_created",
        "developer_worker_started",
        "developer_worker_completed",
        "developer_validation_started",
        "developer_validation_completed",
        "developer_completed",
    ]
    capacity = [event.event for event in events if event.event.startswith("worker_capacity_")]
    assert capacity == ["worker_capacity_claimed", "worker_capacity_released"]


def test_interrupted_developer_recovery_does_not_invent_capacity_release(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    assert service.claim() is not None
    service.recover()
    events = [event.event for event in service.events(task.id, 100, 0)]
    assert "worker_capacity_claimed" not in events
    assert "worker_capacity_released" not in events


def test_interrupted_developer_recovery_releases_only_claimed_capacity(developer_case, database):
    service = TaskService(database, developer_case.settings, developer_runtime=developer_case)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None and execution.developer_request is not None
    descriptor = execution.developer_request.worker_selection.descriptor
    service.model_event(
        task.id,
        "worker_capacity_claimed",
        execution.attempt_id,
        worker_id=descriptor.worker_id,
        max_concurrency=descriptor.max_concurrency,
    )
    service.recover()
    events = [event.event for event in service.events(task.id, 100, 0)]
    assert events.count("worker_capacity_claimed") == 1
    assert events.count("worker_capacity_released") == 1


def test_sandbox_private_files_environment_and_network(developer_case, monkeypatch):
    runtime = developer_case
    repo, request = prepared(runtime)
    source = runtime.source_repository
    secrets = [source / ".env", source / "secrets/token", source / "runtime/zelo.sqlite3"]
    for path in secrets:
        path.parent.mkdir(exist_ok=True)
        path.write_text("harmless-private-sentinel")
    variables = [
        "ZELO_OPERATOR_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "PYTHONPATH",
        "PYTEST_PLUGINS",
    ]
    for key in variables:
        monkeypatch.setenv(key, "harmless-environment-sentinel")
    paths = [str(p) for p in secrets] + [
        str(ROOT / ".env"),
        "/home/zelo/.ssh",
        "/var/run/docker.sock",
    ]
    code = f"""import json, os, socket
from pathlib import Path
assert all(not Path(p).exists() for p in {paths!r})
assert all(key not in os.environ for key in {variables!r})
assert os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
assert [n for _,n in socket.if_nameindex()] == ['lo']
Path('boundary-write.txt').write_text('safe')
try:
    Path('.git/config').open('a').close()
except OSError:
    pass
else:
    raise AssertionError('Git metadata writable')
print('BOUNDARY_OK')
"""
    result = asyncio.run(
        run_process(
            sandbox(repo, [VENV + "/bin/python", "-I", "-c", code], venv=ROOT / ".venv"),
            cwd=repo,
            timeout=10,
        )
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout == b"BOUNDARY_OK\n"
    assert (repo / "boundary-write.txt").read_text() == "safe"


def test_real_codex_command_profile_hides_fake_auth(developer_case):
    runtime = developer_case
    binary = runtime.settings.developer_codex_executable
    if not binary.exists():
        pytest.skip("Installed Codex required for local non-model sandbox probe")
    repo, _ = prepared(runtime)
    auth = runtime.settings.workspace_root / "fake-auth.json"
    auth.write_text("harmless-fake-auth-sentinel")
    worker = CodexCliWorker(runtime.settings)
    assert worker.identity().cli_version == "codex-cli 0.155.1"
    asyncio.run(worker.probe(repo, auth, authority_profile(("workspace-write",))))


@pytest.mark.parametrize("module", ["ruff", "pytest"])
def test_validation_tools_not_shadowed(developer_case, module, monkeypatch):
    repo, _ = prepared(developer_case)
    (repo / f"{module}.py").write_text("raise RuntimeError('SHADOW_MODULE_EXECUTED')\n")
    monkeypatch.setenv("PYTEST_PLUGINS", "unapproved_plugin")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--this-flag-must-not-be-used")
    monkeypatch.setenv("PYTHONPATH", str(repo))
    args = ["--version"] if module == "ruff" else ["-q", "test_example.py"]
    result = asyncio.run(
        run_process(
            sandbox(repo, [VENV + "/bin/python", "-I", "-m", module, *args], venv=ROOT / ".venv"),
            cwd=repo,
            timeout=10,
        )
    )
    assert result.exit_code == 0, result.stderr
    assert b"SHADOW_MODULE_EXECUTED" not in result.stdout + result.stderr


def test_validation_uses_source_not_excluded_cached_bytecode(developer_case):
    repo, _ = prepared(developer_case)
    source = repo / "cached_validation_module.py"
    source.write_text("VALUE = 'source'\n")
    cache = Path(importlib.util.cache_from_source(source))
    py_compile.compile(source, cfile=cache, doraise=True)
    cache.write_bytes(
        cache.read_bytes()[:16]
        + marshal.dumps(compile("VALUE = 'unbound bytecode'\n", str(source), "exec"))
    )
    code = (
        "import sys; sys.path.insert(0, '/workspace'); "
        "import cached_validation_module; "
        "assert cached_validation_module.VALUE == 'source', cached_validation_module.VALUE"
    )
    result = asyncio.run(
        run_process(
            sandbox(repo, python_command("-c", code), venv=ROOT / ".venv"),
            cwd=repo,
            timeout=10,
        )
    )
    assert result.exit_code == 0, result.stderr


def test_external_worker_git_inspection_ignores_repository_program_config(developer_case):
    repo, _ = prepared(developer_case)
    marker = repo / "EXTERNAL_GIT_CONFIG_EXECUTED"
    callback = f"/usr/bin/touch {marker}"
    git(repo, "config", "core.fsmonitor", callback)

    with pin_executable(Path("/usr/bin/git")) as artifact:
        result = asyncio.run(
            run_process(
                external_worker_sandbox(
                    repo,
                    [WORKER_EXECUTABLE, "status", "--short"],
                    worker_fd=artifact.fd,
                    writable=True,
                    service_network=False,
                ),
                cwd=repo,
                timeout=5,
                pass_fds=(artifact.fd,),
            )
        )

    assert result.exit_code == 0, result.stderr
    assert not marker.exists()


def test_git_config_programs_cannot_execute(developer_case):
    repo, request = prepared(developer_case)
    marker = developer_case.settings.workspace_root / "HOST_COMMAND_EXECUTED"
    callback = f"/usr/bin/touch {marker}"
    for key in ("core.fsmonitor", "diff.external", "diff.attack.textconv", "filter.attack.clean"):
        git(repo, "config", key, callback)
    (repo / ".gitattributes").write_text("*.py diff=attack filter=attack\n")
    (repo / "test_example.py").write_text("def test_example():\n    assert 1 == 1\n")
    result = asyncio.run(developer_case.review(repo, request))
    assert "test_example.py" in result["changed_files"]
    assert not marker.exists()


def test_review_staged_unstaged_untracked_and_names(developer_case):
    repo, request = prepared(developer_case)
    staged = 'staged space "é".txt'
    untracked = "new\nline.txt"
    (repo / staged).write_text("staged content\n")
    git(repo, "add", "--", staged)
    (repo / "test_example.py").write_text("def test_example():\n    assert False\n")
    (repo / untracked).write_text("untracked content\n")
    result = asyncio.run(developer_case.review(repo, request))
    assert set(result["changed_files"]) == {staged, untracked, "test_example.py"}
    for content in ("staged content", "untracked content", "assert False"):
        assert content in result["diff"]
    assert "\0" in result["git_status"]


def test_head_change_is_detected_without_primary_mutation(developer_case):
    runtime = developer_case
    request = spec(runtime)
    source_before = tree_digest(runtime.source_repository / ".git")

    async def changed_head(request, repo):
        # Simulate a compromised alternate worker. Real sandbox mounts .git read-only.
        (repo / "new.txt").write_text("fixture change\n")
        await asyncio.to_thread(fixture_commit, repo)

    runtime.worker.action = changed_head
    events = []
    result = asyncio.run(
        runtime.run(
            str(uuid4()), str(uuid4()), request, lambda event, details: events.append(event)
        )
    )
    assert result["error"] == "developer_boundary_violation"
    assert result["head_changed"] is True
    assert "developer_boundary_violation" in events
    assert tree_digest(runtime.source_repository / ".git") == source_before


def test_concurrent_primary_snapshots_do_not_mutate_source_index(developer_case):
    runtime = developer_case
    request = spec(runtime)
    index = runtime.source_repository / ".git" / "index"
    before = index.read_bytes()
    before_digest = tree_digest(index)
    before_mtime = index.stat().st_mtime_ns
    assert environment()["GIT_OPTIONAL_LOCKS"] == "0"

    async def exercise():
        first, second = await asyncio.gather(
            runtime.primary_snapshot(request),
            runtime.primary_snapshot(request),
        )
        assert first == second

    asyncio.run(exercise())
    assert index.read_bytes() == before
    assert tree_digest(index) == before_digest
    assert index.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("failure", ["exception", "timeout", "cancel", "success"])
def test_boundary_verification_on_all_exits(developer_case, failure):
    runtime = developer_case
    timeout_seconds = 30 if failure == "timeout" else 20
    runtime.settings.developer_timeout_seconds = timeout_seconds
    request = spec(runtime, timeout_seconds + 10 if failure == "timeout" else 20)

    async def run():
        ready = asyncio.Event()

        async def action(request, repo):
            (runtime.source_repository / "test_example.py").write_text(
                "# unauthorized fixture edit\n"
            )
            ready.set()
            if failure == "exception":
                raise DeveloperError("injected_failure")
            if failure in {"timeout", "cancel"}:
                await asyncio.sleep(30)

        runtime.worker.action = action
        task = asyncio.create_task(
            runtime.run(str(uuid4()), str(uuid4()), request, lambda *args: None)
        )
        ready_wait = asyncio.create_task(ready.wait())
        done, _ = await asyncio.wait(
            {ready_wait, task}, timeout=timeout_seconds + 5, return_when=asyncio.FIRST_COMPLETED
        )
        if ready_wait not in done:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.gather(ready_wait, return_exceptions=True)
            pytest.fail("runtime ended before the worker reached the boundary check")
        if failure == "cancel":
            task.cancel()
        return await task

    result = asyncio.run(run())
    assert result["error"] == "developer_boundary_violation"
    assert not result["validation_passed"]
    assert (
        result["original_error"]
        == {
            "exception": "injected_failure",
            "timeout": "developer_overall_timeout",
            "cancel": "developer_cancelled",
            "success": None,
        }[failure]
    )


@pytest.mark.parametrize(
    "mode", ["timeout", "cancel", "repeated_cancel", "setsid_timeout", "setsid_cancel"]
)
def test_actual_launcher_kills_descendants(developer_case, monkeypatch, mode):
    repo, _ = prepared(developer_case)
    from app import developer_process

    original = developer_process.spawn_owned_process
    processes = []

    async def record(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(developer_process, "spawn_owned_process", record)
    code = """import os, signal, time
from pathlib import Path
if os.fork() == 0:
    ESCAPE_SESSION
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path('ready').touch()
    time.sleep(30)
else:
    time.sleep(30)
"""
    code = code.replace("ESCAPE_SESSION", "os.setsid()" if mode.startswith("setsid") else "pass")

    def descendants(pid):
        result = []
        path = Path(f"/proc/{pid}/task/{pid}/children")
        if path.exists():
            for child in path.read_text().split():
                result += [int(child), *descendants(int(child))]
        return result

    async def exercise():
        task = asyncio.create_task(
            run_process(sandbox(repo, ["/usr/bin/python3", "-I", "-c", code]), cwd=repo, timeout=1)
        )
        deadline = time.monotonic() + 5
        while not (repo / "ready").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        pids = [processes[0].pid, *descendants(processes[0].pid)]
        assert len(pids) >= 3
        if mode not in {"timeout", "setsid_timeout"}:
            task.cancel()
            if mode == "repeated_cancel":
                await asyncio.sleep(0)
                task.cancel()
        with pytest.raises((DeveloperError, asyncio.CancelledError)):
            await task
        for pid in pids:
            path = Path(f"/proc/{pid}/stat")
            assert not path.exists() or path.read_text().split()[2] == "Z"

    asyncio.run(exercise())


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_stream_overflow_terminates_process(developer_case, stream_name):
    repo, _ = prepared(developer_case)
    code = f"import sys; sys.{stream_name}.write('x'*100000); sys.{stream_name}.flush()"
    with pytest.raises(DeveloperError, match="output_limit"):
        asyncio.run(
            run_process(
                sandbox(repo, ["/usr/bin/python3", "-I", "-c", code]),
                cwd=repo,
                timeout=5,
                stdout_limit=1024,
                stderr_limit=1024,
            )
        )


def test_retry_gets_new_checkout_and_cleanup_is_owned(developer_case, database):
    runtime = developer_case
    service = TaskService(database, runtime.settings, developer_runtime=runtime)

    async def fail(request, repo):
        raise DeveloperError("fake_failure")

    runtime.worker.action = fail
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    runner = Runner(service, developer_runtime=runtime)
    service.decide(task.approvals[0].id, "approve")
    first = service.claim()
    asyncio.run(runner._run(first))
    first_path = service.get(task.id).result["worktree"]
    runtime.worker.action = None
    retry = service.retry(task.id)
    assert retry.status == State.AWAITING_APPROVAL
    service.decide(retry.approvals[-1].id, "approve")
    second = service.claim()
    asyncio.run(runner._run(second))
    final = service.get(task.id)
    assert final.status == State.SUCCEEDED, final.error
    assert first.attempt_id != second.attempt_id
    assert first_path != final.result["worktree"]
    assert (runtime.root() / first_path).exists()
    with runtime.lock(), pytest.raises(DeveloperError, match="busy"):
        runtime.cleanup(first.attempt_id)
    runtime.cleanup(first.attempt_id)
    assert not (runtime.root() / first_path).exists()
    assert (runtime.root() / final.result["worktree"]).exists()


@pytest.mark.parametrize("case", ["active", "symlink", "unowned"])
def test_cleanup_rejects_unsafe_targets(developer_case, case):
    runtime = developer_case
    execution = str(uuid4())
    repo = runtime.path(execution)
    repo.parent.mkdir(parents=True)
    if case == "symlink":
        repo.symlink_to(runtime.source_repository)
    else:
        repo.mkdir()
    (repo.parent / "owner.json").write_text(
        json.dumps(
            {
                "task": str(uuid4()),
                "execution": execution if case != "unowned" else "other",
                "active": case == "active",
            }
        )
    )
    with pytest.raises(DeveloperError):
        runtime.cleanup(execution)
    assert runtime.source_repository.exists()


@pytest.mark.parametrize("case", ["failure", "timeout", "overflow"])
def test_validation_failures_are_authoritative(developer_case, case):
    runtime = developer_case
    if case == "timeout":
        runtime.settings.developer_validation_timeout_seconds = 1
    repo, request = prepared(runtime)
    code = {
        "failure": "def test_example():\n    assert False\n",
        "timeout": "import time\n\n\ndef test_example():\n    time.sleep(30)\n",
        "overflow": 'def test_example():\n    raise RuntimeError("x" * 100000)\n',
    }[case]
    (repo / "test_example.py").write_text(code)
    results = asyncio.run(runtime.validate(repo, request))
    assert results[-1].identifier == "pytest"
    assert results[-1].exit_code != 0
    if case != "failure":
        assert results[-1].error == (
            "process_timeout" if case == "timeout" else "process_output_limit"
        )


def test_runtime_validation_failure_persists_failed_result(developer_case, database):
    runtime = developer_case

    async def fail_test(request, repo):
        (repo / "test_example.py").write_text("def test_example():\n    assert False\n")

    runtime.worker.action = fail_test
    service = TaskService(database, runtime.settings, developer_runtime=runtime)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "x"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    asyncio.run(Runner(service, developer_runtime=runtime)._run(service.claim()))
    result = service.get(task.id)
    assert result.status == State.FAILED
    assert result.result["worker_exit_code"] == 0
    assert not result.result["validation_passed"]
    assert "assert False" in result.result["diff"]
    names = [e.event for e in service.events(task.id, 100, 0)]
    assert names[-1] == "developer_failed" and "developer_completed" not in names


def test_boundary_violation_preserves_validation_failure(developer_case):
    runtime = developer_case
    request = spec(runtime)

    async def fail_validation(request, repo):
        (runtime.source_repository / "test_example.py").write_text("# unauthorized fixture edit\\n")
        (repo / "test_example.py").write_text("def test_example():\\n    assert False\\n")

    runtime.worker.action = fail_validation
    result = asyncio.run(runtime.run(str(uuid4()), str(uuid4()), request, lambda *args: None))
    assert result["error"] == "developer_boundary_violation"
    assert result["boundary_error"] == "developer_boundary_violation"
    assert result["original_error"] == "developer_validation_failed"


@pytest.mark.parametrize("limit", ["diff", "files"])
def test_review_limits(developer_case, limit):
    repo, request = prepared(developer_case)
    (repo / "one.txt").write_text("x" * 5000)
    (repo / "two.txt").write_text("y")
    values = {"diff_bytes": 1024} if limit == "diff" else {"changed_files": 1}
    request = request.model_copy(update={"limits": request.limits.model_copy(update=values)})
    with pytest.raises(DeveloperError):
        asyncio.run(developer_case.review(repo, request))


@pytest.mark.parametrize("limit", ["line", "events"])
def test_stream_jsonl_limits_stop_process(developer_case, limit):
    repo, _ = prepared(developer_case)
    code = "print('x'*10000)" if limit == "line" else "print('{}\\n'*30)"
    with pytest.raises(DeveloperError, match="jsonl_"):
        asyncio.run(
            run_process(
                sandbox(repo, ["/usr/bin/python3", "-I", "-c", code]),
                cwd=repo,
                timeout=5,
                line_limit=1024,
                event_limit=10,
            )
        )


def test_overall_deadline_includes_preparation(developer_case, monkeypatch):
    runtime = developer_case
    runtime.settings.developer_timeout_seconds = 1
    request = spec(runtime)

    async def slow_prepare(*args):
        await asyncio.sleep(30)

    monkeypatch.setattr(runtime, "prepare", slow_prepare)
    started = time.monotonic()
    result = asyncio.run(runtime.run(str(uuid4()), str(uuid4()), request, lambda *args: None))
    assert result["error"] == "developer_overall_timeout"
    assert time.monotonic() - started < 5
    assert runtime.worker.calls == []


def test_nonzero_worker_exit_and_audit(developer_case):
    runtime = developer_case
    request = spec(runtime)
    original = runtime.worker.run

    async def nonzero(*args):
        result = await original(*args)
        return result.model_copy(update={"exit_code": 7})

    runtime.worker.run = nonzero
    names = []
    result = asyncio.run(
        runtime.run(str(uuid4()), str(uuid4()), request, lambda name, details: names.append(name))
    )
    assert result["error"] == "worker_nonzero_exit" and result["worker_exit_code"] == 7
    assert "developer_worker_failed" in names
    assert "developer_validation_started" not in names
