"""Bounded isolated Python compute worker for v0.15."""

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import developer_sandbox
from app.developer_process import DeveloperError, run_process
from app.schemas import PythonRunResult

PYTHON_COMPUTE_POLICY_SCHEMA = "zelo-python-compute-v1"
PYTHON_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
PYTHON_NOFILE_LIMIT = 32
PYTHON_PROCESS_LIMIT = 0
PYTHON_STDOUT_LIMIT = 65536
PYTHON_STDERR_LIMIT = 16384
PYTHON_CPU_MARGIN_SECONDS = 1
PythonEventSink = Callable[[str, dict[str, Any]], None]


class PythonRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


PYTHON_BOOTSTRAP = (
    "import io\n"
    "import json\n"
    "import math\n"
    "import resource\n"
    "import sys\n"
    "payload = json.loads(sys.stdin.read())\n"
    "timeout = float(sys.argv[1])\n"
    f"cpu = max(1, min(61, math.ceil(timeout) + {PYTHON_CPU_MARGIN_SECONDS}))\n"
    f"resource.setrlimit(resource.RLIMIT_AS, ({PYTHON_MEMORY_LIMIT_BYTES}, "
    f"{PYTHON_MEMORY_LIMIT_BYTES}))\n"
    "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
    "resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))\n"
    f"resource.setrlimit(resource.RLIMIT_NOFILE, ({PYTHON_NOFILE_LIMIT}, {PYTHON_NOFILE_LIMIT}))\n"
    f"resource.setrlimit(resource.RLIMIT_NPROC, ({PYTHON_PROCESS_LIMIT}, {PYTHON_PROCESS_LIMIT}))\n"
    "resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))\n"
    'source = payload["code"]\n'
    'sys.stdin = io.StringIO(payload.get("stdin", ""))\n'
    'sys.argv = ["<zelo-python>"]\n'
    'namespace = {"__name__": "__main__", "__file__": "<zelo-python>"}\n'
    'exec(compile(source, "<zelo-python>", "exec"), namespace, namespace)\n'
)


def python_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }


def python_policy_semantics() -> dict[str, object]:
    return {
        "schema": PYTHON_COMPUTE_POLICY_SCHEMA,
        "sandbox": developer_sandbox.python_compute_sandbox_plan(),
        "interpreter_flags": ("-I", "-S"),
        "bootstrap_sha256": hashlib.sha256(PYTHON_BOOTSTRAP.encode("utf-8")).hexdigest(),
        "limits": {
            "memory_bytes": PYTHON_MEMORY_LIMIT_BYTES,
            "open_files": PYTHON_NOFILE_LIMIT,
            "processes": PYTHON_PROCESS_LIMIT,
            "stdout_bytes": PYTHON_STDOUT_LIMIT,
            "stderr_bytes": PYTHON_STDERR_LIMIT,
            "cpu_margin_seconds": PYTHON_CPU_MARGIN_SECONDS,
        },
        "environment": python_environment(),
    }


def _runtime_error(error: DeveloperError) -> PythonRuntimeError:
    code = error.args[0] if error.args else "python_worker_error"
    mapped = {
        "bubblewrap_unavailable": "python_sandbox_unavailable",
        "executable_unavailable": "python_runtime_unavailable",
        "process_timeout": "python_timeout",
        "process_output_limit": "python_output_limit",
        "process_reap_failed": "python_cleanup_failed",
        "process_group_survived": "python_cleanup_failed",
        "process_pipe_close_failed": "python_cleanup_failed",
        "python_runtime_unavailable": "python_runtime_unavailable",
        "python_sandbox_command_invalid": "python_sandbox_invalid",
    }.get(code, "python_worker_error")
    return PythonRuntimeError(mapped)


class PythonRuntime:
    """Execute bounded Python in a no-workspace, no-network Bubblewrap sandbox."""

    async def run(
        self,
        *,
        code: str,
        stdin: str,
        timeout_seconds: float,
        emit: PythonEventSink,
        authorize_action: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(code, str)
            or not 1 <= len(code) <= 65536
            or not isinstance(stdin, str)
            or len(stdin) > 65536
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0.01 <= float(timeout_seconds) <= 60.0
        ):
            raise PythonRuntimeError("python_input_invalid")
        timeout_seconds = float(timeout_seconds)
        payload = json.dumps(
            {"code": code, "stdin": stdin},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        command = [
            developer_sandbox.PYTHON_EXECUTABLE,
            "-I",
            "-S",
            "-c",
            PYTHON_BOOTSTRAP,
            str(timeout_seconds),
        ]
        try:
            argv = developer_sandbox.python_compute_sandbox(command)
            emit("python_execution_started", {})
            if authorize_action is not None and not authorize_action():
                raise PythonRuntimeError("python_agent_execution_invalid")
            result = await run_process(
                argv,
                cwd=Path("/"),
                timeout=timeout_seconds,
                stdout_limit=PYTHON_STDOUT_LIMIT,
                stderr_limit=PYTHON_STDERR_LIMIT,
                stdin=payload,
                env=python_environment(),
            )
        except DeveloperError as error:
            raise _runtime_error(error) from None

        if authorize_action is not None and not authorize_action():
            raise PythonRuntimeError("python_agent_execution_invalid")
        emit(
            "python_execution_completed",
            {"exit_code": result.exit_code, "duration_seconds": result.duration},
        )
        return PythonRunResult(
            stdout=result.stdout.decode("utf-8", "replace"),
            stderr=result.stderr.decode("utf-8", "replace"),
            exit_code=result.exit_code,
            duration_seconds=result.duration,
            completed_at=datetime.now(UTC),
        ).model_dump(mode="json")
