"""Worker/model performance and reported-cost evidence for v0.13."""

from datetime import timedelta

from app.models import Conversation, ConversationTurn, Task, TaskAttempt, utcnow
from tests.conftest import AUTH


def _task(
    *,
    task_id: str,
    kind: str,
    status: str = "succeeded",
    alias: str | None = None,
    model: str | None = None,
    result: dict | None = None,
) -> Task:
    now = utcnow()
    return Task(
        id=task_id,
        kind=kind,
        payload={"prompt": "private operator text", "profile": "general"},
        timeout_seconds=120,
        status=status,
        action_version=1,
        action_hash="a" * 64,
        idempotency_key=None,
        request_hash="b" * 64,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        result=result,
        error=None if status == "succeeded" else "synthetic_failure",
        selected_alias=alias,
        selected_model=model,
        config_version="test-v1",
        capacity_backoff_attempts=0,
    )


def _attempt(
    *,
    attempt_id: str,
    task_id: str,
    number: int,
    outcome: str,
    result: dict | None,
    seconds: float = 2.0,
) -> TaskAttempt:
    started = utcnow()
    return TaskAttempt(
        id=attempt_id,
        task_id=task_id,
        number=number,
        started_at=started,
        ended_at=started + timedelta(seconds=seconds),
        outcome=outcome,
        result=result,
        error=None if outcome == "succeeded" else "synthetic_failure",
    )


