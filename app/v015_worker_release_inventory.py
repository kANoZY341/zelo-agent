"""Conservative v0.15 Worker inventory: config inspection, never runtime proof.

No backend, native Media, external source or network actions are performed.
"""

from dataclasses import dataclass
from typing import Literal

from app.config import Settings
from app.worker_registry import (
    descriptor_for_api_worker,
    descriptor_for_browser_worker,
    descriptor_for_computer_navigate_worker,
    descriptor_for_computer_worker,
    descriptor_for_python_worker,
    descriptor_for_research_worker,
    descriptor_for_shell_worker,
)

RegistrationStatus = Literal[
    "registered_conditional_authority",
    "registered_no_approved_endpoints",
    "disabled_by_default",
    "configuration_incomplete",
    "configured_unverified",
    "not_registered_in_core",
]


@dataclass(frozen=True, slots=True)
class WorkerScope:
    """Worker metadata only; never an Agent or Worker Authority grant."""

    worker_id: str
    role: str
    capability: str
    configured_registration: RegistrationStatus
    release_qualified: bool = False
    new_authority_granted: bool = False


@dataclass(frozen=True, slots=True)
class WorkerReleaseInventory:
    schema_version: Literal["zelo-v015-worker-release-inventory-v1"]
    release_milestone: Literal["v0.15"]
    release_ready: Literal[False]
    operational_proof_performed: Literal[False]
    github_or_host_action_performed: Literal[False]
    native_media_reader_attempt_performed: Literal[False]
    workers: tuple[WorkerScope, ...]


def _row(descriptor, state: RegistrationStatus) -> WorkerScope:
    return WorkerScope(
        worker_id=descriptor.worker_id,
        role=descriptor.role,
        capability=descriptor.capabilities[0],
        configured_registration=state,
    )


def worker_release_inventory(settings: Settings) -> WorkerReleaseInventory:
    """Inspect validated config only; registration is NOT use-point permission."""
    if not isinstance(settings, Settings):
        raise TypeError("v015_worker_inventory_settings_required")

    backend_configured = all(
        item is not None
        for item in (
            settings.computer_supervisor_url,
            settings.computer_supervisor_token,
            settings.computer_token,
        )
    )
    computer_network_ready = (
        backend_configured
        and settings.computer_internal_network_cidr is not None
        and settings.computer_egress_proxy_host is not None
        and bool(settings.computer_browser_endpoints)
    )
    browser_inputs_complete = all(
        item is not None
        for item in (
            settings.browser_worker_receipt,
            settings.browser_worker_bundle,
            settings.browser_worker_runtime,
            settings.browser_worker_admission,
        )
    ) and bool(settings.browser_worker_endpoints)

    if not settings.browser_worker_enabled:
        browser_status: RegistrationStatus = "disabled_by_default"
    elif not browser_inputs_complete:
        browser_status = "configuration_incomplete"
    else:
        browser_status = "configured_unverified"

    if not settings.shell_worker_enabled:
        shell_status: RegistrationStatus = "disabled_by_default"
    elif settings.shell_worker_admission is None:
        shell_status = "configuration_incomplete"
    else:
        shell_status = "configured_unverified"

    return WorkerReleaseInventory(
        schema_version="zelo-v015-worker-release-inventory-v1",
        release_milestone="v0.15",
        release_ready=False,
        operational_proof_performed=False,
        github_or_host_action_performed=False,
        native_media_reader_attempt_performed=False,
        workers=(
            _row(descriptor_for_research_worker(), "registered_conditional_authority"),
            _row(descriptor_for_python_worker(), "registered_conditional_authority"),
            _row(
                descriptor_for_api_worker(),
                (
                    "configured_unverified"
                    if settings.api_worker_endpoints
                    else "registered_no_approved_endpoints"
                ),
            ),
            _row(descriptor_for_browser_worker(), browser_status),
            _row(
                descriptor_for_computer_worker(),
                "configured_unverified" if backend_configured else "disabled_by_default",
            ),
            _row(
                descriptor_for_computer_navigate_worker(),
                "configured_unverified" if computer_network_ready else "disabled_by_default",
            ),
            _row(descriptor_for_shell_worker(), shell_status),
            WorkerScope(
                worker_id="media_native_reader",
                role="media",
                capability="native_media_reader",
                configured_registration="not_registered_in_core",
            ),
        ),
    )
