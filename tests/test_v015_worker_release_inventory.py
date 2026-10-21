"""Release inventory is read-only metadata, never operational certification."""

import json
from dataclasses import asdict

import pytest

from app.policy import DomainError
from app.schemas import ApiRunInput, BrowserRunInput, ComputerRunInput, ShellRunInput
from app.v015_worker_release_inventory import worker_release_inventory

EXPECTED_WORKERS = (
    "codex_core",
    "codex_reviewer",
    "claude_core",
    "claude_reviewer",
    "opencode_core",
    "opencode_reviewer",
    "research_core",
    "python_core",
    "api_core",
    "browser_core",
    "computer_core",
    "computer_navigate_core",
    "shell_core",
    "media_native_reader",
)


def _by_id(report):
    return {item.worker_id: item for item in report.workers}


def test_default_v015_worker_inventory_does_not_claim_operational_readiness(settings):
    report = worker_release_inventory(settings)
    assert report.schema_version == "zelo-v015-worker-release-inventory-v1"
    assert report.release_milestone == "v0.15"
    assert report.release_ready is False
    assert report.operational_proof_performed is False
    assert report.github_or_host_action_performed is False
    assert report.native_media_reader_attempt_performed is False
    assert tuple(item.worker_id for item in report.workers) == EXPECTED_WORKERS
    assert all(item.release_qualified is False for item in report.workers)
    assert all(item.new_authority_granted is False for item in report.workers)

    result = _by_id(report)
    assert result["codex_core"].configured_registration == "registered_conditional_authority"
    assert result["codex_reviewer"].configured_registration == "registered_conditional_authority"
    for worker_id in ("claude_core", "claude_reviewer", "opencode_core", "opencode_reviewer"):
        assert result[worker_id].configured_registration == "disabled_by_default"
    assert result["research_core"].configured_registration == "registered_conditional_authority"
    assert result["python_core"].configured_registration == "registered_conditional_authority"
    assert result["api_core"].configured_registration == "registered_no_approved_endpoints"
    assert result["browser_core"].configured_registration == "disabled_by_default"
    assert result["computer_core"].configured_registration == "disabled_by_default"
    assert result["computer_navigate_core"].configured_registration == "disabled_by_default"
    assert result["shell_core"].configured_registration == "disabled_by_default"
    assert result["media_native_reader"].configured_registration == "not_registered_in_core"


def test_optional_developer_registration_flags_do_not_claim_qualification(settings):
    configured = settings.model_copy(
        update={"developer_claude_enabled": True, "developer_opencode_enabled": True}
    )
    report = worker_release_inventory(configured)
    entries = _by_id(report)
    for worker_id in ("claude_core", "claude_reviewer", "opencode_core", "opencode_reviewer"):
        assert entries[worker_id].configured_registration == "configured_unverified"
        assert entries[worker_id].release_qualified is False
        assert entries[worker_id].new_authority_granted is False
    assert report.operational_proof_performed is False

    from app.developer import DeveloperRuntime

    actual = {item.worker_id: item for item in DeveloperRuntime(configured).registry.list()}
    assert set(actual) == {
        "codex_core",
        "codex_reviewer",
        "claude_core",
        "claude_reviewer",
        "opencode_core",
        "opencode_reviewer",
    }
    for worker_id, descriptor in actual.items():
        assert (entries[worker_id].role, entries[worker_id].capability) == (
            descriptor.role,
            descriptor.capabilities[0],
        )


def test_candidate_endpoints_do_not_imply_qualified_api_or_browser(settings):
    configured = settings.model_copy(
        update={
            "api_worker_endpoints": ("https://example.org",),
            "browser_worker_enabled": True,
            "browser_worker_endpoints": ("https://example.org",),
        }
    )
    entries = _by_id(worker_release_inventory(configured))
    assert entries["api_core"].configured_registration == "configured_unverified"
    assert entries["browser_core"].configured_registration == "configuration_incomplete"
    assert entries["browser_core"].release_qualified is False
    assert entries["api_core"].new_authority_granted is False


def test_candidate_shell_and_browser_receipt_flags_do_not_start_execution(settings, tmp_path):
    configured = settings.model_copy(
        update={
            "shell_worker_enabled": True,
            "shell_worker_admission": tmp_path / "nonexistent-shell-admission",
            "browser_worker_enabled": True,
            "browser_worker_endpoints": ("https://example.org",),
            "browser_worker_receipt": tmp_path / "nonexistent-browser-receipt",
            "browser_worker_bundle": tmp_path / "nonexistent-browser-bundle",
            "browser_worker_runtime": tmp_path / "nonexistent-browser-runtime",
            "browser_worker_admission": tmp_path / "nonexistent-browser-admission",
        }
    )
    report = worker_release_inventory(configured)
    entries = _by_id(report)
    # This means inputs are present, NOT that any file exists or any live
    # reader/browser/shell is operational or approved.
    assert entries["shell_core"].configured_registration == "configured_unverified"
    assert entries["browser_core"].configured_registration == "configured_unverified"
    assert report.operational_proof_performed is False
    assert report.release_ready is False
    assert not (tmp_path / "nonexistent-browser-runtime").exists()


