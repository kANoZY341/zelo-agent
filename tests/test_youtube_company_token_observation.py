"""Provider-reported local research token observations: transparent, incomplete, inert."""

import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.application import create_app
from app.config import ROOT
from app.models import TaskAttempt, utcnow
from app.policy import State
from app.projects import ProjectService
from app.schemas import AgentTaskCreate, ProjectCreate, ResearchRunInput
from app.youtube_company_team import TEAM_ROLE_IDS, youtube_company_registry_digest
from app.youtube_company_token_observation import (
    YoutubeCompanyTokenObservation,
    YoutubeCompanyTokenObservationService,
    _read_reported_tokens,
)
from tests.conftest import AUTH
from tests.test_api import wait_for
from tests.test_research import ResearchProvider, call, final
from tests.test_youtube_company_attempt_budget import _new
from tests.test_youtube_company_team import _config, _real_company_graph


def _view(database, project_id, *, now=None, active=True):
    return YoutubeCompanyTokenObservationService(database).preview(
        project_id,
        active_registry_version=(youtube_company_registry_digest() if active else "0" * 64),
        now=now,
    )


def _finished(company, *, actor_index, key, usage, model_steps=None, outcome=State.SUCCEEDED):
    result = _new(company, index=actor_index, key=key)
    claim = company["tasks"].claim()
    assert claim is not None and claim.task_id == result.id
    if outcome == State.SUCCEEDED:
        payload = {
            "answer": "Synthetic grounded result. [S1]",
            "sources": [
                {
                    "id": "S1",
                    "tool": "read_workspace_file",
                    "locator": "workspace:facts.txt",
                    "sha256": "0" * 64,
                    "truncated": False,
                }
            ],
            "alias": "local_general",
            "actual_model": "test-local-model",
            "model_steps": model_steps if model_steps is not None else len(usage),
            "tool_calls": 1,
            "usage": usage,
            "completed_at": utcnow().isoformat(),
        }
        company["tasks"].finish(claim, outcome, result=payload)
    else:
        company["tasks"].finish(claim, outcome, error="synthetic-failed-research")
    return result


def _step(index, prompt=7, completion=5, total=12):
    return {
        "step": index,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def test_empty_project_report_is_inert_and_strict(database, settings):
    from app.agent_factory import default_agent_registry

    project = ProjectService(database).create(
        ProjectCreate(name="Observation only", summary="No agent or work required")
    )
    view = _view(database, project.id)
    assert view.schema_version == "zelo-youtube-company-token-observation-v1"
    assert view.attempted_research_workers == 0
    assert view.completed_research_workers == 0
    assert view.fully_reported_successes == 0
    assert view.unmetered_attempts == 0
    assert view.provider_reported_total_tokens == 0
    assert tuple(view.trusted_team_roles) == TEAM_ROLE_IDS
    assert view.active_company_registry_on_this_process
    assert view.provider_metrics_are_unverified is True
    assert view.does_not_assume_unmetered_attempts_are_zero is True
    assert view.does_not_include_failed_or_cancelled_model_work is True
    assert view.no_money_or_cost_guarantee is True
    assert view.no_new_execution_or_approval_authority is True
    assert (
        YoutubeCompanyTokenObservation.model_validate_json(view.model_dump_json(), strict=True)
        == view
    )
    baseline = _view(database, project.id, active=False)
    assert not baseline.active_company_registry_on_this_process
    assert _view(database, project.id).active_company_registry_on_this_process
    assert default_agent_registry().get_agent("youtube") is None


def test_successful_usage_is_partial_reported_evidence_across_company_roles(database, settings):
    company = _real_company_graph(database, settings)
    first = _finished(company, actor_index=0, key="tokens-vp", usage=[_step(1), _step(2)])
    second = _finished(company, actor_index=3, key="tokens-specialist", usage=[_step(1, 9, 4, 13)])
    _finished(company, actor_index=1, key="tokens-director-failed", usage=[], outcome=State.FAILED)
    view = _view(database, company["project"].id)
    assert view.attempted_research_workers == 3
    assert view.completed_research_workers == 2
    assert view.fully_reported_successes == 2
    assert view.unmetered_attempts == 1
    assert view.provider_reported_prompt_tokens == 23
    assert view.provider_reported_completion_tokens == 14
    assert view.provider_reported_total_tokens == 37
    assert view.no_money_or_cost_guarantee
    assert view.provider_metrics_are_unverified
    assert view.provider_reported_total_tokens != 0
    with database.read() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TaskAttempt)
                .where(TaskAttempt.task_id.in_([first.id, second.id]))
            )
            == 2
        )


