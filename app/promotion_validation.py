"""Cache-free trusted validation for an exact promotion candidate."""

import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import ROOT
from app.developer import DeveloperRuntime
from app.developer_contracts import DeveloperRequest, ValidationResult
from app.developer_process import DeveloperError, run_process
from app.developer_sandbox import git_argv, sandbox
from app.developer_validation import python_command


async def validate_promotion_candidate(
    runtime: DeveloperRuntime, repo: Path, request: DeveloperRequest
) -> list[ValidationResult]:
    """Re-run the approved validation profile without writing tool caches."""
    commands = [
        ("ruff_check", python_command("-m", "ruff", "check", "--no-cache", ".")),
        ("ruff_format", python_command("-m", "ruff", "format", "--check", ".")),
        ("pytest", python_command("-m", "pytest", "-q", "-p", "no:cacheprovider")),
        (
            "git_diff_check",
            git_argv("diff", "--no-ext-diff", "--no-textconv", "--check", request.base_sha, "--"),
        ),
    ]
    results: list[ValidationResult] = []
    for name, command in commands:
        if runtime.validation_identity() != request.validation_identity:
            raise DeveloperError("validation_environment_changed")
        runtime.verify_checkout_path(repo)
        started = time.monotonic()
        try:
            result = await run_process(
                sandbox(repo, command, venv=ROOT / ".venv"),
                cwd=repo,
                timeout=request.limits.validation_seconds,
                stdout_limit=min(request.limits.stdout_bytes, 32768),
                stderr_limit=request.limits.stderr_bytes,
            )
            output = (result.stdout + result.stderr).decode("utf-8", "replace")[:32768]
            code, error = result.exit_code, None
        except DeveloperError as failure:
            code, output, error = 124, "", str(failure)
        results.append(
            ValidationResult(
                identifier=name,
                exit_code=code,
                output=output,
                duration_seconds=time.monotonic() - started,
                completed_at=datetime.now(UTC),
                error=error,
            )
        )
        if code:
            break
    if runtime.validation_identity() != request.validation_identity:
        raise DeveloperError("validation_environment_changed")
    return results