def test_navigation_candidate_requires_backend_topology_and_named_endpoint(settings):
    configured = settings.model_copy(
        update={
            "computer_supervisor_url": "configured-only",
            "computer_supervisor_token": object(),
            "computer_token": object(),
            "computer_internal_network_cidr": "10.33.0.0/24",
            "computer_egress_proxy_host": "10.33.0.1",
        }
    )
    entries = _by_id(worker_release_inventory(configured))
    assert entries["computer_core"].configured_registration == "configured_unverified"
    assert entries["computer_navigate_core"].configured_registration == "disabled_by_default"
    with_named_endpoint = configured.model_copy(
        update={"computer_browser_endpoints": ("https://example.org",)}
    )
    row = _by_id(worker_release_inventory(with_named_endpoint))["computer_navigate_core"]
    assert row.configured_registration == "configured_unverified"
    assert row.release_qualified is False
    assert row.new_authority_granted is False


def test_inventory_never_emits_configuration_paths_endpoint_urls_or_tokens(settings, tmp_path):
    configured = settings.model_copy(
        update={
            "api_worker_endpoints": ("https://private.example.invalid/credential",),
            "shell_worker_admission": tmp_path / "private-file",
        }
    )
    serialized = json.dumps(asdict(worker_release_inventory(configured)))
    assert "private.example.invalid" not in serialized
    assert str(tmp_path) not in serialized
    assert "credential" not in serialized
    assert "token" not in serialized.lower()
    assert "secret" not in serialized.lower()
    assert "not_registered_in_core" in serialized


def test_unknown_config_object_cannot_be_used_as_release_signal():
    with pytest.raises(TypeError, match="settings_required"):
        worker_release_inventory({"browser_worker_enabled": True})


def test_actual_default_core_worker_registration_stays_inside_release_inventory(client, settings):
    """Exercise Core assembly, not just the metadata projection, with no provider action.

    A Worker registry object or a ComputerRuntime constructor is not evidence
    that its backend or a new capability is enabled. This catches a default
    registration change that the source-only inventory would otherwise miss.
    """
    state = client.app.state
    report = worker_release_inventory(settings)
    metadata = _by_id(report)
    assert report.release_ready is False
    assert report.operational_proof_performed is False

    registrations = {
        "developer": tuple(
            item.worker_id for item in state.runner.developer_runtime.registry.list()
        ),
        "research": tuple(item.worker_id for item in state.research_registry.list()),
        "python": tuple(item.worker_id for item in state.python_registry.list()),
        "api": tuple(item.worker_id for item in state.api_worker_registry.list()),
        "browser": tuple(item.worker_id for item in state.browser_worker_registry.list()),
        "computer": tuple(item.worker_id for item in state.computer_worker_registry.list()),
        "shell": tuple(item.worker_id for item in state.shell_worker_registry.list()),
    }
    assert registrations == {
        "developer": ("codex_core", "codex_reviewer"),
        "research": ("research_core",),
        "python": ("python_core",),
        "api": ("api_core",),
        "browser": (),
        "computer": (),
        "shell": (),
    }
    for descriptor in state.runner.developer_runtime.registry.list():
        assert (metadata[descriptor.worker_id].role, metadata[descriptor.worker_id].capability) == (
            descriptor.role,
            descriptor.capabilities[0],
        )
    assert state.api_endpoint_registry.list() == ()
    assert state.computer_browser_endpoint_registry.list() == ()
    assert not state.agent_computers.configured
    assert state.browser_runtime is None
    assert state.shell_runtime is None
    assert state.computer_egress_proxy is None
    assert state.tasks.browser_supervisor is state.browser_supervisor
    assert state.tasks.computer_supervisor is state.computer_supervisor
    assert state.tasks.shell_supervisor is state.shell_supervisor
    assert metadata["api_core"].configured_registration == "registered_no_approved_endpoints"
    assert metadata["computer_core"].configured_registration == "disabled_by_default"
    assert metadata["media_native_reader"].configured_registration == "not_registered_in_core"
    assert all("media_native_reader" not in ids for ids in registrations.values())
    assert all(
        not item.release_qualified and not item.new_authority_granted for item in report.workers
    )


@pytest.mark.parametrize(
    ("task_input", "denial"),
    (
        (
            ApiRunInput(kind="api_run", payload={"endpoint_id": "unapproved", "path": "/"}),
            "api_endpoint_not_registered",
        ),
        (
            BrowserRunInput(kind="browser_run", payload={"endpoint_id": "unapproved", "path": "/"}),
            "no_eligible_worker",
        ),
        (
            ShellRunInput(kind="shell_run", payload={"script": "printf 'unapproved'"}),
            "no_eligible_worker",
        ),
        (
            ComputerRunInput(kind="computer_run", payload={"action": "browser_snapshot"}),
            "Computer worker requires Agent execution",
        ),
    ),
)
def test_default_unapproved_worker_task_refused_without_persistence(client, task_input, denial):
    """Core's actual admission gate rejects unqualified actions before Task creation."""
    service = client.app.state.tasks
    before = service.list_tasks(limit=100, offset=0).total
    with pytest.raises(DomainError, match=denial):
        service.submit(task_input, None)
    assert service.list_tasks(limit=100, offset=0).total == before
