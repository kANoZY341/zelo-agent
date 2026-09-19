"""Separate merge approval task persistence and execution wiring."""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.developer_contracts import ValidationResult, WorkerIdentity
from app.models import Task, TaskAttempt
from app.policy import State, action_hash
from app.promotion_contracts import (
    CICheckSnapshot,
    MergeRunResult,
    PromotionCIVerification,
    PromotionCommitResult,
    PromotionPublishResult,
    PromotionRequest,
    PromotionReview,
    PromotionRunResult,
)
from app.promotion_github import publisher_identity
from app.runner import Runner
from app.schemas import MergeRunInput
from app.tasks import TaskService


class FakeCIVerifier:
    def __init__(self, value):
        self.value = value
        self.calls = []

    async def verify(self, promotion, publication):
        self.calls.append((promotion, publication))
        return self.value


class FakeMergeRuntime:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, request):
        self.calls.append(request)
        return self.result


def source_fixture(settings, database):
    gh_config = settings.runtime_dir / "merge-gh"
    gh_config.mkdir(exist_ok=True, mode=0o700)
    configured = settings.model_copy(
        update={
            "promotion_repository": "kANoZY341/zelo-agent",
            "promotion_gh_executable": Path("/usr/bin/true"),
            "promotion_gh_config_dir": gh_config,
            "promotion_required_checks": ("checks",),
        }
    )
    publisher = publisher_identity(configured)
    review = PromotionReview(
        developer_task_id="1" * 36,
        developer_attempt_id="2" * 36,
        source_repository="/trusted/source",
        workspace="developer/attempt/repo",
        base_sha="a" * 40,
        head_sha="a" * 40,
        worker_identity=WorkerIdentity(
            implementation="fake",
            config_version="1",
            cli_version="fake",
            capability_profile="test",
            executable="/fake",
            executable_sha256="b" * 64,
            code_mode_host="/fake-host",
            code_mode_host_sha256="c" * 64,
            auth_location="none",
        ),
        validation_identity="d" * 64,
        validation_profile="zelo_core",
        validation_version="1",
        sandbox_version="1",
        validation_passed=True,
        changed_file_count=1,
        candidate_manifest=[
            {
                "path": "feature.py",
                "operation": "add",
                "object_type": "file",
                "mode": "100644",
                "size": 1,
                "sha256": "e" * 64,
            }
        ],
        review_fingerprint="f" * 64,
    )
    promotion = PromotionRequest(
        review=review,
        publisher=publisher,
        repository="kANoZY341/zelo-agent",
        branch="zelo/promotion/task-merge-fixture",
        commit_title="feat: merge task fixture",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        pr_title="feat: merge task fixture",
    )
    validation = [
        ValidationResult(
            identifier=name,
            exit_code=0,
            output="",
            duration_seconds=0,
            completed_at=datetime(2026, 9, 19, 12, 1, tzinfo=UTC),
        )
        for name in ("ruff_check", "ruff_format", "pytest", "git_diff_check")
    ]
    commit = PromotionCommitResult(
        base_sha=review.base_sha,
        tree_sha="6" * 40,
        commit_sha="9" * 40,
        branch=promotion.branch,
        review_fingerprint=review.review_fingerprint,
        validation=validation,
    )
    publication = PromotionPublishResult(
        repository=promotion.repository,
        base_branch="main",
        base_sha=review.base_sha,
        branch=promotion.branch,
        commit_sha=commit.commit_sha,
        pr_number=7,
        pr_url="https://github.com/kANoZY341/zelo-agent/pull/7",
        pr_head_sha=commit.commit_sha,
        pr_base_sha=review.base_sha,
        branch_created=True,
        pr_created=True,
    )
    run_result = PromotionRunResult(commit=commit, publication=publication)
    payload = {
        "developer_task_id": review.developer_task_id,
        "developer_attempt_id": review.developer_attempt_id,
        "target_base_branch": "main",
        "branch": promotion.branch,
        "commit_title": promotion.commit_title,
        "commit_message": promotion.commit_message,
        "pr_title": promotion.pr_title,
        "pr_body": promotion.pr_body,
    }
    source_hash = action_hash("promotion_run", payload, 300.0, 1, promotion.model_dump(mode="json"))
    with database.transaction() as session:
        task = Task(
            kind="promotion_run",
            payload=payload,
            timeout_seconds=300.0,
            status=State.SUCCEEDED,
            action_version=1,
            action_hash=source_hash,
            request_hash=hashlib.sha256(b"merge-source").hexdigest(),
            promotion_spec=promotion.model_dump(mode="json"),
            result=run_result.model_dump(mode="json"),
        )
        session.add(task)
        session.flush()
        attempt = TaskAttempt(
            task_id=task.id,
            number=1,
            outcome=State.SUCCEEDED,
            result=run_result.model_dump(mode="json"),
            ended_at=datetime.now(UTC),
        )
        session.add(attempt)
        session.flush()
        task_id = task.id

    check = CICheckSnapshot(
        check_run_id=10,
        name="checks",
        status="completed",
        conclusion="success",
        completed_at=datetime(2026, 9, 19, 12, 30, tzinfo=UTC),
    )
    ci = PromotionCIVerification(
        repository=promotion.repository,
        pr_number=publication.pr_number,
        base_branch="main",
        base_sha=review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        required_checks=["checks"],
        checks=[check],
        verified_at=datetime(2026, 9, 19, 12, 31, tzinfo=UTC),
    )
    return configured, task_id, promotion, publication, ci


