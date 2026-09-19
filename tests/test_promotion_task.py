"""Approval binding and persistence for v0.7 promotion tasks; no GitHub side effects."""

import asyncio
from pathlib import Path

import pytest

from app.policy import DomainError, State
from app.promotion import PromotionRuntime
from app.promotion_contracts import PromotionPublishResult
from app.promotion_git import PromotionCommitter
from app.promotion_review import PromotionReviewer
from app.runner import Runner
from app.schemas import DeveloperRunInput, PromotionRunInput
from app.tasks import TaskService


def completed_developer(runtime, database):
    service = TaskService(
        database,
        runtime.settings,
        developer_runtime=runtime,
        promotion_reviewer=PromotionReviewer(runtime),
    )
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "promotion fixture"}),
        None,
    )
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))
    completed = service.get(task.id)
    assert completed.status == State.SUCCEEDED
    return service, completed, completed.attempts[-1]


def promotion_input(task_id, attempt_id):
    return PromotionRunInput(
        kind="promotion_run",
        payload={
            "developer_task_id": task_id,
            "developer_attempt_id": attempt_id,
            "branch": "zelo/promotion/review-fixture",
            "commit_title": "feat: promote reviewed candidate",
            "pr_title": "feat: promote reviewed candidate",
        },
    )


class FakePublisher:
    async def publish(self, promotion, local, developer_request):
        return PromotionPublishResult(
            repository=promotion.repository,
            base_branch=promotion.target_base_branch,
            base_sha=promotion.review.base_sha,
            branch=promotion.branch,
            commit_sha=local.commit_sha,
            pr_number=7,
            pr_url=f"https://github.com/{promotion.repository}/pull/7",
            pr_head_sha=local.commit_sha,
            pr_base_sha=promotion.review.base_sha,
            branch_created=True,
            pr_created=True,
        )


def configured(runtime, database):
    gh_config = runtime.settings.runtime_dir / "test-gh-config"
    gh_config.mkdir(exist_ok=True, mode=0o700)
    settings = runtime.settings.model_copy(
        update={
            "promotion_repository": "kANoZY341/zelo-agent",
            "promotion_gh_executable": Path("/usr/bin/true"),
            "promotion_gh_config_dir": gh_config,
        }
    )
    return TaskService(
        database,
        settings,
        developer_runtime=runtime,
        promotion_reviewer=PromotionReviewer(runtime),
    )


@pytest.mark.developer_boundary
def test_promotion_submission_persists_exact_review_and_approval(developer_case, database):
    _, developer, attempt = completed_developer(developer_case, database)
    service = configured(developer_case, database)

    promotion, created = service.submit(promotion_input(developer.id, attempt.id), None)

    assert created is True
    assert promotion.status == State.AWAITING_APPROVAL
    assert promotion.promotion_spec.repository == "kANoZY341/zelo-agent"
    assert promotion.promotion_spec.review.developer_task_id == developer.id
    assert promotion.promotion_spec.review.developer_attempt_id == attempt.id
    assert promotion.promotion_spec.review.validation_passed is True
    assert promotion.promotion_spec.review.changed_file_count >= 1
    assert len(promotion.approvals) == 1
    assert promotion.approvals[0].action_hash
    events = service.events(promotion.id, 100, 0)
    assert [event.event for event in events] == [
        "task_submitted",
        "promotion_review_created",
        "state_transition",
        "approval_created",
        "promotion_approval_created",
    ]


@pytest.mark.developer_boundary
def test_promotion_idempotency_returns_bound_review_without_recomputing(developer_case, database):
    _, developer, attempt = completed_developer(developer_case, database)
    service = configured(developer_case, database)
    request = promotion_input(developer.id, attempt.id)

    first, created = service.submit(request, "promotion-idempotency")
    assert created is True
    repo = developer_case.path(attempt.id)
    (repo / "DEVELOPER_SMOKE.txt").write_text("mutated after review\n")

    second, created = service.submit(request, "promotion-idempotency")

    assert created is False
    assert second.id == first.id
    assert second.promotion_spec.review.review_fingerprint == (
        first.promotion_spec.review.review_fingerprint
    )


