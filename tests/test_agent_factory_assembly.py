"""Trusted application assembly for specialist-agent identities."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import AUTH


def test_coding_agent_is_registered_but_disabled_by_default(client):
    registry = client.app.state.agent_registry
    factory = client.app.state.agent_factory

    assert [item.agent_id for item in registry.list()] == ["coding"]
    coding = registry.get("coding")
    assert coding is not None
    assert coding.enabled is False
    assert coding.memory_namespace == "agent/coding"
    assert coding.delegation_role == "developer"
    assert coding.delegation_capabilities == ("zelo_core_development",)

    with pytest.raises(ValueError, match="agent_disabled"):
        factory.bind("coding")


def test_coding_agent_can_be_enabled_only_by_trusted_configuration(settings, database):
    configured = settings.model_copy(update={"agent_coding_enabled": True})

    with TestClient(create_app(configured), headers=AUTH) as client:
        binding = client.app.state.agent_factory.bind("coding")
        tool_names = {
            item["name"] for item in client.app.state.tool_gateway.registry.metadata()
        }

        assert binding.descriptor.enabled is True
        assert binding.descriptor.role == "coding"
        assert binding.descriptor.allowed_task_kinds == ("agent_run", "developer_run")
        assert set(binding.descriptor.allowed_tools).issubset(tool_names)
        assert "system_info" not in binding.descriptor.allowed_tools
