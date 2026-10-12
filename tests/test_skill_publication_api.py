"""Operator-authenticated Skill publication workflow, without any merge grant."""

import pytest

from app.auth import OperatorPrincipal
from app.skill_publication_intent import prepare_skill_publication_intent
from tests import test_skill_learning_store as learning_tests
from tests.test_skill_publication_intent import accepted_case
from tests.test_skill_publication_merge_preview import _qualified
from tests.test_skill_publication_remote_evidence import _remote_fixture


@pytest.fixture
def proposal_case(database, settings):
    return learning_tests.proposal_case.__wrapped__(database, settings)


def test_claim_api_requires_real_operator_and_persists_one_inert_reservation(
    database, client, proposal_case
):
    store, reviewed, _agent, _rev, _ref = accepted_case(database, proposal_case)
    client.app.state.skill_learning_store = store
    identifier = reviewed.proposal.proposal_id
    intent = prepare_skill_publication_intent(store, identifier, OperatorPrincipal())
    path = f"/skill-publication/proposals/{identifier}/claim"
    body = {"expected_intent_fingerprint": intent.intent_fingerprint}

    assert client.post(path, json=body, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={**body, "publish_now": True}).status_code == 422
    mismatch = client.post(
        path,
        json={"expected_intent_fingerprint": "0" * 64},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == "skill_publication_claim_intent_stale_or_mismatched"

    result = client.post(path, json=body)
    assert result.status_code == 201, result.text
    saved = result.json()
    assert saved["proposal_id"] == identifier
    assert saved["publication_authority"] is False
    assert saved["runtime_authority"] is False
    replay = client.post(path, json=body)
    assert replay.status_code == 201
    assert replay.json() == saved
    lookup = client.get(f"/skill-publication/claims/{saved['id']}")
    assert lookup.status_code == 200 and lookup.json() == saved
    assert (
        client.get(
            f"/skill-publication/claims/{saved['id']}",
            headers={"Authorization": "Bearer bad"},
        ).status_code
        == 401
    )


def test_claim_preview_route_keeps_artifact_evidence_read_only(
    database, client, proposal_case, tmp_path
):
    store, _agent, _intent, claim, _service, request = _qualified(database, proposal_case, tmp_path)
    client.app.state.skill_learning_store = store
    route = f"/skill-publication/claims/{claim.id}/preview"
    payload = request.model_dump(mode="json")

    assert (
        client.post(route, json=payload, headers={"Authorization": "Bearer bad"}).status_code == 401
    )
    response = client.post(route, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["claim_id"] == claim.id
    assert result["no_external_merge_or_execution_authority"] is True
    assert result["runtime_skill_activation_authority"] is False
    assert store.get_promotion_link(claim.proposal_id) is None
    assert client.get(f"/skill-publication/claims/{claim.id}/decision").status_code == 404


def test_unconfigured_live_github_routes_refuse_without_network(
    database, client, proposal_case, tmp_path
):
    store, _agent, _intent, claim, _service, request = _qualified(database, proposal_case, tmp_path)
    client.app.state.skill_learning_store = store
    payload = request.model_dump(mode="json")
    for suffix in ("remote-evidence", "blob-evidence"):
        r = client.post(f"/skill-publication/claims/{claim.id}/{suffix}", json=payload)
        assert r.status_code == 409
        assert r.json()["detail"] == "skill_publication_remote_repository_unconfigured"

    decision = client.post(
        f"/skill-publication/claims/{claim.id}/decision",
        json={
            "confirmation": "APPROVE_EXACT_SKILL_PUBLICATION_REVIEW",
            "merge_request": payload,
        },
    )
    assert decision.status_code == 409
    assert decision.json()["detail"] == "skill_publication_remote_repository_unconfigured"


def test_direct_owner_decision_endpoint_with_scripted_provider_but_no_put(
    database, client, proposal_case, tmp_path, monkeypatch
):
    from app import skill_publication_api

    store, _agent, claim, request, transport, remote = _remote_fixture(
        database, proposal_case, tmp_path
    )
    client.app.state.skill_learning_store = store
    monkeypatch.setattr(skill_publication_api, "_remote", lambda _: remote)
    payload = request.model_dump(mode="json")
    route = f"/skill-publication/claims/{claim.id}/decision"

    assert client.post(route, json={}).status_code == 422
    assert (
        client.post(
            route,
            json={"confirmation": "yes", "merge_request": payload},
        ).status_code
        == 422
    )
    assert (
        client.post(
            route,
            json={
                "confirmation": "APPROVE_EXACT_SKILL_PUBLICATION_REVIEW",
                "merge_request": payload,
            },
            headers={"Authorization": "Bearer bad"},
        ).status_code
        == 401
    )
    assert transport.calls == []

    response = client.post(
        route,
        json={
            "confirmation": "APPROVE_EXACT_SKILL_PUBLICATION_REVIEW",
            "merge_request": payload,
        },
    )
    assert response.status_code == 201, response.text
    evidence = response.json()
    assert evidence["claim_id"] == claim.id
    assert evidence["publication_authority"] is False
    assert evidence["external_merge_authority"] is False
    assert evidence["runtime_authority"] is False
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert len(transport.calls) == 3
    assert store.get_promotion_link(claim.proposal_id) is None

    retrieved = client.get(route)
    assert retrieved.status_code == 200 and retrieved.json() == evidence
    assert client.get(route, headers={"Authorization": "Bearer bad"}).status_code == 401


def test_public_bad_json_and_unknown_claim_fail_closed_without_data_disclosure(client):

    claim_id = "00000000-0000-4000-8000-000000000001"
    assert client.get(f"/skill-publication/claims/{claim_id}").status_code == 404
    assert client.get(f"/skill-publication/claims/{claim_id}/decision").status_code == 404
    assert (
        client.post(
            f"/skill-publication/claims/{claim_id}/preview",
            json={"fake": "grant-me-merging"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/skill-publication/proposals/{claim_id}/claim",
            json={"expected_intent_fingerprint": "nope"},
        ).status_code
        == 422
    )