def test_performance_evidence_aggregates_persisted_model_usage_without_prompts(client, database):
    conversation_id = "00000000-0000-0000-0000-000000000101"
    turn_id = "00000000-0000-0000-0000-000000000102"
    ai_task_id = "00000000-0000-0000-0000-000000000103"
    ai_attempt_id = "00000000-0000-0000-0000-000000000104"
    agent_task_id = "00000000-0000-0000-0000-000000000105"
    agent_attempt_id = "00000000-0000-0000-0000-000000000106"

    ai_result = {
        "text": "private model answer",
        "provider": "fake",
        "alias": "local_general",
        "actual_model": "model-a",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        },
        "timing": {"total_duration_ns": 1_000_000_000},
        "completed_at": utcnow().isoformat(),
    }
    agent_result = {
        "final_response": "private agent answer",
        "alias": "local_general",
        "actual_model": "model-a",
        "model_steps": 2,
        "tool_calls": 1,
        "tools_used": ["repo_status"],
        "usage": [
            {
                "step": 1,
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            },
            {
                "step": 2,
                "prompt_tokens": 8,
                "completion_tokens": 2,
                "total_tokens": 10,
            },
        ],
        "completed_at": utcnow().isoformat(),
    }

    with database.transaction() as session:
        session.add(
            Conversation(
                id=conversation_id,
                title="Private conversation",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.add(
            ConversationTurn(
                id=turn_id,
                conversation_id=conversation_id,
                number=1,
                idempotency_key="performance-turn",
                user_content="SECRET_USER_PROMPT",
                assistant_content="SECRET_ASSISTANT_REPLY",
                mind_action={"type": "reply", "content": "SECRET_ASSISTANT_REPLY"},
                selected_alias="local_mind",
                selected_model="mind-a",
                usage={
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
                core_task_id=None,
                created_at=utcnow(),
            )
        )
        session.add(
            _task(
                task_id=ai_task_id,
                kind="ai_prompt",
                alias="local_general",
                model="model-a",
                result=ai_result,
            )
        )
        session.add(
            _attempt(
                attempt_id=ai_attempt_id,
                task_id=ai_task_id,
                number=1,
                outcome="succeeded",
                result=ai_result,
            )
        )
        session.add(
            _task(
                task_id=agent_task_id,
                kind="agent_run",
                alias="local_general",
                model="model-a",
                result=agent_result,
            )
        )
        session.add(
            _attempt(
                attempt_id=agent_attempt_id,
                task_id=agent_task_id,
                number=1,
                outcome="succeeded",
                result=agent_result,
                seconds=3,
            )
        )

    response = client.get("/performance/evidence?limit=20", headers=AUTH)
    assert response.status_code == 200, response.text
    evidence = response.json()

    assert evidence["schema"] == "zelo-performance-evidence-v1"
    assert evidence["summary_scope"] == "returned_evidence_only"
    assert evidence["totals"]["returned_model_observations"] == 3
    assert evidence["totals"]["returned_worker_observations"] == 0
    assert evidence["reported_cost"]["status"] == "not_reported_by_current_sources"
    assert evidence["reported_cost"]["amount_micros"] is None
    assert evidence["reported_cost"]["currency"] is None

    mind = next(item for item in evidence["model_evidence"] if item["source_kind"] == "mind_turn")
    assert mind["source_id"] == turn_id
    assert mind["total_tokens"] == 7
    assert mind["duration_seconds"] is None

    model_summary = next(
        item
        for item in evidence["model_summaries"]
        if item["alias"] == "local_general" and item["model"] == "model-a"
    )
    assert model_summary["observations"] == 2
    assert model_summary["prompt_tokens"] == 25
    assert model_summary["completion_tokens"] == 9
    assert model_summary["total_tokens"] == 34
    assert model_summary["duration_observations"] == 2
    assert model_summary["duration_seconds"] == 5.0

    encoded = response.text
    assert "SECRET_USER_PROMPT" not in encoded
    assert "SECRET_ASSISTANT_REPLY" not in encoded
    assert "private model answer" not in encoded
    assert "private agent answer" not in encoded


def test_performance_evidence_reports_developer_and_reviewer_worker_observations(client, database):
    task_id = "00000000-0000-0000-0000-000000000201"
    attempt_id = "00000000-0000-0000-0000-000000000202"
    result = {
        "worker": "codex",
        "worker_version": "codex-cli-test",
        "worker_config_version": "cfg-1",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "output_tokens": 30,
            "ignored_secret_counter": 999,
        },
        "duration_seconds": 12.5,
        "validation_passed": True,
        "independent_review": {
            "implementation": "claude-code",
            "worker_version": "claude-test",
            "usage": {"input_tokens": 40, "output_tokens": 10},
            "duration_seconds": 4.5,
        },
    }

    with database.transaction() as session:
        session.add(
            _task(
                task_id=task_id,
                kind="developer_run",
                result=result,
            )
        )
        session.add(
            _attempt(
                attempt_id=attempt_id,
                task_id=task_id,
                number=1,
                outcome="succeeded",
                result=result,
                seconds=20,
            )
        )

    evidence = client.get("/performance/evidence", headers=AUTH).json()
    assert evidence["totals"]["returned_worker_observations"] == 2
    developer = next(item for item in evidence["worker_evidence"] if item["role"] == "developer")
    reviewer = next(item for item in evidence["worker_evidence"] if item["role"] == "reviewer")

    assert developer["worker"] == "codex"
    assert developer["duration_seconds"] == 12.5
    assert developer["validation_passed"] is True
    assert developer["usage"] == {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 30,
    }
    assert reviewer["worker"] == "claude-code"
    assert reviewer["duration_seconds"] == 4.5
    assert reviewer["validation_passed"] is None

    codex_summary = next(
        item
        for item in evidence["worker_summaries"]
        if item["role"] == "developer" and item["worker"] == "codex"
    )
    assert codex_summary["observations"] == 1
    assert codex_summary["input_tokens"] == 100
    assert codex_summary["cached_input_tokens"] == 20
    assert codex_summary["output_tokens"] == 30
    assert codex_summary["validation_passes"] == 1


def test_performance_evidence_keeps_failed_unreported_model_attempt_visible(client, database):
    task_id = "00000000-0000-0000-0000-000000000301"
    attempt_id = "00000000-0000-0000-0000-000000000302"
    with database.transaction() as session:
        session.add(
            _task(
                task_id=task_id,
                kind="research_run",
                status="failed",
                alias="local_general",
                model="model-b",
                result=None,
            )
        )
        session.add(
            _attempt(
                attempt_id=attempt_id,
                task_id=task_id,
                number=1,
                outcome="failed",
                result=None,
            )
        )

    evidence = client.get("/performance/evidence", headers=AUTH).json()
    item = next(entry for entry in evidence["model_evidence"] if entry["source_id"] == attempt_id)
    assert item["outcome"] == "failed"
    assert item["usage_status"] == "unreported"
    assert item["total_tokens"] is None

    summary = next(entry for entry in evidence["model_summaries"] if entry["model"] == "model-b")
    assert summary["observations"] == 1
    assert summary["succeeded"] == 0
    assert summary["failed"] == 1
    assert summary["usage_observations"] == 0


def test_performance_evidence_route_is_bounded_and_requires_operator(client):
    assert client.get("/performance/evidence?limit=0", headers=AUTH).status_code == 422
    assert client.get("/performance/evidence?limit=501", headers=AUTH).status_code == 422

    original = client.headers.pop("Authorization")
    try:
        assert client.get("/performance/evidence").status_code == 401
    finally:
        client.headers["Authorization"] = original
