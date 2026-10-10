"""Per-agent Skill Learning history is bounded, scoped and never executable."""

from uuid import uuid4

import pytest

from app.agent_learned_skill_history import agent_learning_history
from app.agent_specialist_profile import AgentSpecialistProfileService
from tests.conftest import AUTH

pytest_plugins = ("tests.test_skill_learning_store",)


def _history_url(instance_id):
    return f"/agents/{instance_id}/specialist-profile/learning"


def test_own_proposals_are_scoped_digest_bound_and_not_activated(database, proposal_case):
    store, agent, _task, _attempt, revision, ref = proposal_case
    item, created = store.submit(
        agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,)
    )
    assert created
    profile = AgentSpecialistProfileService(
        database, store.agent_factory, store.skill_registry
    ).get(agent.id)
    projection = agent_learning_history(profile, store)
    assert projection.agent_instance_id == agent.id
    assert projection.total_count == 1 and not projection.more_available
    assert projection.registry_binding_current
    assert not projection.automatically_learned_or_activated
    assert not projection.model_context_admission and not projection.permission_widening
    summary = projection.items[0]
    assert summary.proposal_id == item.proposal.proposal_id
    assert summary.proposal_status == "proposed"
    assert summary.proposed_procedure_id == item.proposal.proposed_revision.procedure_id
    assert summary.proposed_revision_sha256 == item.proposal.proposed_revision.content_sha256
    assert summary.review_recorded is False
    assert summary.promotion_evidence_linked is False
    assert summary.execution_enabled is False
    assert summary.procedure_content_included is False
    assert "instructions" not in summary.model_dump()
    assert "evidence_refs" not in summary.model_dump()
    other, total = store.list_for_agent(str(uuid4()), limit=32, offset=0)
    assert other == () and total == 0


def test_operator_api_shows_exact_agent_history_and_refuses_unauthorized(
    database, proposal_case, client
):
    store, agent, _task, _attempt, revision, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    client.app.state.agent_factory = store.agent_factory
    client.app.state.skill_registry = store.skill_registry
    client.app.state.skill_learning_store = store
    response = client.get(_history_url(agent.id), headers=AUTH)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["total_count"] == 1 and not view["more_available"]
    assert view["items"][0]["proposal_id"] == stored.proposal.proposal_id
    assert view["items"][0]["promotion_evidence_linked"] is False
    assert view["automatically_learned_or_activated"] is False
    assert (
        client.get(_history_url(agent.id), headers={"Authorization": "Bearer bad"}).status_code
        == 401
    )
    assert client.post(_history_url(agent.id), headers=AUTH).status_code == 405
    assert client.get(_history_url(str(uuid4())), headers=AUTH).status_code == 404


def test_corrupt_learning_records_fail_closed_without_raw_details(
    database, proposal_case, client, monkeypatch
):
    store, agent, *_ = proposal_case
    client.app.state.agent_factory = store.agent_factory
    client.app.state.skill_registry = store.skill_registry
    client.app.state.skill_learning_store = store

    def invalid(*_args, **_kwargs):
        raise ValueError("sensitive internal store details")

    monkeypatch.setattr(store, "list_for_agent", invalid)
    response = client.get(_history_url(agent.id), headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "agent_learning_history_unavailable"}
    assert "sensitive" not in response.text


def test_history_is_bounded_and_distinguishes_old_records_from_current_grants(
    database, proposal_case, monkeypatch
):
    store, agent, _task, _attempt, revision, ref = proposal_case
    store.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    profile = AgentSpecialistProfileService(
        database, store.agent_factory, store.skill_registry
    ).get(agent.id)
    real = store.list_for_agent

    def limited(instance_id, *, limit, offset):
        assert instance_id == agent.id and limit == 32 and offset == 0
        items, count = real(instance_id, limit=limit, offset=offset)
        return items, count + 100

    monkeypatch.setattr(store, "list_for_agent", limited)
    view = agent_learning_history(profile, store)
    assert len(view.items) == 1
    assert view.total_count == 101
    assert view.more_available
    assert not view.permission_widening


def test_invalid_history_source_refused(database, proposal_case):
    store, agent, *_ = proposal_case
    profile = AgentSpecialistProfileService(
        database, store.agent_factory, store.skill_registry
    ).get(agent.id)
    with pytest.raises(ValueError, match="agent_learning_history_source_invalid"):
        agent_learning_history(None, store)
    with pytest.raises(ValueError, match="agent_learning_history_source_invalid"):
        agent_learning_history(profile, None)


def test_promotion_link_is_evidence_not_automatic_activation(database, proposal_case, monkeypatch):
    store, agent, _task, _attempt, revision, ref = proposal_case
    stored, _ = store.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    profile = AgentSpecialistProfileService(
        database, store.agent_factory, store.skill_registry
    ).get(agent.id)
    observed = []

    def archived_links(proposal_ids):
        observed.append(proposal_ids)
        return {stored.proposal.proposal_id: object()}

    monkeypatch.setattr(store, "promotion_links_for", archived_links)
    view = agent_learning_history(profile, store)
    assert observed == [(stored.proposal.proposal_id,)]
    assert view.items[0].promotion_evidence_linked
    assert not view.items[0].execution_enabled
    assert not view.items[0].skill_registry_promoted_by_this_view
    assert not view.automatically_learned_or_activated


def test_paused_agent_history_never_claims_current_execution(database, proposal_case):
    from app.models import AgentInstance

    store, agent, _task, _attempt, revision, ref = proposal_case
    store.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    with database.transaction() as session:
        session.get(AgentInstance, agent.id).status = "paused"
    profile = AgentSpecialistProfileService(
        database, store.agent_factory, store.skill_registry
    ).get(agent.id)
    view = agent_learning_history(profile, store)
    assert view.inspection_state == "instance_inactive"
    assert len(view.items) == 1
    assert not view.automatically_learned_or_activated
    assert not view.items[0].execution_enabled