@pytest.mark.developer_boundary
def test_promotion_spec_tamper_invalidates_pending_approval(developer_case, database):
    _, developer, attempt = completed_developer(developer_case, database)
    service = configured(developer_case, database)
    promotion, _ = service.submit(promotion_input(developer.id, attempt.id), None)

    from app.models import Task

    with database.transaction() as session:
        stored = session.get(Task, promotion.id)
        changed = dict(stored.promotion_spec)
        changed["branch"] = "zelo/promotion/tampered"
        stored.promotion_spec = changed

    with pytest.raises(DomainError, match="no longer pending|conflicts"):
        service.decide(promotion.approvals[0].id, "approve")

    current = service.get(promotion.id)
    assert current.status == State.CANCELLED
    assert current.error == "approval_invalidated"
    assert current.approvals[0].status == "invalidated"


@pytest.mark.developer_boundary
def test_approved_promotion_creates_only_local_trusted_commit(developer_case, database):
    _, developer, attempt = completed_developer(developer_case, database)
    service = configured(developer_case, database)
    promotion, _ = service.submit(promotion_input(developer.id, attempt.id), None)
    reviewer = PromotionReviewer(developer_case)
    committer = PromotionCommitter(developer_case, reviewer)
    promotion_runtime = PromotionRuntime(committer, FakePublisher())

    assert service.claim() is None
    service.decide(promotion.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    assert execution.promotion_request.review.review_fingerprint == (
        promotion.promotion_spec.review.review_fingerprint
    )

    asyncio.run(
        Runner(
            service,
            developer_runtime=developer_case,
            promotion_runtime=promotion_runtime,
        )._run(execution)
    )

    current = service.get(promotion.id)
    assert current.status == State.SUCCEEDED
    assert current.result["commit"]["review_fingerprint"] == (
        promotion.promotion_spec.review.review_fingerprint
    )
    assert current.result["commit"]["branch"] == "zelo/promotion/review-fixture"
    assert len(current.result["commit"]["commit_sha"]) == 40
    assert len(current.result["commit"]["tree_sha"]) == 40
    assert current.result["publication"]["pr_number"] == 7
    events = [event.event for event in service.events(promotion.id, 100, 0)]
    assert "promotion_started" in events
    assert "promotion_commit_created" in events
    assert "promotion_branch_pushed" in events
    assert "promotion_pr_created" in events
    assert events[-1] == "promotion_completed"


@pytest.mark.developer_boundary
def test_promotion_requires_latest_successful_developer_attempt(developer_case, database):
    _, developer, attempt = completed_developer(developer_case, database)
    service = configured(developer_case, database)

    request = promotion_input(developer.id, attempt.id)
    with database.transaction() as session:
        from app.models import TaskAttempt

        newer = TaskAttempt(
            task_id=developer.id,
            number=attempt.number + 1,
            outcome=State.FAILED,
            error="fixture",
        )
        session.add(newer)

    with pytest.raises(DomainError, match="latest successful developer attempt"):
        service.submit(request, None)


@pytest.mark.developer_boundary
def test_promotion_repository_must_be_operator_configured(developer_case, database):
    _, developer, attempt = completed_developer(developer_case, database)
    gh_config = developer_case.settings.runtime_dir / "test-gh-config-missing-repo"
    gh_config.mkdir(exist_ok=True, mode=0o700)
    settings = developer_case.settings.model_copy(
        update={
            "promotion_gh_executable": Path("/usr/bin/true"),
            "promotion_gh_config_dir": gh_config,
        }
    )
    service = TaskService(
        database,
        settings,
        developer_runtime=developer_case,
        promotion_reviewer=PromotionReviewer(developer_case),
    )

    with pytest.raises(DomainError, match="Promotion review unavailable") as failure:
        service.submit(promotion_input(developer.id, attempt.id), None)

    assert failure.value.status_code == 503
