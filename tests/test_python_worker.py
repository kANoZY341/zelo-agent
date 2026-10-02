"""v0.15 bounded Python Worker tests."""

import asyncio
from dataclasses import replace

import pytest

from app.models import Task
from app.python_worker import PythonRuntime, PythonRuntimeError
from app.runner import Runner
from app.schemas import PythonRunInput
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_python_worker
from tests.test_api import wait_for


def python_supervisor(worker_id: str = "python_core") -> Supervisor[PythonRuntime]:
    runtime = PythonRuntime()
    registry = WorkerRegistry[PythonRuntime](
        ((descriptor_for_python_worker(worker_id=worker_id), runtime),)
    )
    return Supervisor[PythonRuntime](registry)


def run_python(code: str, *, stdin: str = "", timeout: float = 2.0) -> dict:
    return asyncio.run(
        PythonRuntime().run(
            code=code,
            stdin=stdin,
            timeout_seconds=timeout,
            emit=lambda *_: None,
        )
    )


@pytest.mark.parametrize(
    ("code", "stdin", "timeout"),
    [
        ("", "", 1.0),
        ("print(1)", "x" * 65537, 1.0),
        ("print(1)", "", 0.0),
        ("print(1)", "", 61.0),
        ("print(1)", "", float("nan")),
    ],
)
def test_python_runtime_rejects_unbounded_direct_inputs(code, stdin, timeout):
    with pytest.raises(PythonRuntimeError, match="python_input_invalid"):
        run_python(code, stdin=stdin, timeout=timeout)


def test_python_runtime_executes_stdlib_and_stdin():
    result = run_python(
        "import math,sys\nvalue=int(sys.stdin.read())\nprint(math.factorial(value))",
        stdin="6",
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == "720\n"
    assert result["stderr"] == ""


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("open('/workspace/x','w').write('x')", "Read-only file system"),
        ("open('/tmp/x','w').write('x')", "Read-only file system"),
        ("open('/x','w').write('x')", "Read-only file system"),
        ("import os\nos.fork()", "Resource temporarily unavailable"),
        (
            "import socket\nsocket.create_connection(('1.1.1.1',80),timeout=0.2)",
            "Network is unreachable",
        ),
    ],
)
def test_python_runtime_denies_write_process_and_network(code, expected):
    result = run_python(code)
    assert result["exit_code"] != 0
    assert expected in result["stderr"]


def test_python_runtime_has_no_shell_binary():
    result = run_python("import os\nos.execv('/bin/sh',['sh','-c','echo unsafe'])")
    assert result["exit_code"] != 0
    assert "No such file or directory" in result["stderr"]


def test_python_runtime_has_no_host_workspace_site_packages_or_capabilities():
    result = run_python(
        "import os,sys\n"
        "status=open('/proc/self/status').read().splitlines()\n"
        "print(next(line for line in status if line.startswith('CapEff:')))\n"
        "print(os.path.exists('/etc/passwd'))\n"
        "print(os.listdir('/workspace'))\n"
        "print(os.uname().nodename)\n"
        "print(sys.path)"
    )
    assert result["exit_code"] == 0
    assert "CapEff:\t0000000000000000" in result["stdout"]
    assert "False" in result["stdout"]
    assert "[]" in result["stdout"]
    assert "zelo-python" in result["stdout"]
    assert "site-packages" not in result["stdout"]


def test_python_runtime_scrubs_environment_and_host_home():
    result = run_python(
        "import os\n"
        "print(sorted(os.environ))\n"
        "print(os.path.exists('/home'))\n"
        "print(os.environ.get('HOME'))"
    )
    assert result["exit_code"] == 0
    assert "ANTHROPIC" not in result["stdout"]
    assert "OPENAI" not in result["stdout"]
    assert "GITHUB" not in result["stdout"]
    assert "False" in result["stdout"]
    assert "/nonexistent" in result["stdout"]


def test_python_runtime_blocks_nested_user_namespaces():
    result = run_python(
        "import ctypes\n"
        "libc=ctypes.CDLL(None,use_errno=True)\n"
        "rc=libc.unshare(0x10000000)\n"
        "print(rc,ctypes.get_errno())"
    )
    assert result["exit_code"] == 0
    rc, error = (int(item) for item in result["stdout"].split())
    assert rc == -1
    assert error != 0


def test_python_runtime_enforces_memory_limit():
    result = run_python("bytearray(300 * 1024 * 1024)")
    assert result["exit_code"] != 0
    assert "MemoryError" in result["stderr"]


def test_python_runtime_enforces_timeout():
    with pytest.raises(PythonRuntimeError, match="python_timeout"):
        run_python("while True: pass", timeout=0.05)


def test_python_runtime_enforces_output_limit():
    with pytest.raises(PythonRuntimeError, match="python_output_limit"):
        run_python("print('x' * 70000)")


