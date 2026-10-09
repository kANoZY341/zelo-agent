"""Specialist Dot profile is Core-owned metadata, never a grant or Dot runtime."""

from sqlalchemy import func, select

from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec, research_agent_spec
from app.agent_specialist_profile import AgentSpecialistProfileService
from app.models import AgentInstance, Project, Task
from app.skills import default_skill_registry
from tests.conftest import AUTH


def _setup(database, client):
    baseline = research_agent_spec()
    data = baseline.model_dump(mode="json")
    data.update(
        {
            "instructions": "Use research evidence only; never auto-run tools.",
            "skill_ids": ["evidence_retrieval"],
            "space_permissions": ["project_workspace"],
            "skill_learning": "propose",
        }
    )
    spec = AgentSpec.model_validate(data)
    factory = AgentFactory(AgentRegistry((spec,)))
    with database.transaction() as session:
        project = Project(name="Specialist", summary="Specialist profile fixture", status="active")
        session.add(project)
        session.flush()
        instance = AgentInstance(
            agent_id=spec.agent_id,
            agent_version=spec.version,
            registry_version=factory.registry.version,
            project_id=project.id,
            spec=spec.model_dump(mode="json"),
            status="active",
        )
        session.add(instance)
        session.flush()
        ids = (project.id, instance.id)
    client.app.state.agent_factory = factory
    skills = default_skill_registry()
    client.app.state.skill_registry = skills
    return ids[0], ids[1], factory, skills


def test_specialist_profile_shows_provenance_without_worker_or_space_grants(database, client):
    project_id, instance_id, factory, skills = _setup(database, client)
    response = client.get(f"/agents/{instance_id}/specialist-profile", headers=AUTH)
    assert response.status_code == 200, response.text
    profile = response.json()
    assert profile["agent_id"] == "research"
    assert profile["project_id"] == project_id
    assert profile["registry_version"] == factory.registry.version
    assert profile["registry_binding_current"] is True
    assert profile["inspection_state"] == "current"
    assert profile["spec_instructions"].startswith("Use research evidence only")
    assert profile["instruction_origin"] == "trusted_agent_spec"
    assert profile["memory_namespace"] == "agent/research"
    assert profile["configured_skill_learning"] == "propose"
    assert profile["qualified_skill_learning_activation"] is False
    assert len(profile["skill_declarations"]) == 1
    evidence = profile["skill_declarations"][0]
    assert evidence["skill_id"] == "evidence_retrieval"
    assert evidence["reason"] == "compatible_active_catalog_entry"
    assert evidence["declared_eligible"] is True
    assert evidence["effective_execution_granted"] is False
    assert profile["declared_spaces"][0]["space_id"] == "project_workspace"
    assert profile["declared_spaces"][0]["effective_access_verified"] is False
    assert profile["declared_tools"]
    assert all(not item["verified_for_task_execution"] for item in profile["declared_tools"])
    assert profile["effective_space_access"] == []
    assert profile["effective_worker_or_tool_grants"] == []
    assert profile["upstream_dot_runtime_loaded"] is False
    assert profile["operation_or_permission_mutated"] is False
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0
    standalone = AgentSpecialistProfileService(database, factory, skills).get(instance_id)
    assert standalone.model_dump(mode="json") == profile


def test_profile_stale_registry_does_not_trust_obsolete_instructions(database, client):
    _project_id, instance_id, _factory, skills = _setup(database, client)
    client.app.state.agent_factory = AgentFactory(AgentRegistry())
    response = client.get(f"/agents/{instance_id}/specialist-profile", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inspection_state"] == "stale"
    assert body["registry_binding_current"] is False
    assert body["spec_instructions"] == ""
    assert body["instruction_origin"] == "unavailable"
    assert body["declared_spaces"] == []
    assert body["declared_tools"] == []
    assert body["skill_declarations"] == []


def test_paused_and_project_inactive_are_explicitly_visible(database, client):
    project_id, instance_id, _factory, _skills = _setup(database, client)
    with database.transaction() as session:
        session.get(AgentInstance, instance_id).status = "paused"
    resp = client.get(f"/agents/{instance_id}/specialist-profile", headers=AUTH)
    assert resp.json()["inspection_state"] == "instance_inactive"
    with database.transaction() as session:
        session.get(AgentInstance, instance_id).status = "active"
        session.get(Project, project_id).status = "paused"
    resp = client.get(f"/agents/{instance_id}/specialist-profile", headers=AUTH)
    assert resp.json()["inspection_state"] == "project_inactive"


def test_missing_skills_and_incompatible_skills_get_denial_reasons(database, client):
    _project_id, instance_id, factory, _skills = _setup(database, client)
    from app.skills import SkillRegistry, SkillSpec

    incompatible = SkillSpec(
        skill_id="evidence_retrieval",
        version=2,
        description="Mismatched source skill",
        required_capabilities=("code_change",),
        procedures=("evidence_retrieval",),
        lifecycle="active",
    )
    client.app.state.skill_registry = SkillRegistry((incompatible,))
    result = client.get(f"/agents/{instance_id}/specialist-profile", headers=AUTH)
    assert result.status_code == 200
    claim = result.json()["skill_declarations"][0]
    assert claim["reason"] == "agent_skill_incompatible"
    assert claim["declared_eligible"] is False
    client.app.state.skill_registry = SkillRegistry()
    result = client.get(f"/agents/{instance_id}/specialist-profile", headers=AUTH)
    claim = result.json()["skill_declarations"][0]
    assert claim["reason"] == "skill_not_registered"
    assert claim["lifecycle"] == "not_registered"
    assert claim["version"] is None


def test_operator_auth_read_only_and_missing_agent(database, client):
    _project_id, instance_id, _factory, _skills = _setup(database, client)
    endpoint = f"/agents/{instance_id}/specialist-profile"
    assert client.get(endpoint, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(endpoint, headers=AUTH).status_code == 405
    absent = "/agents/00000000-0000-0000-0000-000000000001/specialist-profile"
    assert client.get(absent, headers=AUTH).status_code == 404
