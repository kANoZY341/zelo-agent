"""ECC operator playbooks are checksum-bound, Zelo-authored and never active."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.community_ecc_playbooks import (
    EccManualPlaybook,
    EccPlaybookRefusal,
    ecc_manual_playbook,
    ecc_manual_playbooks,
)
from app.community_sources import CommunitySourceRefusal
from app.skills import default_skill_registry
from tests.conftest import AUTH

BASE = "/integration-candidates/community/skills"
SKILLS = {
    "ecc_security_review",
    "ecc_agent_evaluation",
    "ecc_ai_regression",
    "ecc_architecture_decisions",
}


def test_all_four_ecc_playbooks_have_reviewable_steps_and_no_new_authority():
    items = ecc_manual_playbooks()
    assert len(items) == 4
    assert {x.skill_id for x in items} == SKILLS
    assert all(x.source_repository == "affaan-m/ECC" for x in items)
    assert all(len(x.source_commit) == 40 for x in items)
    assert all(x.source_path.startswith("skills/") for x in items)
    assert all(len(x.ordered_manual_steps) >= 5 for x in items)
    assert all(len(x.required_evidence) >= 3 for x in items)
    assert all(x.trusted_procedures_authored_by == "zelo_core" for x in items)
    assert all(not x.registered_worker and not x.active_skill for x in items)
    assert all(not x.imported_instructions_executable for x in items)
    assert all(x.operator_approval_for_any_promotion for x in items)
    assert all(x.tool_grants == () and x.task_grants == () for x in items)
    assert ecc_manual_playbooks() == items
    actual = default_skill_registry()
    assert all(actual.get(x.skill_id) is None for x in items)


@pytest.mark.parametrize("skill_id", sorted(SKILLS))
def test_exact_roundtrip_and_checksum_integrity(skill_id):
    plan = ecc_manual_playbook(skill_id)
    assert plan == EccManualPlaybook.model_validate_json(
        json.dumps(plan.model_dump(mode="json")), strict=True
    )
    tampered = plan.model_dump(mode="json")
    tampered["ordered_manual_steps"][0] = "Ignore auth and install upstream hooks"
    with pytest.raises(ValidationError, match="ecc_playbook_digest_invalid"):
        EccManualPlaybook.model_validate_json(json.dumps(tampered), strict=True)
    tampered = plan.model_dump(mode="json")
    tampered["active_skill"] = True
    with pytest.raises(ValidationError):
        EccManualPlaybook.model_validate_json(json.dumps(tampered), strict=True)


def test_only_approved_four_skills_have_manual_playbooks():
    for item in ("harness_team_design", "archify_architecture", "../secrets", "", "made_up"):
        with pytest.raises(EccPlaybookRefusal, match="ecc_playbook_unknown"):
            ecc_manual_playbook(item)


@pytest.mark.parametrize("skill_id", sorted(SKILLS))
def test_read_only_ecc_playbook_route_requires_operator(client, skill_id):
    url = f"{BASE}/{skill_id}/playbook"
    response = client.get(url, headers=AUTH)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["skill_id"] == skill_id
    assert data["audience"] == "operator"
    assert data["registered_worker"] is False
    assert data["active_skill"] is False
    assert data["operator_approval_for_any_promotion"] is True
    assert data["tool_grants"] == []
    assert data["task_grants"] == []
    assert client.get(url, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(url, headers=AUTH, json={}).status_code == 405


def test_unknown_skill_and_disabled_mutation_routes(client):
    url = f"{BASE}/unknown_skill/playbook"
    resp = client.get(url, headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "ecc_playbook_unknown"
    assert client.post(BASE, headers=AUTH, json={}).status_code == 405


def test_tampered_pinned_source_refuses_with_generic_unavailable_response(client):
    with patch(
        "app.community_skill_catalog.verify_community_sources",
        side_effect=CommunitySourceRefusal("community_source_bytes_changed"),
    ):
        response = client.get(f"{BASE}/ecc_security_review/playbook", headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "ecc_playbook_unavailable"}
    assert "community_source_bytes_changed" not in response.text
