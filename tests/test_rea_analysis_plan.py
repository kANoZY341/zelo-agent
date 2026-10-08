"""Read-only REA analysis-plan display never grants an execution capability."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app import rea_analysis_plan as plans
from app.models import Task
from app.rea_analysis_plan import ReaAnalysisPlanPreview
from app.rea_operator_targets import ReaOperatorTargetStore
from app.worker_authority import authority_policy_version
from tests.conftest import AUTH
from tests.test_rea_operator_targets import OPERATOR, SNAPSHOT_ID, claim, review_dict

URL = f"/integration-candidates/rea/operator-targets/{SNAPSHOT_ID}/analysis-plan"


def test_prepared_plan_is_exact_and_non_authoritative(client, database):
    selected, created = ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)
    assert created and selected.currently_selected
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["snapshot_id"] == SNAPSHOT_ID
    assert record["receipt_sha256"] == selected.receipt_sha256
    assert record["content_sha256"] == selected.content_sha256
    assert record["file_count"] == selected.file_count
    assert record["total_bytes"] == selected.total_bytes
    assert record["source_manifest_sha256"] == (
        "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
    )
    assert record["review_fingerprint"] == selected.review_fingerprint
    assert record["source_commit"] == "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
    assert record["catalog_sha256"] == plans.EXPECTED_CATALOG_SHA256
    assert record["tool_name"] == "analyze_javascript_application"
    assert record["tool_qualification"] == "fixed_fixture_only_not_target_qualified"
    assert record["target_validation"] == "operator_asserted_unverified"
    assert record["agent_id"] == "reverse_engineering"
    assert record["agent_lifecycle"] == "draft"
    assert len(record["candidate_spec_sha256"]) == 64
    assert len(record["active_agent_registry_version"]) == 64
    assert record["worker_authority_policy_version"] == authority_policy_version()
    for key in (
        "approval_authority",
        "task_created",
        "agent_activated",
        "worker_registered",
        "may_execute",
    ):
        assert record[key] is False
    assert record["tool_grants"] == []
    assert record["refusal_reasons"] == [
        "target_not_verified_by_core",
        "rea_agent_not_active",
        "rea_worker_not_qualified",
        "rea_tool_ungranted",
        "execution_not_approved",
    ]
    assert record == client.get(URL, headers=AUTH).json()
    assert (
        ReaAnalysisPlanPreview.model_validate_json(json.dumps(record), strict=True).plan_fingerprint
        == record["plan_fingerprint"]
    )
    with database.read() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0


def test_claim_can_be_synthetic_and_still_never_execute(client, database):
    # No local target folder exists: operator assertion cannot substitute for
    # the future trusted analyzer's actual byte and lease validation.
    ReaOperatorTargetStore(database).select(claim(content="d" * 64), principal=OPERATOR)
    result = client.get(URL, headers=AUTH)
    assert result.status_code == 200
    assert result.json()["content_sha256"] == "d" * 64
    assert result.json()["target_validation"] == "operator_asserted_unverified"
    assert result.json()["may_execute"] is False


def test_missing_and_revoked_selection_fail_closed(client, database):
    assert client.get(URL, headers=AUTH).status_code == 404
    ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)
    ReaOperatorTargetStore(database).revoke(SNAPSHOT_ID, principal=OPERATOR)
    denied = client.get(URL, headers=AUTH)
    assert denied.status_code == 409
    assert denied.json() == {"detail": "rea_operator_target_revoked"}


def test_auth_and_http_method_restrictions(client, database):
    ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)
    denied = client.get(URL, headers={"Authorization": "Bearer invalid"})
    assert denied.status_code == 401
    assert client.post(URL, headers=AUTH, json=review_dict()).status_code == 405
    assert client.get(URL.replace(SNAPSHOT_ID, "../etc/passwd"), headers=AUTH).status_code != 200


def test_tampering_plan_fingerprint_or_grants_is_rejected(client, database):
    ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 200
    record = response.json()
    record["content_sha256"] = "a" * 64
    with pytest.raises(ValidationError, match="rea_analysis_plan_fingerprint_invalid"):
        ReaAnalysisPlanPreview.model_validate_json(json.dumps(record), strict=True)
    record = response.json()
    record["may_execute"] = True
    with pytest.raises(ValidationError):
        ReaAnalysisPlanPreview.model_validate_json(json.dumps(record), strict=True)
    record = response.json()
    record["tool_grants"] = ["analyze_javascript_application"]
    with pytest.raises(ValidationError):
        ReaAnalysisPlanPreview.model_validate_json(json.dumps(record), strict=True)
    record = response.json()
    record["refusal_reasons"] = []
    with pytest.raises(ValidationError, match="rea_analysis_plan_refusals_missing"):
        ReaAnalysisPlanPreview.model_validate_json(json.dumps(record), strict=True)


def test_catalog_permission_drift_is_not_accepted(client, database, monkeypatch):
    from app.rea_tool_inventory import ReaCatalogRefusal

    ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)

    def broken_catalog():
        raise ReaCatalogRefusal("local_secret_details_should_not_leak")

    monkeypatch.setattr(plans, "rea_tool_inventory", broken_catalog)
    denied = client.get(URL, headers=AUTH)
    assert denied.status_code == 503
    assert denied.json() == {"detail": "rea_analysis_plan_unavailable"}


def test_unexpected_live_agent_activation_refuses_preview(client, database, monkeypatch):
    ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)
    live = plans.default_agent_registry()

    class DriftedRegistry:
        version = live.version

        @staticmethod
        def get_agent(agent_id):
            return object() if agent_id == "reverse_engineering" else None

    monkeypatch.setattr(plans, "default_agent_registry", DriftedRegistry)
    denied = client.get(URL, headers=AUTH)
    assert denied.status_code == 503
    assert denied.json() == {"detail": "rea_analysis_plan_unavailable"}


def test_catalog_grant_or_qualification_drift_fails_closed(client, database, monkeypatch):
    ReaOperatorTargetStore(database).select(claim(), principal=OPERATOR)
    original = plans.rea_tool_inventory()

    for changes in (
        {"grants": ("analyze_javascript_application",)},
        {"execution_enabled": True},
        {"worker_registered": True},
    ):
        monkeypatch.setattr(
            plans, "rea_tool_inventory", lambda updates=changes: original.model_copy(update=updates)
        )
        response = client.get(URL, headers=AUTH)
        assert response.status_code == 503
        assert response.json() == {"detail": "rea_analysis_plan_unavailable"}


def test_claimed_target_becoming_authoritative_without_qualification_is_refused(
    client, database, monkeypatch
):
    from app.rea_operator_targets import ReaOperatorTargetStore as Store

    Store(database).select(claim(), principal=OPERATOR)
    original = Store.get

    def invalid_record(self, snapshot_id, *, principal):
        selected = original(self, snapshot_id, principal=principal)
        return selected.model_copy(update={"target_verified_by_core": True})

    monkeypatch.setattr(Store, "get", invalid_record)
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "rea_analysis_plan_unavailable"}
