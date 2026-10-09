"""New YouTube and Trading departments remain permissionless draft proposals."""

import json

import pytest
from pydantic import ValidationError

from app.agent_factory import default_agent_registry
from app.community_harness import TeamBlueprint, propose_team
from tests.conftest import AUTH

EXPECTED = {
    "youtube_content": (
        "youtube",
        "youtube_content",
        "youtube_research",
        "youtube_script_writer",
    ),
    "trading_research": (
        "trading",
        "trading_intelligence",
        "trading_risk",
        "market_researcher",
    ),
}


@pytest.mark.parametrize("template", sorted(EXPECTED))
def test_four_level_teams_have_fixed_identity_and_no_action_grants(template):
    draft = propose_team(template)
    assert draft == propose_team(template)
    assert tuple(role.agent_id for role in draft.roles) == EXPECTED[template]
    assert tuple(role.organizational_level for role in draft.roles) == (
        "vp",
        "director",
        "manager",
        "specialist",
    )
    assert draft.parent_ids == (None, *EXPECTED[template][:3])
    assert draft.managing_director_approval_required and not draft.ready_to_activate
    assert not draft.tasks_created
    assert draft.tool_grants == () and draft.worker_grants == ()
    assert not any(role.allowed_tools or role.allowed_task_kinds for role in draft.roles)
    assert not any(role.space_permissions or role.permissions for role in draft.roles)
    assert not any(role.computer or role.allow_subagents for role in draft.roles)
    assert not any(role.skill_ids or role.skill_learning != "disabled" for role in draft.roles)
    assert all(role.lifecycle == "draft" and role.review_required for role in draft.roles)
    active = default_agent_registry()
    assert all(active.get_agent(role.agent_id) is None for role in draft.roles)
    assert TeamBlueprint.model_validate_json(draft.model_dump_json(), strict=True) == draft


@pytest.mark.parametrize("template", sorted(EXPECTED))
def test_signed_immutable_blueprint_rejects_activation_and_widening(template):
    draft = propose_team(template)
    changed = draft.model_dump(mode="json")
    changed["roles"][3]["allowed_tools"] = ["shell_execution"]
    with pytest.raises(ValidationError):
        TeamBlueprint.model_validate_json(json.dumps(changed), strict=True)
    changed = draft.model_dump(mode="json")
    changed["roles"][3]["lifecycle"] = "active"
    with pytest.raises(ValidationError, match="harness_blueprint_execution_forbidden"):
        TeamBlueprint.model_validate_json(json.dumps(changed), strict=True)
    changed = draft.model_dump(mode="json")
    changed["parent_ids"][3] = "unrelated_agent"
    with pytest.raises(ValidationError, match="harness_reporting_graph_invalid"):
        TeamBlueprint.model_validate_json(json.dumps(changed), strict=True)


@pytest.mark.parametrize("template", sorted(EXPECTED))
def test_operator_can_preview_without_creating_agents_or_tasks(client, template):
    url = f"/integration-candidates/community/teams/{template}"
    response = client.get(url, headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template"] == template
    assert body["tasks_created"] is False
    assert body["ready_to_activate"] is False
    assert body["tool_grants"] == []
    assert body["worker_grants"] == []
    assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post(url, headers=AUTH, json={}).status_code == 405


def test_unlisted_team_templates_refused(client):
    with pytest.raises(ValueError, match="harness_template_unknown"):
        propose_team("trade_executor")
    assert (
        client.get(
            "/integration-candidates/community/teams/trade_executor", headers=AUTH
        ).status_code
        == 422
    )
