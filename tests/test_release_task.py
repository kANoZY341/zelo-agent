"""Separate release approval persistence and exact-main execution wiring."""

import asyncio
import hashlib
from datetime import UTC, datetime

from app.models import Task, TaskAttempt
from app.policy import State, action_hash
from app.promotion_contracts import ReleaseRunResult
from app.runner import Runner
from app.schemas import ReleaseRunInput
from app.tasks import TaskService
from tests.test_promotion_release import fixture as release_fixture


class FakeReleaseRuntime:
    def __init__(self, result):
        self.result = result
        self.ready = []
        self.calls = []

    async def verify_ready(self, request):
        self.ready.append(request)
        return None

    async def run(self, request):
        self.calls.append(request)
        return self.result


def merge_source(database, tmp_path):
    configured, release_template = release_fixture(tmp_path)
    merge = release_template.merge
    merge_result = release_template.merge_result
    payload = {"promotion_task_id": merge.promotion_task_id}
    source_hash = action_hash("merge_run", payload, 120.0, 1, merge.model_dump(mode="json"))
    with database.transaction() as session:
        task = Task(
            kind="merge_run",
            payload=payload,
            timeout_seconds=120.0,
            status=State.SUCCEEDED,
            action_version=1,
            action_hash=source_hash,
            request_hash=hashlib.sha256(b"release-source").hexdigest(),
            merge_spec=merge.model_dump(mode="json"),
            result=merge_result.model_dump(mode="json"),
        )
        session.add(task)
        session.flush()
        attempt = TaskAttempt(
            task_id=task.id,
            number=1,
            outcome=State.SUCCEEDED,
            result=merge_result.model_dump(mode="json"),
            ended_at=datetime.now(UTC),
        )
        session.add(attempt)
        session.flush()
        task_id = task.id
    return configured, task_id, merge, merge_result


def test_release_task_requires_separate_approval_and_exact_main(database, tmp_path):
    configured, merge_task_id, merge, merge_result = merge_source(database, tmp_path)
    final = ReleaseRunResult(
        repository=merge_result.repository,
        branch="main",
        main_sha=merge_result.merge_commit_sha,
        tag="v0.7.0",
        tag_object_sha="7" * 40,
        annotated=True,
        reused=False,
    )
    runtime = FakeReleaseRuntime(final)
    service = TaskService(
        database,
        configured,
        promotion_release_runtime=runtime,
    )

    task, created = service.submit(
        ReleaseRunInput(
            kind="release_run",
            payload={"merge_task_id": merge_task_id, "tag": "v0.7.0"},
        ),
        None,
    )

    assert created is True
    assert task.status == State.AWAITING_APPROVAL
    assert task.release_spec.merge_task_id == merge_task_id
    assert task.release_spec.main_sha == merge_result.merge_commit_sha
    assert task.release_spec.tag == "v0.7.0"
    assert len(task.approvals) == 1
    assert len(runtime.ready) == 1
    assert service.claim() is None

    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    assert execution.release_request.main_sha == merge_result.merge_commit_sha
    assert execution.release_request.merge == merge

    asyncio.run(
        Runner(
            service,
            promotion_release_runtime=runtime,
        )._run(execution)
    )

    current = service.get(task.id)
    assert current.status == State.SUCCEEDED
    assert current.result["tag"] == "v0.7.0"
    assert current.result["main_sha"] == merge_result.merge_commit_sha
    assert len(runtime.calls) == 1
    events = [event.event for event in service.events(task.id, 100, 0)]
    assert "release_ready_verified" in events
    assert "release_approval_created" in events
    assert "release_execution_started" in events
    assert events[-1] == "release_tag_created"


def test_release_source_change_fails_closed_before_tagging(database, tmp_path):
    configured, merge_task_id, _, _ = merge_source(database, tmp_path)
    final = ReleaseRunResult(
        repository="kANoZY341/zelo-agent",
        branch="main",
        main_sha="8" * 40,
        tag="v0.7.0",
        tag_object_sha="7" * 40,
        annotated=True,
        reused=False,
    )
    runtime = FakeReleaseRuntime(final)
    service = TaskService(
        database,
        configured,
        promotion_release_runtime=runtime,
    )
    task, _ = service.submit(
        ReleaseRunInput(
            kind="release_run",
            payload={"merge_task_id": merge_task_id, "tag": "v0.7.0"},
        ),
        None,
    )
    service.decide(task.approvals[0].id, "approve")

    with database.transaction() as session:
        source = session.get(Task, merge_task_id)
        source.action_hash = "1" * 64

    assert service.claim() is None
    current = service.get(task.id)
    assert current.status == State.CANCELLED
    assert current.error == "release_source_changed"
    assert runtime.calls == []


def test_interrupted_release_retry_requires_fresh_approval(database, tmp_path):
    configured, merge_task_id, _, merge_result = merge_source(database, tmp_path)
    final = ReleaseRunResult(
        repository=merge_result.repository,
        branch="main",
        main_sha=merge_result.merge_commit_sha,
        tag="v0.7.0",
        tag_object_sha="7" * 40,
        annotated=True,
        reused=False,
    )
    runtime = FakeReleaseRuntime(final)
    service = TaskService(
        database,
        configured,
        promotion_release_runtime=runtime,
    )
    task, _ = service.submit(
        ReleaseRunInput(
            kind="release_run",
            payload={"merge_task_id": merge_task_id, "tag": "v0.7.0"},
        ),
        None,
    )
    original_spec = task.release_spec.model_dump(mode="json")
    service.decide(task.approvals[0].id, "approve")
    execution = service.claim()
    assert execution is not None
    service.finish(execution, State.INTERRUPTED, error="process_interrupted")

    retried = service.retry(task.id)

    assert retried.status == State.AWAITING_APPROVAL
    assert retried.action_version == 2
    assert retried.release_spec.model_dump(mode="json") == original_spec
    assert retried.result is None
    assert retried.error is None
    assert len(retried.approvals) == 2
    assert retried.approvals[-1].status == "pending"
    assert retried.approvals[-1].action_version == retried.action_version
    assert retried.approvals[-1].action_hash != retried.approvals[0].action_hash
