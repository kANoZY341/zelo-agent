"""Real local Review & Promotion smoke: python -m scripts.smoke_promotion."""

import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import ROOT, Settings
from app.database import Database
from app.developer import DeveloperRuntime
from app.developer_contracts import DeveloperRequest, WorkerIdentity, WorkerResult
from app.developer_process import DeveloperError
from app.policy import DomainError, State
from app.promotion import PromotionRuntime
from app.promotion_ci import PromotionCIVerifier
from app.promotion_contracts import PromotionPublishResult, PromotionRequest
from app.promotion_git import PromotionCommitter
from app.promotion_github import GitHubPromotionPublisher
from app.promotion_review import PromotionReviewer
from app.runner import Runner
from app.schemas import DeveloperRunInput, PromotionRunInput
from app.tasks import TaskService

SMOKE_CANDIDATE = "SMOKE_PROMOTION_CANDIDATE.txt"
SMOKE_CANDIDATE_CONTENT = "Zelo v0.7 deterministic local promotion smoke candidate.\n"


def smoke_branch(base_sha: str) -> str:
    """Return a stable, recognizable branch for one primary checkout revision."""
    return f"zelo/promotion/smoke-{base_sha[:12]}"


class DeterministicSmokeWorker:
    """Developer-style worker that writes one fixed candidate and never invokes a model."""

    _identity = WorkerIdentity(
        implementation="zelo-smoke-worker",
        config_version="1",
        cli_version="zelo-smoke-worker-v1",
        capability_profile="deterministic-local-candidate",
        executable="builtin",
        executable_sha256="0" * 64,
        code_mode_host="none",
        code_mode_host_sha256="0" * 64,
        auth_location="none",
    )

    def identity(self) -> WorkerIdentity:
        return self._identity

    @contextmanager
    def bind(self, approved: WorkerIdentity):
        if approved != self._identity:
            raise DeveloperError("worker_configuration_changed")
        yield self

    async def preflight(self) -> WorkerIdentity:
        return self._identity

    async def run(self, request: DeveloperRequest, repo: Path) -> WorkerResult:
        started = datetime.now(UTC)
        (repo / SMOKE_CANDIDATE).write_text(SMOKE_CANDIDATE_CONTENT)
        return WorkerResult(
            exit_code=0,
            summary="Deterministic smoke candidate created",
            event_count=1,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=0,
        )


def _run_task(
    service: TaskService,
    task: Any,
    runtime: DeveloperRuntime,
    promotion_runtime: PromotionRuntime | None = None,
) -> Any:
    if task.status == State.SUCCEEDED:
        return service.get(task.id)
    if task.status != State.AWAITING_APPROVAL or not task.approvals:
        raise RuntimeError("smoke task is not safely runnable")
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    if execution is None:
        raise RuntimeError("smoke task could not be claimed")
    asyncio.run(
        Runner(
            service,
            developer_runtime=runtime,
            promotion_runtime=promotion_runtime,
        )._run(execution)
    )
    completed = service.get(task.id)
    if completed.status != State.SUCCEEDED:
        raise RuntimeError("smoke task failed")
    return completed


def _settings() -> Settings:
    settings = Settings()
    if settings.promotion_repository is None:
        raise RuntimeError("ZELO_PROMOTION_REPOSITORY is not configured")
    return settings.model_copy(
        update={
            "runtime_dir": ROOT / "runtime" / "smoke-promotion",
            "workspace_root": ROOT / "workspaces" / "smoke-promotion",
        }
    )


async def smoke(settings: Settings) -> dict[str, Any]:
    worker = DeterministicSmokeWorker()
    runtime = DeveloperRuntime(settings, worker)
    developer_request = runtime.request(
        "Create the deterministic local promotion smoke candidate.",
        "zelo_core",
        900,
    )
    branch = smoke_branch(developer_request.base_sha)
    developer_key = f"smoke-promotion-developer-{developer_request.base_sha}"
    promotion_key = f"smoke-promotion-{developer_request.base_sha}"

    settings.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = Database(settings.database_path)
    database.migrate()
    try:
        reviewer = PromotionReviewer(runtime)
        service = TaskService(
            database,
            settings,
            developer_runtime=runtime,
            promotion_reviewer=reviewer,
        )
        developer_task, _ = service.submit(
            DeveloperRunInput(
                kind="developer_run",
                payload={
                    "instruction": developer_request.instruction,
                    "validation_profile": "zelo_core",
                },
                timeout_seconds=900,
            ),
            developer_key,
        )
        developer = _run_task(service, developer_task, runtime)
        attempt = developer.attempts[-1]

        promotion_task, _ = service.submit(
            PromotionRunInput(
                kind="promotion_run",
                payload={
                    "developer_task_id": developer.id,
                    "developer_attempt_id": attempt.id,
                    "target_base_branch": "main",
                    "branch": branch,
                    "commit_title": "test: v0.7 promotion smoke candidate",
                    "commit_message": "Deterministic local Review & Promotion smoke.",
                    "pr_title": "test: v0.7 promotion smoke candidate",
                    "pr_body": (
                        "Real local v0.7 Review & Promotion smoke. "
                        "This PR is intentionally not merged by the smoke."
                    ),
                },
                timeout_seconds=900,
            ),
            promotion_key,
        )
        publisher = GitHubPromotionPublisher(settings, runtime, reviewer)
        promotion_runtime = PromotionRuntime(
            committer=PromotionCommitter(runtime, reviewer),
            publisher=publisher,
        )
        promotion = _run_task(service, promotion_task, runtime, promotion_runtime)
        if promotion.result is None or promotion.promotion_spec is None:
            raise RuntimeError("smoke promotion result is unavailable")

        promotion_request = PromotionRequest.model_validate(promotion.promotion_spec)
        publication = PromotionPublishResult.model_validate(promotion.result["publication"])
        verification = await PromotionCIVerifier(settings).verify(
            promotion_request,
            publication,
        )
        return {
            "repository": publication.repository,
            "branch": publication.branch,
            "pr_number": publication.pr_number,
            "pr_url": publication.pr_url,
            "head_sha": verification.head_sha,
            "required_checks": verification.required_checks,
            "checks": [check.name for check in verification.checks],
            "branch_created": publication.branch_created,
            "pr_created": publication.pr_created,
        }
    finally:
        database.close()


def main() -> None:
    try:
        result = asyncio.run(smoke(_settings()))
    except (DeveloperError, DomainError, RuntimeError) as error:
        print(f"promotion smoke failed: {error}")
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
