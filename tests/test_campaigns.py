"""v0.12 Campaign ownership and operational-state tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.campaigns import CampaignService
from app.policy import DomainError, State
from app.projects import ProjectService
from app.schemas import (
    CampaignCheckpointCreate,
    CampaignCreate,
    EchoInput,
    GoalCreate,
    GoalSpec,
    ProjectCreate,
)
from app.tasks import TaskService
from tests.conftest import AUTH


def campaign_fixture(database):
    projects = ProjectService(database)
    project = projects.create(ProjectCreate(name="Zelo", summary="Persistent personal AI."))
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Durable campaign",
            spec=GoalSpec(
                objective="Execute bounded multi-step work durably.",
                success_criteria=["Dependencies are enforced by Core."],
                constraints=["No autonomous scheduler yet."],
            ),
        ),
    )
    campaign = CampaignService(database).create(
        goal.id,
        CampaignCreate(title="Campaign state proof", summary="Dependency and checkpoint proof."),
    )
    return project, goal, campaign


def test_campaign_api_persists_events_and_checkpoints(client):
    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": "Zelo", "summary": "Persistent personal AI."},
    ).json()
    goal = client.post(
        f"/projects/{project['id']}/goals",
        headers=AUTH,
        json={
            "title": "Campaign state",
            "spec": {
                "objective": "Own bounded work.",
                "success_criteria": ["Campaigns persist."],
                "constraints": ["No scheduler yet."],
            },
        },
    ).json()

    created = client.post(
        f"/goals/{goal['id']}/campaigns",
        headers=AUTH,
        json={"title": "First campaign", "summary": "State proof."},
    )
    assert created.status_code == 201, created.text
    campaign = created.json()
    assert campaign["goal_id"] == goal["id"]
    assert campaign["status"] == "active"
    assert campaign["task_count"] == 0

    checkpoint = client.post(
        f"/campaigns/{campaign['id']}/checkpoints",
        headers=AUTH,
        json={"state": {"phase": "planning", "cursor": 1}},
    )
    assert checkpoint.status_code == 201, checkpoint.text
    assert checkpoint.json()["version"] == 1

    checkpoints = client.get(
        f"/campaigns/{campaign['id']}/checkpoints",
        headers=AUTH,
    ).json()
    assert checkpoints["total"] == 1
    assert checkpoints["items"][0]["state"]["phase"] == "planning"

    events = client.get(
        f"/campaigns/{campaign['id']}/events",
        headers=AUTH,
    ).json()
    assert [item["event"] for item in events["items"]] == [
        "campaign_created",
        "checkpoint_recorded",
    ]
    assert [item["sequence"] for item in events["items"]] == [1, 2]


def test_campaign_dependencies_gate_runner(settings, database):
    _, _, campaign = campaign_fixture(database)
    tasks = TaskService(database, settings)

    first, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "first"},
            campaign_id=campaign.id,
        ),
        None,
    )
    second, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "second"},
            campaign_id=campaign.id,
            depends_on_task_ids=[first.id],
        ),
        None,
    )

    stored_second = tasks.get(second.id)
    assert stored_second.campaign_id == campaign.id
    assert stored_second.depends_on_task_ids == [first.id]

    first_execution = tasks.claim()
    assert first_execution is not None
    assert first_execution.task_id == first.id
    assert tasks.claim() is None

    tasks.finish(first_execution, State.SUCCEEDED, {"text": "first"})
    second_execution = tasks.claim()
    assert second_execution is not None
    assert second_execution.task_id == second.id


def test_campaign_dependency_must_be_in_same_campaign(settings, database):
    projects = ProjectService(database)
    project = projects.create(ProjectCreate(name="Zelo", summary="Persistent personal AI."))
    goal = projects.create_goal(
        project.id,
        GoalCreate(
            title="Campaign ownership",
            spec=GoalSpec(objective="Keep task graphs scoped to one campaign."),
        ),
    )
    campaigns = CampaignService(database)
    first_campaign = campaigns.create(goal.id, CampaignCreate(title="A"))
    second_campaign = campaigns.create(goal.id, CampaignCreate(title="B"))
    tasks = TaskService(database, settings)

    prerequisite, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "outside"},
            campaign_id=first_campaign.id,
        ),
        None,
    )

    with pytest.raises(DomainError, match="same campaign"):
        tasks.submit(
            EchoInput(
                kind="echo",
                payload={"text": "invalid"},
                campaign_id=second_campaign.id,
                depends_on_task_ids=[prerequisite.id],
            ),
            None,
        )


def test_campaign_checkpoint_budget_is_enforced(database):
    _, _, campaign = campaign_fixture(database)
    campaigns = CampaignService(database)

    with pytest.raises(DomainError, match="size limit"):
        campaigns.checkpoint(
            campaign.id,
            CampaignCheckpointCreate(state={"oversized": "x" * 70000}),
        )


def test_campaign_events_and_checkpoints_are_immutable(database):
    _, _, campaign = campaign_fixture(database)
    campaigns = CampaignService(database)
    campaigns.checkpoint(
        campaign.id,
        CampaignCheckpointCreate(state={"phase": "proof"}),
    )

    statements = (
        "UPDATE campaign_events SET event='altered'",
        "DELETE FROM campaign_events",
        "UPDATE campaign_checkpoints SET version=99",
        "DELETE FROM campaign_checkpoints",
    )
    for statement in statements:
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(text(statement))


def test_campaign_ownership_and_dependencies_are_database_immutable(settings, database):
    _, goal, campaign = campaign_fixture(database)
    campaigns = CampaignService(database)
    other_campaign = campaigns.create(goal.id, CampaignCreate(title="Other campaign"))
    tasks = TaskService(database, settings)

    prerequisite, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "prerequisite"},
            campaign_id=campaign.id,
        ),
        None,
    )
    dependent, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "dependent"},
            campaign_id=campaign.id,
            depends_on_task_ids=[prerequisite.id],
        ),
        None,
    )
    outsider, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "outsider"},
            campaign_id=other_campaign.id,
        ),
        None,
    )

    immutable_statements = (
        (
            "UPDATE campaign_task_links SET campaign_id=:campaign_id WHERE task_id=:task_id",
            {"campaign_id": other_campaign.id, "task_id": dependent.id},
        ),
        (
            "DELETE FROM campaign_task_links WHERE task_id=:task_id",
            {"task_id": dependent.id},
        ),
        (
            "UPDATE task_dependencies SET depends_on_task_id=:depends_on "
            "WHERE task_id=:task_id",
            {"depends_on": outsider.id, "task_id": dependent.id},
        ),
        (
            "DELETE FROM task_dependencies WHERE task_id=:task_id",
            {"task_id": dependent.id},
        ),
    )
    for statement, parameters in immutable_statements:
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(text(statement), parameters)

    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id) "
                    "VALUES (:task_id, :depends_on)"
                ),
                {"task_id": outsider.id, "depends_on": prerequisite.id},
            )


def test_campaign_terminal_event_and_activity_follow_pre_execution_cancel(settings, database):
    _, _, campaign = campaign_fixture(database)
    campaigns = CampaignService(database)
    before = campaigns.get(campaign.id)
    tasks = TaskService(database, settings)

    task, _ = tasks.submit(
        EchoInput(
            kind="echo",
            payload={"text": "cancel me"},
            campaign_id=campaign.id,
        ),
        None,
    )
    after_attach = campaigns.get(campaign.id)
    assert after_attach.updated_at > before.updated_at

    cancelled = tasks.cancel(task.id)
    assert cancelled.status == State.CANCELLED

    after_cancel = campaigns.get(campaign.id)
    assert after_cancel.updated_at > after_attach.updated_at
    events = campaigns.events(campaign.id, 100, 0).items
    terminal = [
        event
        for event in events
        if event.event == "task_finished" and event.task_id == task.id
    ]
    assert len(terminal) == 1
    assert terminal[0].details["status"] == State.CANCELLED
