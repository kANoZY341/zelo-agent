"""R0 default Core Agent/Skill/Space scope is not expanded by dormant imports.

This is an isolated in-process source regression, not live Worker qualification
or an authorization to publish the v0.15 release.
"""

from app.community_skill_catalog import community_skill_candidates


def test_default_activations_are_only_bounded_trusted_agents_and_no_space_grants(client, settings):
    state = client.app.state
    agents = {spec.agent_id: spec for spec in state.agent_registry.list_agents()}
    assert set(agents) == {"coding", "research"}
    assert state.agent_activation_report.status == "accepted"
    assert state.agent_activation_report.registry_version == state.agent_registry.version
    assert (
        state.agent_activation_report.capability_registry_version
        == state.capability_registry.version
    )

    for spec in agents.values():
        assert spec.lifecycle == "active"
        assert spec.organizational_level == "specialist"
        assert spec.computer is None
        assert spec.space_permissions == ()
        assert not spec.allow_subagents
        assert spec.max_subagents == 0
        assert set(spec.allowed_tools) <= {
            "repo_status",
            "list_workspace_files",
            "read_workspace_file",
        }
    assert agents["coding"].allowed_task_kinds == ("developer_run",)
    assert agents["research"].allowed_task_kinds == ("research_run",)
    assert state.agent_registry.get_agent("reverse_engineering") is None

    # Registry metadata for Computer/Shell/Python does not register a Worker.
    skills = {spec.skill_id: spec for spec in state.skill_registry.list()}
    assert "computer_navigation" in skills
    assert "computer_observation" in skills
    assert "shell_execution" in skills
    assert "python_execution" in skills
    assert not state.computer_worker_registry.list()
    assert not state.browser_worker_registry.list()
    assert not state.shell_worker_registry.list()

    for candidate in community_skill_candidates():
        assert candidate.skill.lifecycle == "draft"
        assert not candidate.runtime_authority
        assert not candidate.publication_authority
        assert candidate.skill.task_kinds == ()
        assert candidate.skill.tools == ()
        assert state.skill_registry.get(candidate.skill.skill_id) is None

    assert state.skill_registry.get("rea_investigation") is None
    assert settings.skill_publication_external_merge_enabled is False
    assert settings.v015_release_approved_main_sha is None
    assert settings.youtube_research_approved_spec_sha256 is None
    assert settings.youtube_company_approved_registry_sha256 is None
