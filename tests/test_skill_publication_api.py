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
    proposal_case, database, client
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
    proposal_case, database, client, tmp_path
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
    proposal_case, database, client, tmp_path
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
    proposal_case, database, client, tmp_path, monkeypatch
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


def test_post_attempt_reconciliation_persists_only_after_an_attempt_and_appends(
    proposal_case, database, client, tmp_path, monkeypatch
):
    from app import skill_publication_api
    from app.models import SkillPublicationMergeAttemptRecord, new_id
    from app.skill_publication_intent import _digest
    from tests.test_skill_publication_one_attempt import _approved_source
    from tests.test_skill_publication_reconciliation import (
        _prepare_merged_pr,
        _prepared,
        _response,
    )

    store, claim, _decision, request, transport, fence, service = _prepared(
        database, proposal_case, tmp_path
    )
    client.app.state.skill_learning_store = store
    monkeypatch.setattr(skill_publication_api, "_remote", lambda _: service.fences.blob.remote)
    path = f"/skill-publication/claims/{claim.id}/post-attempt-reconciliation"
    list_path = f"/skill-publication/claims/{claim.id}/post-attempt-reconciliations"
    merge_payload = request.model_dump(mode="json")
    payload = {
        "confirmation": "RECONCILE_EXACT_SKILL_PUBLICATION_ATTEMPT_GET_ONLY",
        "merge_request": merge_payload,
    }

    invalid_confirmation = client.post(
        path, json={"confirmation": "yes", "merge_request": merge_payload}
    )
    assert invalid_confirmation.status_code == 422
    assert transport.calls == []

    missing = client.post(path, json=payload)
    assert missing.status_code == 409
    assert missing.json()["detail"] == (
        "skill_publication_post_attempt_reconciliation_attempt_missing"
    )
    assert transport.calls == []

    _approved_source(database, request)
    attempt_id = new_id()
    attempt_material = {
        "schema": "zelo-skill-publication-single-attempt-v1",
        "fence_id": fence.id,
        "claim_id": claim.id,
        "promotion_task_id": request.promotion_task_id,
        "repository": fence.repository,
        "pr_number": fence.pr_number,
        "pr_head_sha": fence.pr_head_sha,
        "pr_base_sha": fence.pr_base_sha,
        "fence_fingerprint": fence.fence_fingerprint,
        "merge_request_sha256": fence.merge_request_sha256,
        "operator_subject": "ahmad",
    }
    with database.transaction() as session:
        session.add(
            SkillPublicationMergeAttemptRecord(
                id=attempt_id,
                fence_id=fence.id,
                claim_id=claim.id,
                promotion_task_id=request.promotion_task_id,
                repository=fence.repository,
                pr_number=fence.pr_number,
                pr_head_sha=fence.pr_head_sha,
                pr_base_sha=fence.pr_base_sha,
                fence_fingerprint=fence.fence_fingerprint,
                merge_request_sha256=fence.merge_request_sha256,
                attempt_fingerprint=_digest(attempt_material),
                operator_subject="ahmad",
                phase="send_outcome_unresolved",
                send_may_have_occurred=True,
                automatic_retry_allowed=False,
                runtime_authority=False,
            )
        )

    endpoint = f"/repos/{fence.repository}/pulls/{fence.pr_number}"
    transport.responses = [("GET", endpoint, _response(request))]
    first = client.post(path, json=payload)
    assert first.status_code == 201, first.text
    first_observation = first.json()
    assert first_observation["attempt_id"] == attempt_id
    assert first_observation["status"] == "open_unmerged"
    assert first_observation["automatic_retry_allowed"] is False
    assert first_observation["runtime_authority"] is False
    assert first_observation["remote_actor_identity_unproven"] is True
    assert first_observation["remote_merge_method_unproven"] is True
    assert all(method == "GET" for method, _, _ in transport.calls)

    remote = service.fences.blob.remote

    def no_remote_required_for_read(_request):
        raise AssertionError("historical observation reads must use only the database")

    monkeypatch.setattr(skill_publication_api, "_remote", no_remote_required_for_read)
    saved_once = client.get(list_path)
    assert saved_once.status_code == 200, saved_once.text
    assert saved_once.json() == [first_observation]
    monkeypatch.setattr(skill_publication_api, "_remote", lambda _: remote)

    transport.responses = [("GET", endpoint, _response(request))]
    second = client.post(path, json=payload)
    assert second.status_code == 201, second.text
    saved_twice = client.get(list_path)
    assert saved_twice.status_code == 200
    assert [item["attempt_id"] for item in saved_twice.json()] == [attempt_id, attempt_id]
    assert saved_twice.json()[0]["id"] != saved_twice.json()[1]["id"]

    merge_sha = _prepare_merged_pr(request, fence, transport)
    merged = client.post(path, json=payload)
    assert merged.status_code == 201, merged.text
    merged_observation = merged.json()
    assert merged_observation["status"] == "merged_single_parent_content_observed"
    assert merged_observation["merge_commit_sha"] == merge_sha
    saved_three = client.get(list_path)
    assert saved_three.status_code == 200
    assert len(saved_three.json()) == 3
    assert all(method == "GET" for method, _, _ in transport.calls)

    calls_before_denials = len(transport.calls)
    changed = request.model_copy(
        update={"merge_commit_message": "A different, unreviewed merge request"}
    )
    changed_response = client.post(
        path,
        json={
            "confirmation": "RECONCILE_EXACT_SKILL_PUBLICATION_ATTEMPT_GET_ONLY",
            "merge_request": changed.model_dump(mode="json"),
        },
    )
    assert changed_response.status_code == 409
    assert changed_response.json()["detail"] == (
        "skill_publication_post_attempt_reconciliation_request_changed"
    )
    unauthorized = client.post(path, json=payload, headers={"Authorization": "Bearer invalid"})
    assert unauthorized.status_code == 401
    assert len(transport.calls) == calls_before_denials

    invalid_pr = _response(request)
    invalid_pr["head"]["sha"] = "e" * 40
    transport.responses = [("GET", endpoint, invalid_pr)]
    failed_observation = client.post(path, json=payload)
    assert failed_observation.status_code == 409
    assert failed_observation.json()["detail"] == ("skill_publication_remote_evidence_unavailable")
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert len(client.get(list_path).json()) == 3

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    for statement in (
        "UPDATE skill_publication_post_attempt_reconciliations "
        "SET status='closed_unmerged' WHERE id=:id",
        "DELETE FROM skill_publication_post_attempt_reconciliations WHERE id=:id",
    ):
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(text(statement), {"id": first_observation["id"]})

    assert store.get_promotion_link(claim.proposal_id) is None
