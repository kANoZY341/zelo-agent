"""v0.15 R5: inspecting curated source candidates must not create authority.

Exercises the real authenticated Core API and migrated isolated test database,
not imported upstream instructions, runtimes or optional engine scripts.
"""

from sqlalchemy import func, select

from app.models import (
    AgentInstance,
    Project,
    SkillLearningProposalRecord,
    SkillPublicationClaimRecord,
    Task,
)
from tests.conftest import AUTH

BASE = "/integration-candidates/community"
PERSISTED = (
    AgentInstance,
    Project,
    Task,
    SkillLearningProposalRecord,
    SkillPublicationClaimRecord,
)


def _persistence_snapshot(store):
    with store.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model)) or 0 for model in PERSISTED
        )


def test_curated_references_remain_inert_after_real_core_operator_inspection(client):
    """GET proposals may describe source and drafts, but never activate them."""
    state = client.app.state
    before_db = _persistence_snapshot(state.storage)
    before_agents = state.agent_registry.version
    before_skills = state.skill_registry.version
    before_workers = (
        tuple(item.worker_id for item in state.research_registry.list()),
        tuple(item.worker_id for item in state.python_registry.list()),
        tuple(item.worker_id for item in state.api_worker_registry.list()),
        tuple(item.worker_id for item in state.browser_worker_registry.list()),
        tuple(item.worker_id for item in state.computer_worker_registry.list()),
        tuple(item.worker_id for item in state.shell_worker_registry.list()),
    )

    source = client.get(BASE, headers=AUTH)
    assert source.status_code == 200, source.text
    inventory = source.json()
    assert inventory["worker_registered"] is False
    assert inventory["context_mode_source_imported"] is False
    assert {item["license"] for item in inventory["sources"]} == {"MIT", "Apache-2.0"}
    assert all(
        item["auto_activation"] is False and item["tool_grants"] == []
        for item in inventory["sources"]
    )

    skill_response = client.get(BASE + "/skills", headers=AUTH)
    assert skill_response.status_code == 200, skill_response.text
    draft_skills = skill_response.json()
    assert len(draft_skills) == 6
    assert all(
        item["skill"]["lifecycle"] == "draft"
        and not item["ready_for_promotion"]
        and item["skill"]["tools"] == []
        and item["skill"]["task_kinds"] == []
        and item["tool_grants"] == []
        for item in draft_skills
    )
    assert all(state.skill_registry.get(item["skill"]["skill_id"]) is None for item in draft_skills)

    blueprint_response = client.get(BASE + "/teams/engineering_quality", headers=AUTH)
    assert blueprint_response.status_code == 200, blueprint_response.text
    blueprint = blueprint_response.json()
    assert blueprint["ready_to_activate"] is False
    assert blueprint["managing_director_approval_required"] is True
    assert blueprint["tool_grants"] == []
    assert blueprint["worker_grants"] == []
    assert all(
        state.agent_registry.get_agent(role["agent_id"]) is None for role in blueprint["roles"]
    )

    assert client.get(BASE + "/architecture", headers=AUTH).status_code == 200
    assert client.get(BASE + "/architecture/mermaid", headers=AUTH).status_code == 200
    assert client.get(BASE, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post(BASE, headers=AUTH, json={}).status_code == 405

    assert _persistence_snapshot(state.storage) == before_db
    assert state.agent_registry.version == before_agents
    assert state.skill_registry.version == before_skills
    assert before_workers == (
        tuple(item.worker_id for item in state.research_registry.list()),
        tuple(item.worker_id for item in state.python_registry.list()),
        tuple(item.worker_id for item in state.api_worker_registry.list()),
        tuple(item.worker_id for item in state.browser_worker_registry.list()),
        tuple(item.worker_id for item in state.computer_worker_registry.list()),
        tuple(item.worker_id for item in state.shell_worker_registry.list()),
    )
