"""Operator-only route for a positive Skill merge receipt link; never publication authority."""

from copy import deepcopy

import pytest

from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_one_attempt import _setup_one
from tests.test_skill_publication_promotion_receipt_link import _case


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def test_positive_receipt_link_route_is_confirmed_idempotent_and_read_only(
    database, proposal_case, client, tmp_path, monkeypatch
):
    from app import skill_publication_api

    store, claim, request, transport, attempt_service, receipt, responses = _case(
        database, proposal_case, tmp_path
    )
    client.app.state.skill_learning_store = store
    path = f"/skill-publication/claims/{claim.id}/promotion-receipt-link"
    payload = {
        "confirmation": "LINK_EXACT_POSITIVE_SKILL_MERGE_RECEIPT_NO_ACTIVATION",
        "merge_request": request.model_dump(mode="json"),
    }
    unconfigured = client.post(path, json=payload)
    assert unconfigured.status_code == 409
    assert unconfigured.json()["detail"] == "skill_publication_remote_repository_unconfigured"
    assert transport.calls == []
    monkeypatch.setattr(
        skill_publication_api, "_remote", lambda _: attempt_service.fences.blob.remote
    )

    assert (
        client.post(path, json=payload, headers={"Authorization": "Bearer bad"}).status_code == 401
    )
    assert client.post(path, json={**payload, "confirmation": "yes"}).status_code == 422
    assert client.post(path, json={**payload, "activate": True}).status_code == 422
    assert transport.calls == []
    assert client.get(path).status_code == 404

    response = client.post(path, json=payload)
    assert response.status_code == 201, response.text
    link = response.json()
    assert link["claim_id"] == claim.id
    assert link["receipt_id"] == receipt.id
    assert link["runtime_authority"] is False
    assert store.get_promotion_link(claim.proposal_id) is None
    assert store.resolve_promoted_revision(claim.proposal_id) is None
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert client.get(path).json() == link
    assert client.get(path, headers={"Authorization": "Bearer bad"}).status_code == 401

    transport.calls = []
    transport.responses = deepcopy(responses)
    replay = client.post(path, json=payload)
    assert replay.status_code == 200
    assert replay.json() == link
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_receipt_link_route_refuses_uncertain_attempt_before_provider_read(
    database, proposal_case, client, tmp_path, monkeypatch
):
    from app import skill_publication_api

    store, claim, request, transport, _fence, attempt_service, _template = _setup_one(
        database, proposal_case, tmp_path
    )
    client.app.state.skill_learning_store = store
    monkeypatch.setattr(
        skill_publication_api, "_remote", lambda _: attempt_service.fences.blob.remote
    )
    path = f"/skill-publication/claims/{claim.id}/promotion-receipt-link"
    response = client.post(
        path,
        json={
            "confirmation": "LINK_EXACT_POSITIVE_SKILL_MERGE_RECEIPT_NO_ACTIVATION",
            "merge_request": request.model_dump(mode="json"),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "skill_publication_promotion_link_positive_receipt_missing"
    assert transport.calls == []
    assert client.get(path).status_code == 404


def test_receipt_link_route_unconfigured_provider_refuses_without_network(client):
    claim_id = "00000000-0000-4000-8000-000000000001"
    response = client.post(
        f"/skill-publication/claims/{claim_id}/promotion-receipt-link",
        json={
            "confirmation": "LINK_EXACT_POSITIVE_SKILL_MERGE_RECEIPT_NO_ACTIVATION",
            "merge_request": {},
        },
    )
    assert response.status_code == 422
    assert (
        client.get(f"/skill-publication/claims/{claim_id}/promotion-receipt-link").status_code
        == 404
    )
