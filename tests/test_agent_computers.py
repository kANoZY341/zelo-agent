"""Persistent per-agent computer foundation tests."""

import asyncio
import hashlib
import hmac

import httpx
import pytest
from pydantic import ValidationError

from app.agent_computers import (
    ComputerBackendError,
    ComputerState,
    OpenBotComputerBackend,
)
from app.config import Settings


def settings(**overrides) -> Settings:
    values = {
        "computer_supervisor_url": "http://127.0.0.1:4312",
        "computer_supervisor_token": "s" * 24,
        "computer_token": "c" * 24,
        "computer_namespace": "zelo",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_computer_configuration_is_all_or_nothing_and_loopback_only():
    with pytest.raises(ValidationError, match="configuration must be complete"):
        Settings(
            _env_file=None,
            computer_supervisor_url="http://127.0.0.1:4312",
        )

    with pytest.raises(ValidationError, match="loopback HTTP"):
        settings(computer_supervisor_url="https://example.com")


def test_agent_token_matches_hardened_supervisor_contract():
    backend = OpenBotComputerBackend(settings())
    agent_id = "00000000-0000-4000-8000-000000000001"
    expected = hmac.new(
        ("c" * 24).encode("ascii"),
        f"zelo-computer:{agent_id}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    assert backend.derive_agent_token(agent_id) == expected

    with pytest.raises(ComputerBackendError, match="computer_agent_id_invalid"):
        backend.derive_agent_token("../escape")


def test_endpoint_must_match_agent_container_and_loopback_port():
    backend = OpenBotComputerBackend(settings())
    agent_id = "agent-1"
    state = ComputerState.model_validate(
        {
            "botId": agent_id,
            "container": "zelo-computer-agent-1",
            "status": "running",
            "port": 49152,
        }
    )
    assert backend.endpoint(agent_id, state) == "http://127.0.0.1:49152"

    with pytest.raises(ComputerBackendError, match="computer_identity_mismatch"):
        backend.endpoint(
            agent_id,
            state.model_copy(update={"container": "other-computer-agent-1"}),
        )

    with pytest.raises(ComputerBackendError, match="computer_endpoint_invalid"):
        backend.endpoint(
            agent_id,
            state.model_copy(update={"url": "http://example.com:49152"}),
        )


def test_supervisor_lifecycle_uses_only_supervisor_token_and_validates_identity():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/ensure"):
            return httpx.Response(
                200,
                json={
                    "botId": "agent-1",
                    "container": "zelo-computer-agent-1",
                    "status": "running",
                    "port": 49152,
                },
            )
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"computers": []})

    backend = OpenBotComputerBackend(
        settings(),
        transport=httpx.MockTransport(handler),
    )

    state = asyncio.run(backend.ensure("agent-1"))
    asyncio.run(backend.stop("agent-1"))

    assert state.status == "running"
    assert [request.method for request in calls] == ["POST", "POST"]
    assert all(request.headers["authorization"] == f"Bearer {'s' * 24}" for request in calls)
    serialized = "\n".join(str(dict(request.headers)) for request in calls)
    assert "c" * 24 not in serialized


def test_application_exposes_inert_agent_computer_backend(client):
    backend = client.app.state.agent_computers
    assert isinstance(backend, OpenBotComputerBackend)
    assert backend.configured is False


def test_supervisor_response_redacts_infrastructure_secrets():
    master = "c" * 24
    supervisor = "s" * 24

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "computers": [],
                "reflected": f"{master}:{supervisor}",
            },
        )

    backend = OpenBotComputerBackend(
        settings(),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(backend._request("/computers"))
    assert result["reflected"] == "[redacted]:[redacted]"


def test_list_rejects_cross_namespace_computer_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "computers": [
                    {
                        "botId": "agent-1",
                        "container": "other-computer-agent-1",
                        "status": "stopped",
                    }
                ]
            },
        )

    backend = OpenBotComputerBackend(
        settings(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ComputerBackendError, match="computer_identity_mismatch"):
        asyncio.run(backend.list())


def test_control_take_release_uses_derived_agent_token_and_preserves_handback_state():
    agent_id = "agent-1"
    mode = {"holder": "bot", "request": None}
    calls: list[httpx.Request] = []

    def control_payload():
        request = mode["request"]
        return {
            "holder": mode["holder"],
            "requested": bool(request and request["status"] == "waiting"),
            "transitioning": False,
            "resumeSnapshotRequired": (
                mode["holder"] == "bot" and bool(request and request["status"] == "completed")
            ),
            **({"request": request} if request else {}),
        }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/computers":
            return httpx.Response(
                200,
                json={
                    "computers": [
                        {
                            "botId": agent_id,
                            "container": "zelo-computer-agent-1",
                            "status": "running",
                            "port": 49152,
                        }
                    ]
                },
            )
        if path == "/control" and request.method == "GET":
            return httpx.Response(200, json=control_payload())
        if path == "/control/request":
            mode["request"] = {"id": "handoff", "status": "waiting"}
            return httpx.Response(200, json=control_payload())
        if path == "/control/take":
            mode["holder"] = "human"
            mode["request"] = {"id": "handoff", "status": "taken"}
            return httpx.Response(200, json=control_payload())
        if path == "/control/release":
            mode["holder"] = "bot"
            mode["request"] = {"id": "handoff", "status": "completed"}
            return httpx.Response(200, json=control_payload())
        raise AssertionError(f"unexpected request: {request.method} {path}")

    backend = OpenBotComputerBackend(
        settings(),
        transport=httpx.MockTransport(handler),
    )

    taken = asyncio.run(backend.take(agent_id))
    assert taken.holder == "human"
    assert taken.request is not None and taken.request.status == "taken"

    released = asyncio.run(backend.release(agent_id))
    assert released.holder == "bot"
    assert released.resume_snapshot_required is True

    derived = backend.derive_agent_token(agent_id)
    supervisor_auth = f"Bearer {'s' * 24}"
    computer_auth = f"Bearer {derived}"
    for request in calls:
        if request.url.path.startswith("/computers"):
            assert request.headers["authorization"] == supervisor_auth
        else:
            assert request.headers["authorization"] == computer_auth
            assert request.headers["x-openbot-bot-id"] == agent_id
            assert ("c" * 24) not in str(dict(request.headers))


def test_default_agents_do_not_gain_computer_routes(client):
    project = client.post(
        "/projects",
        json={"name": "No computer", "summary": "Default agent stays unchanged."},
    ).json()
    instance = client.post(
        "/agents",
        json={"agent_id": "coding", "project_id": project["id"]},
    ).json()

    response = client.get(f"/agents/{instance['id']}/computer")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent instance has no computer profile"
