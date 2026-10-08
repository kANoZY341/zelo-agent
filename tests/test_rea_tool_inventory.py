"""REA's complete 139-tool catalog can be viewed but grants no execution."""

import hashlib
import json

import pytest

from app import rea_tool_inventory as inventory
from tests.conftest import AUTH


def test_operator_inventory_is_exact_and_has_no_grants():
    snapshot = inventory.rea_tool_inventory()
    assert snapshot.schema_version == "zelo-rea-operator-tool-inventory-v1"
    assert snapshot.tool_count == 139
    assert len(snapshot.tools) == 139
    assert [item.name for item in snapshot.tools] == sorted(item.name for item in snapshot.tools)
    assert len({item.name for item in snapshot.tools}) == 139
    assert snapshot.grants == ()
    assert snapshot.execution_enabled is False
    assert snapshot.worker_registered is False
    proved = [tool for tool in snapshot.tools if tool.qualification == "manual_fixed_fixture_only"]
    assert [tool.name for tool in proved] == ["analyze_javascript_application"]
    assert sum(tool.qualification == "unqualified" for tool in snapshot.tools) == 138


@pytest.mark.parametrize("tamper", ["modified", "oversize", "symlink"])
def test_tool_catalog_invalid_inputs_fail_closed(tmp_path, tamper):
    path = tmp_path / "inventory.json"
    if tamper == "modified":
        path.write_bytes(inventory.CATALOG.read_bytes() + b" ")
    elif tamper == "oversize":
        path.write_bytes(b"x" * (inventory.CATALOG_MAX_BYTES + 1))
    else:
        destination = tmp_path / "target.json"
        destination.write_bytes(inventory.CATALOG.read_bytes())
        path.symlink_to(destination)
    with pytest.raises(inventory.ReaCatalogRefusal):
        inventory.rea_tool_inventory(catalog_path=path)


@pytest.mark.parametrize("field", ["execution_enabled", "zelo_tool_grants", "tool_names"])
def test_catalog_contract_refuses_grants_and_unlisted_or_duplicate_names(
    tmp_path, monkeypatch, field
):
    data = json.loads(inventory.CATALOG.read_text(encoding="utf-8"))
    if field == "execution_enabled":
        data[field] = True
    elif field == "zelo_tool_grants":
        data[field] = ["analyze_javascript_application"]
    else:
        data[field][1] = data[field][0]
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "inventory.json"
    path.write_bytes(encoded)
    # Prove semantic validation independently of the pinned catalog digest.
    monkeypatch.setattr(inventory, "EXPECTED_CATALOG_SHA256", hashlib.sha256(encoded).hexdigest())
    with pytest.raises(inventory.ReaCatalogRefusal):
        inventory.rea_tool_inventory(catalog_path=path)


def test_operator_only_get_route_exposes_names_but_no_permissions(client):
    accepted = client.get("/integration-candidates/rea/tools", headers=AUTH)
    assert accepted.status_code == 200, accepted.text
    response = accepted.json()
    assert response["tool_count"] == 139
    assert response["execution_enabled"] is False
    assert response["worker_registered"] is False
    assert response["grants"] == []
    assert response["tools"][3] == {
        "name": "analyze_javascript_application",
        "qualification": "manual_fixed_fixture_only",
    }
    assert len(response["tools"]) == 139
    assert (
        client.get(
            "/integration-candidates/rea/tools", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    assert (
        client.post("/integration-candidates/rea/tools", headers=AUTH, json={}).status_code == 405
    )
    assert (
        client.get("/integration-candidates/rea", headers=AUTH).json()["execution_enabled"] is False
    )


def test_catalog_integrity_refusal_is_bounded_api_503(client, monkeypatch):
    from app import rea_candidate_api

    def refusal():
        raise inventory.ReaCatalogRefusal("internal_detail_must_not_leak")

    monkeypatch.setattr(rea_candidate_api, "rea_tool_inventory", refusal)
    response = client.get("/integration-candidates/rea/tools", headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "rea_catalog_unavailable"}
