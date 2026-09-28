"""Run the existing Zelo Codex worker as a bounded read-only PR reviewer."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from app.codex_worker import CodexCliWorker
from app.config import Settings
from app.developer_contracts import Limits, ReviewerExecutionRequest, ReviewerReport
from app.developer_sandbox import sandbox_policy_version
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_reviewer


def _sha(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("expected a full 40-character Git SHA")
    return normalized


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _limits(settings: Settings, timeout_seconds: float) -> Limits:
    return Limits(
        overall_seconds=min(timeout_seconds, settings.developer_timeout_seconds),
        worker_seconds=min(timeout_seconds, settings.developer_worker_timeout_seconds),
        validation_seconds=settings.developer_validation_timeout_seconds,
        git_seconds=settings.developer_git_timeout_seconds,
        stdout_bytes=settings.developer_stdout_bytes,
        stderr_bytes=settings.developer_stderr_bytes,
        line_bytes=settings.developer_jsonl_line_bytes,
        events=settings.developer_jsonl_events,
        diff_bytes=settings.developer_diff_bytes,
        changed_files=settings.developer_changed_files,
    )


async def _review(args: argparse.Namespace) -> dict:
    repo = Path(args.repository).resolve()
    if not (repo / ".git").exists():
        raise RuntimeError("repository checkout is unavailable")

    base_sha = _sha(args.base_sha)
    head_sha = _sha(args.head_sha)
    actual_head = _sha(_git(repo, "rev-parse", "HEAD"))
    if actual_head != head_sha:
        raise RuntimeError("checked-out HEAD does not match the requested PR head")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, head_sha],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    settings = Settings()
    worker = CodexCliWorker(settings)
    descriptor = descriptor_for_reviewer(
        worker,
        implementation="codex",
        worker_id="codex_pr_reviewer",
    )
    registry = WorkerRegistry([(descriptor, worker)])
    registration, selection = Supervisor(registry).select(
        role="reviewer",
        capabilities=("code_review",),
    )

    identity = await worker.preflight()
    if identity.implementation != registration.descriptor.implementation:
        raise RuntimeError("Codex reviewer identity does not match its registered implementation")

    request = ReviewerExecutionRequest(
        instruction=(
            "Review the exact pull-request change set between base "
            f"{base_sha} and head {head_sha}. Inspect correctness, security, regressions, "
            "migration/idempotency behavior, concurrency/restart behavior, and compliance with "
            "AGENTS.md and the Zelo architecture. Treat passing tests as evidence, not proof. "
            "Do not modify anything. Return exactly one JSON object with no markdown using this "
            'shape: {"summary":"bounded summary","findings":[{"severity":"low|medium|high|critical",'
            '"message":"specific actionable finding","path":"relative/path/or null"}]}. '
            "Use an empty findings array only when no concrete issue is found."
        ),
        source_repository=str(repo),
        workspace_root=str(settings.workspace_root),
        base_sha=head_sha,
        worker=identity,
        worker_selection=selection,
        sandbox_version=sandbox_policy_version(),
        limits=_limits(settings, args.timeout_seconds),
    )

    with worker.bind(identity) as bound:
        result = await bound.run(request, repo)

    report = ReviewerReport.model_validate_json(result.summary)
    return {
        "schema": "zelo-codex-pr-review-v1",
        "base_sha": base_sha,
        "head_sha": head_sha,
        "worker": {
            "worker_id": registration.descriptor.worker_id,
            "implementation": identity.implementation,
            "cli_version": identity.cli_version,
            "config_version": identity.config_version,
            "capability_profile": identity.capability_profile,
        },
        "report": report.model_dump(mode="json"),
        "usage": result.usage,
        "event_count": result.event_count,
        "nonfatal_error_items": result.nonfatal_error_items,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "duration_seconds": result.duration_seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", default="codex-pr-review.json")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    args = parser.parse_args()

    payload = asyncio.run(_review(args))
    output = Path(args.output)
    output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
