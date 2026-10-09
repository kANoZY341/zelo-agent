"""OpenDots Computer Panel data is projected only from Core-owned grants."""

import asyncio
from datetime import UTC, datetime

from app.agent_factory import AgentComputerSpec
from app.community_computer_panel import project_computer_panel
from app.schemas import (
    AgentComputerControlResponse,
    AgentComputerEventPage,
    AgentComputerPermissionsResponse,
    AgentComputerStatusResponse,
)
from tests.conftest import AUTH

INSTANCE = "11111111-1111-4111-8111-111111111111"


class FakeComputerService:
    def __init__(self, *, active=True, permissions=True):
        self.calls = []
        self.active = active
        self.permissions = permissions

    async def status(self, instance_id):
        self.calls.append("status")
        return AgentComputerStatusResponse(
            agent_instance_id=instance_id,
            configured=self.active,
            state="running" if self.active else "not_configured",
            spec=AgentComputerSpec(browser=True, files=True, shell=False, human_takeover=True),
            permissions=AgentComputerPermissionsResponse(
                enabled=self.permissions,
                browser=self.permissions,
                files=self.permissions,
                shell=False,
                updated_at=datetime(2026, 10, 9, tzinfo=UTC),
            ),
            control=AgentComputerControlResponse(
                holder="human",
                requested=False,
                transitioning=False,
                resume_snapshot_required=False,
            )
            if self.active
            else None,
        )

    def events(self, instance_id, *, limit, offset):
        self.calls.append("events")
        assert limit == 10 and offset == 0
        return AgentComputerEventPage(items=[], limit=limit, offset=offset, total=0)


def test_panel_projection_reuses_existing_operator_core_status_only():
    backend = FakeComputerService()
    panel = asyncio.run(project_computer_panel(backend, INSTANCE))
    assert backend.calls == ["status", "events"]
    assert panel.browser_tab_visible and panel.files_tab_visible
    assert panel.browser_tab_ready and panel.files_tab_ready
    assert not panel.terminal_tab_visible and not panel.terminal_tab_ready
    assert panel.human_control_holder == "human"
    assert panel.activity_tab_visible and panel.view_only
    assert not panel.new_control_granted
    assert not panel.new_browser_mutation_granted
    assert not panel.new_file_or_shell_mutation_granted
    assert not panel.upstream_api_or_runtime_loaded


def test_panel_does_not_manufacture_rights_when_computer_offline_or_denied():
    for backend in (FakeComputerService(active=False), FakeComputerService(permissions=False)):
        panel = asyncio.run(project_computer_panel(backend, INSTANCE))
        assert not panel.browser_tab_ready
        assert not panel.files_tab_ready
        assert not panel.terminal_tab_ready


def test_operator_authenticated_read_only_panel_api(client):
    original = client.app.state.agent_computer_service
    fake = FakeComputerService()
    client.app.state.agent_computer_service = fake
    try:
        route = f"/agents/{INSTANCE}/computer/panel"
        denied = client.get(route, headers={"Authorization": "Bearer bad"})
        assert denied.status_code == 401
        accepted = client.get(route, headers=AUTH)
        assert accepted.status_code == 200, accepted.text
        payload = accepted.json()
        assert payload["view_only"] is True
        assert payload["new_browser_mutation_granted"] is False
        assert payload["agent_instance_id"] == INSTANCE
        assert client.post(route, headers=AUTH).status_code == 405
    finally:
        client.app.state.agent_computer_service = original