def test_python_submission_persists_exact_selection(settings, database):
    supervisor = python_supervisor()
    service = TaskService(database, settings, python_supervisor=supervisor)
    created, was_created = service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print(2 + 2)"}),
        None,
    )

    assert was_created is True
    assert created.python_spec is not None
    assert created.python_spec.descriptor.worker_id == "python_core"
    assert created.python_spec.descriptor.role == "python"
    assert created.python_spec.descriptor.capabilities == ("python_execution",)
    assert created.python_spec.authority_profile == "isolated-compute-no-network-v1"

    execution = service.claim()
    assert execution is not None
    assert execution.python_selection == created.python_spec


def test_python_worker_selection_fails_closed_if_registry_changes(settings, database):
    service = TaskService(database, settings, python_supervisor=python_supervisor())
    created, _ = service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print('safe')"}),
        None,
    )
    service.python_supervisor = python_supervisor("python_next")

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "python_worker_selection_invalid"


def test_python_retry_reselects_current_worker(settings, database):
    service = TaskService(database, settings, python_supervisor=python_supervisor())
    created, _ = service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print('safe')"}),
        None,
    )
    service.cancel(created.id)
    service.python_supervisor = python_supervisor("python_next")

    retried = service.retry(created.id)
    assert retried.action_version == 2
    assert retried.python_spec is not None
    assert retried.python_spec.descriptor.worker_id == "python_next"
    execution = service.claim()
    assert execution is not None
    assert execution.python_selection is not None
    assert execution.python_selection.descriptor.worker_id == "python_next"


def test_pre_v015_python_task_fails_closed_then_retry_rebinds(settings, database):
    service = TaskService(database, settings, python_supervisor=python_supervisor())
    created, _ = service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print('safe')"}),
        None,
    )
    with database.transaction() as session:
        persisted = session.get(Task, created.id)
        assert persisted is not None
        persisted.python_spec = None

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "python_worker_selection_invalid"

    retried = service.retry(created.id)
    assert retried.status == "pending"
    assert retried.python_spec is not None


def test_python_action_hash_binds_worker_selection(settings, database):
    service = TaskService(database, settings, python_supervisor=python_supervisor())
    created, _ = service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print('safe')"}),
        None,
    )
    with database.transaction() as session:
        persisted = session.get(Task, created.id)
        assert persisted is not None
        tampered = dict(persisted.python_spec)
        tampered["excluded_worker_ids"] = ["other_worker"]
        persisted.python_spec = tampered

    assert service.claim() is None
    cancelled = service.get(created.id)
    assert cancelled.status == "cancelled"
    assert cancelled.error == "action_changed"


def test_runner_requires_bound_python_worker_selection(settings, database):
    supervisor = python_supervisor()
    service = TaskService(database, settings, python_supervisor=supervisor)
    service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print('safe')"}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    assert execution.python_selection is not None
    runner = Runner(service, python_supervisor=supervisor)

    with pytest.raises(PythonRuntimeError, match="python_worker_selection_unavailable"):
        asyncio.run(runner._python_worker(replace(execution, python_selection=None)))


def test_runner_rejects_stale_python_worker_selection(settings, database):
    supervisor = python_supervisor()
    service = TaskService(database, settings, python_supervisor=supervisor)
    service.submit(
        PythonRunInput(kind="python_run", payload={"code": "print('safe')"}),
        None,
    )
    execution = service.claim()
    assert execution is not None
    runner = Runner(service, python_supervisor=python_supervisor("python_next"))

    with pytest.raises(PythonRuntimeError, match="python_worker_selection_stale"):
        asyncio.run(runner._python_worker(execution))


def test_python_api_executes_and_audits_selection(client):
    response = client.post(
        "/tasks",
        json={
            "kind": "python_run",
            "payload": {"code": "print(sum(range(5)))"},
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["python_spec"]["descriptor"]["worker_id"] == "python_core"
    assert created["python_spec"]["authority_profile"] == "isolated-compute-no-network-v1"

    completed = wait_for(client, created["id"], "succeeded")
    assert completed["result"]["stdout"] == "10\n"
    assert completed["result"]["exit_code"] == 0
    events = client.get(f"/tasks/{created['id']}/events").json()
    names = [item["event"] for item in events]
    assert "supervisor_worker_selected" in names
    assert "supervisor_worker_revalidated" in names
    assert "python_execution_started" in names
    assert "python_execution_completed" in names


def test_python_nonzero_exit_is_failed_with_bounded_result(client):
    response = client.post(
        "/tasks",
        json={"kind": "python_run", "payload": {"code": "raise ValueError('boom')"}},
    )
    assert response.status_code == 201, response.text
    failed = wait_for(client, response.json()["id"], "failed")
    assert failed["error"] == "python_exit_nonzero"
    assert failed["result"]["exit_code"] != 0
    assert "ValueError: boom" in failed["result"]["stderr"]


@pytest.mark.parametrize("field,value", [("worker_id", "attacker"), ("authority", ["network"])])
def test_python_api_cannot_select_worker_or_authority(field, value):
    with pytest.raises(ValueError):
        PythonRunInput(
            kind="python_run",
            payload={"code": "print('safe')", field: value},
        )