def test_merge_task_requires_second_approval_and_executes_exact_request(settings, database):
    configured, source_id, promotion, publication, ci = source_fixture(settings, database)
    verifier = FakeCIVerifier(ci)
    service = TaskService(database, configured, promotion_ci_verifier=verifier)

    task, created = service.submit(
        MergeRunInput(kind="merge_run", payload={"promotion_task_id": source_id}),
        None,
    )

    assert created is True
    assert task.status == State.AWAITING_APPROVAL
    assert task.merge_spec.promotion_task_id == source_id
    assert task.merge_spec.ci.head_sha == publication.pr_head_sha
    assert task.merge_spec.merge_method == "squash"
    assert len(task.approvals) == 1
    assert service.claim() is None

    merge_result = MergeRunResult(
        repository=promotion.repository,
        pr_number=publication.pr_number,
        base_branch="main",
        base_sha=promotion.review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        merge_method="squash",
        merge_commit_sha="8" * 40,
    )
    merge_runtime = FakeMergeRuntime(merge_result)
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    assert execution.merge_request.ci.head_sha == publication.pr_head_sha

    asyncio.run(
        Runner(
            service,
            promotion_merge_runtime=merge_runtime,
        )._run(execution)
    )

    current = service.get(task.id)
    assert current.status == State.SUCCEEDED
    assert current.result["merge_commit_sha"] == "8" * 40
    assert len(merge_runtime.calls) == 1
    events = [event.event for event in service.events(task.id, 100, 0)]
    assert "promotion_ci_verified" in events
    assert "merge_approval_created" in events
    assert "merge_reverified" in events
    assert events[-1] == "merge_completed"


def test_merge_source_change_fails_closed_before_execution(settings, database):
    configured, source_id, _, _, ci = source_fixture(settings, database)
    service = TaskService(
        database,
        configured,
        promotion_ci_verifier=FakeCIVerifier(ci),
    )
    task, _ = service.submit(
        MergeRunInput(kind="merge_run", payload={"promotion_task_id": source_id}),
        None,
    )
    service.decide(task.approvals[0].id, "approve")

    with database.transaction() as session:
        source = session.get(Task, source_id)
        source.action_hash = "7" * 64

    assert service.claim() is None
    current = service.get(task.id)
    assert current.status == State.CANCELLED
    assert current.error == "merge_source_changed"


def test_interrupted_merge_retry_requires_fresh_approval(settings, database):
    configured, source_id, _, _, ci = source_fixture(settings, database)
    service = TaskService(
        database,
        configured,
        promotion_ci_verifier=FakeCIVerifier(ci),
    )
    task, _ = service.submit(
        MergeRunInput(kind="merge_run", payload={"promotion_task_id": source_id}),
        None,
    )
    original_spec = task.merge_spec.model_dump(mode="json")
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    service.finish(execution, State.INTERRUPTED, error="process_interrupted")

    retried = service.retry(task.id)

    assert retried.status == State.AWAITING_APPROVAL
    assert retried.action_version == 2
    assert retried.merge_spec.model_dump(mode="json") == original_spec
    assert retried.result is None
    assert retried.error is None
    assert len(retried.approvals) == 2
    assert retried.approvals[-1].status == "pending"
    assert retried.approvals[-1].action_hash == retried.action_hash
