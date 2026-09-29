"""v0.14 bounded parent-child delegation contract tests."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_delegations import AgentDelegationService
from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec
from app.agent_instances import AgentInstanceService
from app.campaign_control import CampaignControlService
from app.memory import MemoryService
from app.models import CampaignBudgetReservation, utcnow
from app.policy import DomainError
from app.schemas import (
    AgentDelegationCreate,
    AgentDelegationRevoke,
    AgentInstanceCreate,
    CampaignBudgetCommit,
    CampaignBudgetReserve,
    CampaignBudgetSet,
)
from tests.conftest import AUTH


def spec(
    agent_id: str,
    *,
    allow_subagents: bool = False,
    max_subagents: int = 0,
    capabilities: tuple[str, ...] = ("code_change",),
) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        role=agent_id,
        objective=f"Bounded {agent_id} work.",
        capabilities=capabilities,
        allowed_task_kinds=("developer_run",),
        allowed_tools=("repo_status",),
        permissions=("workspace_read",),
        procedures=("implementation",),
        memory_namespace=f"agent/{agent_id}",
        lifecycle="active",
        max_active_instances=1,
        allow_subagents=allow_subagents,
        max_subagents=max_subagents,
    )


def create_project_goal_campaign(client, name: str = "Delegation") -> tuple[dict, dict, dict]:
    project = client.post(
        "/projects",
        headers=AUTH,
        json={"name": name, "summary": "Delegation tests."},
    ).json()
    goal_response = client.post(
        f"/projects/{project['id']}/goals",
        headers=AUTH,
        json={
            "title": "Ship bounded delegation",
            "spec": {
                "objective": "Delegation contracts are bounded",
                "success_criteria": ["contracts persist"],
                "constraints": ["no execution"],
            },
        },
    )
    assert goal_response.status_code == 201, goal_response.text
    goal = goal_response.json()
    campaign_response = client.post(
        f"/goals/{goal['id']}/campaigns",
        headers=AUTH,
        json={"title": "Delegation campaign", "summary": "Bounded work."},
    )
    assert campaign_response.status_code == 201, campaign_response.text
    return project, goal, campaign_response.json()


def services(database, *specs: AgentSpec):
    factory = AgentFactory(AgentRegistry(specs))
    memories = MemoryService(database)
    instances = AgentInstanceService(database, factory, memories)
    controls = CampaignControlService(database)
    delegations = AgentDelegationService(database, factory, controls)
    return instances, controls, delegations


def create_instance(instances, agent_id: str, project_id: str):
    return instances.create(AgentInstanceCreate(agent_id=agent_id, project_id=project_id))


def reserve(controls, campaign_id: str, owner_id: str, *, key: str, units: int = 3):
    controls.set_budget(campaign_id, "agent_steps", CampaignBudgetSet(limit_units=100))
    return controls.reserve_budget(
        campaign_id,
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=owner_id,
            idempotency_key=key,
            units=units,
            ttl_seconds=3600,
        ),
    )


def request(child_id: str, campaign_id: str, reservation_id: str, *, key: str, max_depth: int = 2):
    return AgentDelegationCreate(
        child_instance_id=child_id,
        campaign_id=campaign_id,
        budget_reservation_id=reservation_id,
        idempotency_key=key,
        objective="Implement the bounded delegated change.",
        allowed_task_kinds=["developer_run"],
        required_capabilities=["code_change"],
        max_depth=max_depth,
        budget_resource="agent_steps",
        budget_units=3,
    )


def test_create_replay_list_and_revoke_releases_budget(database, client):
    project, _goal, campaign = create_project_goal_campaign(client)
    parent_spec = spec("manager", allow_subagents=True, max_subagents=2)
    child_spec = spec("producer")
    instances, controls, delegations = services(database, parent_spec, child_spec)
    parent = create_instance(instances, "manager", project["id"])
    child = create_instance(instances, "producer", project["id"])
    reservation = reserve(controls, campaign["id"], parent.id, key="budget:one")

    body = request(child.id, campaign["id"], reservation.id, key="delegate:one")
    created = delegations.create(parent.id, body)
    replayed = delegations.create(parent.id, body)

    assert replayed == created
    assert created.parent_instance_id == parent.id
    assert created.child_instance_id == child.id
    assert created.depth == 1
    assert created.max_depth == 2
    assert created.status == "active"
    assert delegations.get(created.id) == created
    assert delegations.list(parent.id, status="active", limit=20, offset=0).total == 1

    with pytest.raises(DomainError, match="bound to an active agent delegation"):
        controls.release_budget(campaign["id"], reservation.id)
    with pytest.raises(DomainError, match="bound to an active agent delegation"):
        controls.commit_budget(
            campaign["id"],
            reservation.id,
            CampaignBudgetCommit(actual_units=1),
        )
    assert controls.list_reservations(campaign["id"], 20, 0).items[0].status == "reserved"
    assert delegations.get(created.id).status == "active"

    revoked = delegations.revoke(
        created.id,
        AgentDelegationRevoke(reason="Operator stopped delegated work."),
    )
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
    assert revoked.revocation_reason == "Operator stopped delegated work."
    released = controls.list_reservations(campaign["id"], 20, 0).items[0]
    assert released.id == reservation.id
    assert released.status == "released"

    assert (
        delegations.revoke(
            created.id,
            AgentDelegationRevoke(reason="Second revoke is idempotent."),
        )
        == revoked
    )


def test_delegation_fails_closed_for_authority_scope_and_idempotency(database, client):
    project, _goal, campaign = create_project_goal_campaign(client)
    other_project, _goal2, _campaign2 = create_project_goal_campaign(client, "Other")
    parent_spec = spec("manager", allow_subagents=True, max_subagents=1)
    child_spec = spec("producer")
    other_spec = spec("other")
    instances, controls, delegations = services(database, parent_spec, child_spec, other_spec)
    parent = create_instance(instances, "manager", project["id"])
    child = create_instance(instances, "producer", project["id"])
    other = create_instance(instances, "other", other_project["id"])
    reservation = reserve(controls, campaign["id"], parent.id, key="budget:scope")

    body = request(child.id, campaign["id"], reservation.id, key="delegate:scope")
    created = delegations.create(parent.id, body)

    conflict = body.model_copy(update={"objective": "Different objective"})
    with pytest.raises(DomainError, match="idempotency conflict"):
        delegations.create(parent.id, conflict)

    second_reservation = controls.reserve_budget(
        campaign["id"],
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=parent.id,
            idempotency_key="budget:other-project",
            units=3,
            ttl_seconds=3600,
        ),
    )
    with pytest.raises(DomainError, match="one Project"):
        delegations.create(
            parent.id,
            request(
                other.id,
                campaign["id"],
                second_reservation.id,
                key="delegate:other-project",
            ),
        )

    no_authority_instances, no_authority_controls, no_authority_delegations = services(
        database,
        spec("plain_parent"),
        spec("plain_child"),
    )
    second_project, _goal3, second_campaign = create_project_goal_campaign(client, "No auth")
    plain_parent = create_instance(no_authority_instances, "plain_parent", second_project["id"])
    plain_child = create_instance(no_authority_instances, "plain_child", second_project["id"])
    no_authority_reservation = reserve(
        no_authority_controls,
        second_campaign["id"],
        plain_parent.id,
        key="budget:noauth",
    )
    with pytest.raises(DomainError, match="not authorized"):
        no_authority_delegations.create(
            plain_parent.id,
            request(
                plain_child.id,
                second_campaign["id"],
                no_authority_reservation.id,
                key="delegate:noauth",
            ),
        )

    assert created.status == "active"


def test_delegation_requires_valid_budget_and_child_capabilities(database, client):
    project, _goal, campaign = create_project_goal_campaign(client)
    parent_spec = spec("manager", allow_subagents=True, max_subagents=2)
    capable_child_spec = spec("capable")
    weak_child_spec = spec("weak", capabilities=("research",))
    instances, controls, delegations = services(
        database,
        parent_spec,
        capable_child_spec,
        weak_child_spec,
    )
    parent = create_instance(instances, "manager", project["id"])
    capable = create_instance(instances, "capable", project["id"])
    weak = create_instance(instances, "weak", project["id"])

    wrong_owner = reserve(controls, campaign["id"], capable.id, key="budget:wrong-owner")
    with pytest.raises(DomainError, match="budget proof"):
        delegations.create(
            parent.id,
            request(
                capable.id,
                campaign["id"],
                wrong_owner.id,
                key="delegate:wrong-owner",
            ),
        )

    correct = controls.reserve_budget(
        campaign["id"],
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=parent.id,
            idempotency_key="budget:correct",
            units=3,
            ttl_seconds=3600,
        ),
    )
    with pytest.raises(DomainError, match="lacks required capability"):
        delegations.create(
            parent.id,
            request(
                weak.id,
                campaign["id"],
                correct.id,
                key="delegate:weak",
            ),
        )


def test_nested_delegation_inherits_depth_and_blocks_multiple_parents(database, client):
    project, _goal, campaign = create_project_goal_campaign(client)
    root_spec = spec("root", allow_subagents=True, max_subagents=2)
    middle_spec = spec("middle", allow_subagents=True, max_subagents=2)
    leaf_spec = spec("leaf")
    instances, controls, delegations = services(database, root_spec, middle_spec, leaf_spec)
    root = create_instance(instances, "root", project["id"])
    middle = create_instance(instances, "middle", project["id"])
    leaf = create_instance(instances, "leaf", project["id"])

    root_budget = reserve(controls, campaign["id"], root.id, key="budget:root")
    first = delegations.create(
        root.id,
        request(middle.id, campaign["id"], root_budget.id, key="delegate:root", max_depth=2),
    )
    assert first.depth == 1

    cycle_budget = controls.reserve_budget(
        campaign["id"],
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=middle.id,
            idempotency_key="budget:cycle",
            units=3,
            ttl_seconds=3600,
        ),
    )
    with pytest.raises(DomainError, match="cycle"):
        delegations.create(
            middle.id,
            request(
                root.id,
                campaign["id"],
                cycle_budget.id,
                key="delegate:cycle",
                max_depth=2,
            ),
        )

    middle_budget = controls.reserve_budget(
        campaign["id"],
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=middle.id,
            idempotency_key="budget:middle",
            units=3,
            ttl_seconds=3600,
        ),
    )
    nested = delegations.create(
        middle.id,
        request(
            leaf.id,
            campaign["id"],
            middle_budget.id,
            key="delegate:middle",
            max_depth=2,
        ),
    )
    assert nested.depth == 2
    assert nested.max_depth == 2

    extra_budget = controls.reserve_budget(
        campaign["id"],
        CampaignBudgetReserve(
            resource="agent_steps",
            owner_id=root.id,
            idempotency_key="budget:leaf-second-parent",
            units=3,
            ttl_seconds=3600,
        ),
    )
    with pytest.raises(DomainError, match="active parent"):
        delegations.create(
            root.id,
            request(
                leaf.id,
                campaign["id"],
                extra_budget.id,
                key="delegate:leaf-second-parent",
                max_depth=2,
            ),
        )


def test_expired_bound_budget_revokes_delegation_atomically(database, client):
    project, _goal, campaign = create_project_goal_campaign(client)
    parent_spec = spec("manager", allow_subagents=True, max_subagents=2)
    child_spec = spec("producer")
    instances, controls, delegations = services(database, parent_spec, child_spec)
    parent = create_instance(instances, "manager", project["id"])
    child = create_instance(instances, "producer", project["id"])
    reservation = reserve(controls, campaign["id"], parent.id, key="budget:expiry")
    created = delegations.create(
        parent.id,
        request(child.id, campaign["id"], reservation.id, key="delegate:expiry"),
    )

    with database.transaction() as session:
        stored = session.get(CampaignBudgetReservation, reservation.id)
        assert stored is not None
        stored.expires_at = utcnow() - timedelta(seconds=1)

    recovered = controls.recover_expired(campaign["id"])
    assert recovered.expired_budget_reservations == 1

    expired = controls.list_reservations(campaign["id"], 20, 0).items[0]
    assert expired.status == "expired"
    revoked = delegations.get(created.id)
    assert revoked.status == "revoked"
    assert revoked.revocation_reason == "budget_reservation_expired"
    assert revoked.revoked_at is not None


def test_database_guards_immutable_contract_and_cycle(database, client):
    project, _goal, campaign = create_project_goal_campaign(client)
    root_spec = spec("root", allow_subagents=True, max_subagents=2)
    middle_spec = spec("middle", allow_subagents=True, max_subagents=2)
    instances, controls, delegations = services(database, root_spec, middle_spec)
    root = create_instance(instances, "root", project["id"])
    middle = create_instance(instances, "middle", project["id"])
    root_budget = reserve(controls, campaign["id"], root.id, key="budget:guard")
    created = delegations.create(
        root.id,
        request(middle.id, campaign["id"], root_budget.id, key="delegate:guard"),
    )

    with pytest.raises(IntegrityError, match="owns budget reservation"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE campaign_budget_reservations SET status = 'released' "
                    "WHERE id = :reservation_id"
                ),
                {"reservation_id": root_budget.id},
            )

    with pytest.raises(IntegrityError, match="immutable"):
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE agent_delegations SET objective = 'tampered' WHERE id = :delegation_id"
                ),
                {"delegation_id": created.id},
            )

    with pytest.raises(IntegrityError, match="cannot be deleted"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM agent_delegations WHERE id = :delegation_id"),
                {"delegation_id": created.id},
            )
