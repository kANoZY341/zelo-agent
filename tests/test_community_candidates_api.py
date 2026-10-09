"""Authenticated review surfaces expose imported candidates without activation."""

from tests.conftest import AUTH

BASE = "/integration-candidates/community"


def test_operator_only_source_catalog_is_exact_and_inert(client):
    result = client.get(BASE, headers=AUTH)
    assert result.status_code == 200, result.text
    data = result.json()
    assert len(data["sources"]) == 5
    assert {x["name"] for x in data["sources"]} == {
        "open_code_review",
        "ecc",
        "harness",
        "archify",
        "opendots",
    }
    assert data["context_mode_source_imported"] is False
    assert data["worker_registered"] is False
    assert data["license_review_required_before_activation"] is True
    assert all(x["auto_activation"] is False and x["tool_grants"] == [] for x in data["sources"])
    assert client.get(BASE, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post(BASE, headers=AUTH, json={}).status_code == 405


def test_operator_can_inspect_six_draft_skills_without_adding_live_grants(client):
    data = client.get(BASE + "/skills", headers=AUTH)
    assert data.status_code == 200, data.text
    items = data.json()
    assert len(items) == 6
    assert all(item["skill"]["lifecycle"] == "draft" for item in items)
    assert all(item["skill"]["task_kinds"] == [] for item in items)
    assert all(item["skill"]["tools"] == [] and item["tool_grants"] == [] for item in items)
    assert all(item["ready_for_promotion"] is False for item in items)
    assert all(item["reviewed_steps"] for item in items)
    assert client.post(BASE + "/skills", headers=AUTH, json={}).status_code == 405


def test_operator_only_harness_blueprint_does_not_create_agents(client):
    response = client.get(BASE + "/teams/engineering_quality", headers=AUTH)
    assert response.status_code == 200, response.text
    blueprint = response.json()
    assert blueprint["template"] == "engineering_quality"
    assert len(blueprint["roles"]) == 4
    assert blueprint["managing_director_approval_required"] is True
    assert blueprint["ready_to_activate"] is False
    assert blueprint["worker_grants"] == [] and blueprint["tool_grants"] == []
    assert client.get(BASE + "/teams/unapproved", headers=AUTH).status_code == 422
    assert client.post(BASE + "/teams/engineering_quality", headers=AUTH).status_code == 405
    assert client.get("/agent-factory/activation", headers=AUTH).status_code == 200


def test_archify_candidate_is_inspectable_json_and_mermaid_not_live_render(client):
    response = client.get(BASE + "/architecture", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["diagram_type"] == "architecture"
    assert len(data["components"]) == 9
    assert data["meta"]["output"].endswith(".html")
    preview = client.get(BASE + "/architecture/mermaid", headers=AUTH)
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/plain")
    assert preview.text.startswith("flowchart TD\n")
    assert (
        client.get(BASE + "/architecture", headers={"Authorization": "Bearer bad"}).status_code
        == 401
    )
