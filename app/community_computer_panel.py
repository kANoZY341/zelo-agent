"""OpenDots-inspired read-only Computer Panel data projected from Zelo Core.

The upstream ComputerPanel.tsx is pinned inert source; its Dot routes, store,
permissions and network actions are NOT imported. Only existing Core-owned
AgentComputerService status and event metadata is used. No UI mutation.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_computer_service import AgentComputerService
from qualification.community_sources import verify_community_sources


class ComputerPanelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-opendots-computer-panel-preview-v1"] = (
        "zelo-opendots-computer-panel-preview-v1"
    )
    agent_instance_id: str = Field(min_length=36, max_length=36)
    backend: Literal["openbot"] = "openbot"
    state: Literal["not_configured", "stopped", "running", "unavailable"]
    browser_tab_visible: bool
    files_tab_visible: bool
    terminal_tab_visible: bool
    activity_tab_visible: Literal[True] = True
    browser_tab_ready: bool
    files_tab_ready: bool
    terminal_tab_ready: bool
    recent_event_names: tuple[str, ...] = Field(max_length=10)
    total_events: int = Field(ge=0)
    human_control_holder: Literal["bot", "human", "none"]
    take_control_via_existing_core_only: Literal[True] = True
    new_control_granted: Literal[False] = False
    new_browser_mutation_granted: Literal[False] = False
    new_file_or_shell_mutation_granted: Literal[False] = False
    upstream_api_or_runtime_loaded: Literal[False] = False
    view_only: Literal[True] = True


async def project_computer_panel(
    computer_service: AgentComputerService, agent_instance_id: str
) -> ComputerPanelContract:
    """Read one authorized computer identity; never activate start/take/run."""

    verify_community_sources()
    status = await computer_service.status(agent_instance_id)
    events = computer_service.events(agent_instance_id, limit=10, offset=0)
    # Core events are ordered by ascending sequence. A first-page-only
    # preview becomes permanently stale once the agent has >10 events.
    # Re-read the bounded tail through the same operator-owned service;
    # two independent read snapshots may differ but grant no authority.
    if events.total > 10:
        events = computer_service.events(agent_instance_id, limit=10, offset=events.total - 10)
    # Tabs may display existing status; permissions remain enforced by Core
    # at action dispatch and cannot be fabricated by this UI projection.
    active = bool(status.configured and status.state == "running" and status.permissions.enabled)
    return ComputerPanelContract(
        agent_instance_id=status.agent_instance_id,
        state=status.state,
        browser_tab_visible=status.spec.browser,
        files_tab_visible=status.spec.files,
        terminal_tab_visible=status.spec.shell,
        browser_tab_ready=active and status.spec.browser and status.permissions.browser,
        files_tab_ready=active and status.spec.files and status.permissions.files,
        terminal_tab_ready=active and status.spec.shell and status.permissions.shell,
        recent_event_names=tuple(item.event for item in events.items),
        total_events=events.total,
        human_control_holder=(status.control.holder if status.control is not None else "none"),
    )
