"""Operator Skill publication dispatch stays exact, one-use, and default closed."""

from copy import deepcopy

import pytest

from app import skill_publication_api
from tests import test_skill_learning_store as learning_tests
from tests.test_skill_publication_dispatch_fence import FENCE_CONFIRM, _setup
from tests.test_skill_publication_one_attempt import (
    CONFIRM,
    _add_success_response,
    _rows,
    _setup_one,
)


@pytest.fixture
def proposal_case(database, settings):
    return learning_tests.proposal_case.__wrapped__(database, settings)


def test_dispatch_fence_api_reserves_only_after_exact_owner_decision(
    proposal_case, database, client, tmp_path, monkeypatch
):
    store, claim, _decision, request, transport, service, _ = _setup(
        database, proposal_case, tmp_path
    )
    client.app.state.skill_learning_store = store
    monkeypatch.setattr(skill_publication_api, "_remote", lambda _: service.blob.remote)
    route = f"/skill-publication/claims/{claim.id}/dispatch-fence"
    body = {"confirmation": FENCE_CONFIRM, "merge_request": request.model_dump(mode="json")}

    assert client.post(route, json=body, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(route, json={**body, "confirmation": "yes"}).status_code == 422
    assert transport.calls == []
    original = deepcopy(transport.responses)

    created = client.post(route, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["phase"] == "reserved_no_send"
    assert created.json()["network_dispatch_started"] is False
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert client.get(route).json() == created.json()

    transport.responses = deepcopy(original)
    transport.calls = []
    replay = client.post(route, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == created.json()
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_merge_once_api_refuses_default_configuration_before_network(
    proposal_case, database, client, tmp_path, monkeypatch
):
    store, claim, _decision, request, _transport, _service, _ = _setup(
        database, proposal_case, tmp_path
    )
    client.app.state.skill_learning_store = store

    def forbidden_remote(_request):
        raise AssertionError("default disabled route must not construct provider")

    monkeypatch.setattr(skill_publication_api, "_remote", forbidden_remote)
    route = f"/skill-publication/claims/{claim.id}/merge-once"
    body = {"confirmation": CONFIRM, "merge_request": request.model_dump(mode="json")}

    assert client.post(route, json=body, headers={"Authorization": "Bearer bad"}).status_code == 401
    response = client.post(route, json=body)
    assert response.status_code == 409
    assert response.json() == {"detail": "skill_publication_single_attempt_disabled"}


def test_merge_once_api_sends_one_scripted_put_and_replay_refuses(
    proposal_case, database, settings, client, tmp_path, monkeypatch
):
    store, claim, request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(request, transport, template)
    client.app.state.skill_learning_store = store
    client.app.state.settings = service.settings.model_copy(
        update={"operator_token": settings.operator_token}
    )
    monkeypatch.setattr(skill_publication_api, "_remote", lambda _: service.fences.blob.remote)
    route = f"/skill-publication/claims/{claim.id}/merge-once"
    body = {"confirmation": CONFIRM, "merge_request": request.model_dump(mode="json")}

    response = client.post(route, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["merge_commit_sha"] == "f" * 40
    assert _rows(database) == (1, 1)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1

    retry = client.post(route, json=body)
    assert retry.status_code == 409
    assert retry.json()["detail"] == "skill_publication_single_attempt_already_recorded"
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1

    # Historical evidence remains readable after dispatch is disabled again.
    client.app.state.settings = settings
    base = f"/skill-publication/claims/{claim.id}"
    assert client.get(base + "/dispatch-fence").status_code == 200
    attempt = client.get(base + "/merge-attempt")
    assert attempt.status_code == 200
    assert attempt.json()["automatic_retry_allowed"] is False
    assert client.get(base + "/merge-receipt").json() == response.json()
    assert client.get(base + "/merge-failure").status_code == 404
    assert (
        client.get(base + "/merge-attempt", headers={"Authorization": "Bearer bad"}).status_code
        == 401
    )
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1


@pytest.mark.parametrize("failure_phase", ["put_request", "post_pr"])
def test_merge_once_api_reports_terminal_uncertainty_and_readable_history(
    failure_phase, proposal_case, database, settings, client, tmp_path, monkeypatch
):
    store, claim, merge_request, transport, _fence, service, template = _setup_one(
        database, proposal_case, tmp_path
    )
    _add_success_response(merge_request, transport, template)
    original_request = transport.request

    async def fail_after_dispatch(method, endpoint, body=None):
        if failure_phase == "put_request" and method == "PUT":
            transport.calls.append((method, endpoint, body))
            raise TimeoutError()
        if (
            failure_phase == "post_pr"
            and method == "GET"
            and any(call[0] == "PUT" for call in transport.calls)
        ):
            transport.calls.append((method, endpoint, body))
            raise RuntimeError("simulated provider outage")
        return await original_request(method, endpoint, body)

    transport.request = fail_after_dispatch
    client.app.state.skill_learning_store = store
    client.app.state.settings = service.settings.model_copy(
        update={"operator_token": settings.operator_token}
    )
    monkeypatch.setattr(skill_publication_api, "_remote", lambda _: service.fences.blob.remote)
    base = f"/skill-publication/claims/{claim.id}"
    body = {
        "confirmation": CONFIRM,
        "merge_request": merge_request.model_dump(mode="json"),
    }

    response = client.post(base + "/merge-once", json=body)
    assert response.status_code == 409
    assert response.json() == {"detail": "skill_publication_single_attempt_outcome_unresolved"}
    assert _rows(database) == (1, 0)
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1

    client.app.state.settings = settings
    attempt = client.get(base + "/merge-attempt")
    assert attempt.status_code == 200
    assert attempt.json()["automatic_retry_allowed"] is False
    failure = client.get(base + "/merge-failure")
    assert failure.status_code == 200
    assert failure.json()["phase"] == failure_phase
    assert failure.json()["reason"] == "provider_exception"
    assert client.get(base + "/merge-receipt").status_code == 404
    assert sum(method == "PUT" for method, _, _ in transport.calls) == 1