@pytest.mark.parametrize(
    "usage,model_steps",
    [
        ([{"step": 1, "prompt_tokens": None, "completion_tokens": 5, "total_tokens": None}], 1),
        ([{"step": 1, "prompt_tokens": -2, "completion_tokens": 5, "total_tokens": 3}], 1),
        ([{"step": 1, "prompt_tokens": True, "completion_tokens": 5, "total_tokens": 6}], 1),
        ([{"step": 1, "prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 99}], 1),
        ([{"step": 2, "prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}], 1),
        ([{"step": 1, "prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}], 2),
        ([], 0),
        ([_step(1, 10_000_001, 1, 10_000_002)], 1),
    ],
)
def test_partial_invalid_or_missing_usage_never_claimed_as_fully_metered(
    database, settings, usage, model_steps
):
    company = _real_company_graph(database, settings)
    task = _finished(
        company,
        actor_index=0,
        key="untrusted-metrics",
        usage=usage,
        model_steps=model_steps,
    )
    with database.read() as session:
        attempt = session.scalar(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
        assert _read_reported_tokens(attempt) is None
    view = _view(database, company["project"].id)
    assert view.attempted_research_workers == 1
    assert view.completed_research_workers == 1
    assert view.fully_reported_successes == 0
    assert view.unmetered_attempts == 1
    assert view.provider_reported_total_tokens == 0
    assert view.does_not_assume_unmetered_attempts_are_zero


def test_rolling_24h_window_and_other_project_generic_agent_exclusion(database, settings):
    from app.agent_instances import AgentInstanceService
    from app.memory import MemoryService
    from app.schemas import AgentInstanceCreate

    company = _real_company_graph(database, settings)
    task = _finished(company, actor_index=2, key="window-company-manager", usage=[_step(1)])
    outside = ProjectService(database).create(
        ProjectCreate(name="Separate Project", summary="Token evidence isolation")
    )
    generic = AgentInstanceService(database, company["factory"], MemoryService(database)).create(
        AgentInstanceCreate(agent_id="research", project_id=outside.id)
    )
    task2, was_new = company["execution"].submit(
        generic.id,
        AgentTaskCreate(
            task=ResearchRunInput(
                kind="research_run", payload={"query": "Generic unbound evidence"}
            )
        ),
        "generic-usage-isolation",
    )
    assert was_new
    claim = company["tasks"].claim()
    assert claim is not None and claim.task_id == task2.id
    company["tasks"].finish(
        claim,
        State.SUCCEEDED,
        result={
            "answer": "Generic cited evidence. [S1]",
            "sources": [
                {
                    "id": "S1",
                    "tool": "read_workspace_file",
                    "locator": "workspace:facts.txt",
                    "sha256": "0" * 64,
                    "truncated": False,
                }
            ],
            "alias": "local_general",
            "actual_model": "local",
            "model_steps": 1,
            "tool_calls": 1,
            "usage": [_step(1)],
            "completed_at": utcnow().isoformat(),
        },
    )
    assert _view(database, company["project"].id).provider_reported_total_tokens == 12
    assert _view(database, outside.id).provider_reported_total_tokens == 0

    with database.transaction() as session:
        row = session.scalar(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
        row.started_at = utcnow() - timedelta(hours=25)
    elapsed = _view(database, company["project"].id)
    assert elapsed.attempted_research_workers == 0
    assert elapsed.provider_reported_total_tokens == 0


def test_operator_get_projection_receives_actual_fake_model_usage(database, settings):
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="test-company-usage-", dir=ROOT / "workspaces") as temp:
        workspace = Path(temp)
        (workspace / "facts.txt").write_text("Local cited evidence.", encoding="utf-8")
        provider = ResearchProvider(
            call("read_workspace_file", path="facts.txt"),
            final("Cited local evidence. [S1]", "S1"),
        )
        with TestClient(
            create_app(
                _config(settings).model_copy(update={"workspace_root": workspace}),
                provider=provider,
            ),
            headers=AUTH,
        ) as client:
            company = _real_company_graph(database, settings)
            writer = company["created"][-1].child_instance
            delegation = company["created"][-1].delegation
            entry = client.post(
                f"/agents/{writer.id}/tasks",
                json={
                    "task": {
                        "kind": "research_run",
                        "campaign_id": company["campaign"].id,
                        "payload": {"query": "Report this local science article."},
                    },
                    "delegation_id": delegation.id,
                },
                headers={**AUTH, "Idempotency-Key": "measured-fake-provider-bound"},
            )
            assert entry.status_code == 201, entry.text
            task = wait_for(client, entry.json()["id"], "succeeded")
            assert task["result"]["usage"][0]["total_tokens"] == 12
            uri = f"/projects/{company['project'].id}/youtube-company-token-observation"
            denied = client.get(uri, headers={"Authorization": "Bearer wrong"})
            assert denied.status_code == 401
            report = client.get(uri)
            assert report.status_code == 200, report.text
            data = report.json()
            assert data["attempted_research_workers"] == 1
            assert data["completed_research_workers"] == 1
            assert data["fully_reported_successes"] == 1
            assert data["unmetered_attempts"] == 0
            assert data["provider_reported_prompt_tokens"] == 14
            assert data["provider_reported_completion_tokens"] == 10
            assert data["provider_reported_total_tokens"] == 24
            assert data["active_company_registry_on_this_process"] is True
            assert data["no_money_or_cost_guarantee"] is True
            assert data["provider_metrics_are_unverified"] is True
            assert (
                client.get(
                    "/projects/00000000-0000-4000-8000-000000000000/youtube-company-token-observation"
                ).status_code
                == 404
            )
            assert len(provider.requests) == 2


def test_observation_invariant_fields_and_role_identity_fail_closed(database, settings):
    project = ProjectService(database).create(
        ProjectCreate(name="Strict observation", summary="No delegated rights")
    )
    baseline = _view(database, project.id).model_dump(mode="json")
    for field in (
        "provider_metrics_are_unverified",
        "does_not_assume_unmetered_attempts_are_zero",
        "does_not_include_failed_or_cancelled_model_work",
        "no_money_or_cost_guarantee",
        "no_new_execution_or_approval_authority",
    ):
        tampered = json.loads(json.dumps(baseline))
        tampered[field] = False
        with pytest.raises(ValidationError):
            YoutubeCompanyTokenObservation.model_validate_json(json.dumps(tampered), strict=True)
    malformed_roles = json.loads(json.dumps(baseline))
    malformed_roles["trusted_team_roles"][0] = "third_party_unknown"
    with pytest.raises(ValidationError):
        YoutubeCompanyTokenObservation.model_validate_json(json.dumps(malformed_roles), strict=True)
