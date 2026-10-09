/**
 * Read-only Zelo-owned projection of the reviewed OpenDots Computer Panel.
 * This file does not contain OpenDots' Dot model, stores, or action transport.
 */
export type ComputerPanelState = "not_configured" | "stopped" | "running" | "unavailable";
export type PanelTab = "Browser" | "Files" | "Terminal" | "Activity";

export type AgentComputerPanelProjection = {
  schema_version: "zelo-opendots-computer-panel-preview-v1";
  agent_instance_id: string;
  backend: "openbot";
  state: ComputerPanelState;
  browser_tab_visible: boolean;
  files_tab_visible: boolean;
  terminal_tab_visible: boolean;
  activity_tab_visible: true;
  browser_tab_ready: boolean;
  files_tab_ready: boolean;
  terminal_tab_ready: boolean;
  recent_event_names: string[];
  total_events: number;
  human_control_holder: "bot" | "human" | "none";
  take_control_via_existing_core_only: true;
  new_control_granted: false;
  new_browser_mutation_granted: false;
  new_file_or_shell_mutation_granted: false;
  upstream_api_or_runtime_loaded: false;
  view_only: true;
};

const CANONICAL_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function validComputerInstanceId(value: string): boolean {
  return CANONICAL_ID.test(value);
}

export function verifyPanelProjection(
  input: unknown,
  expectedInstanceId: string,
): AgentComputerPanelProjection {
  if (
    !validComputerInstanceId(expectedInstanceId) ||
    !input || typeof input !== "object" || Array.isArray(input)
  ) {
    throw new Error("computer_panel_identity_invalid");
  }
  const data = input as Record<string, unknown>;
  if (
    data.schema_version !== "zelo-opendots-computer-panel-preview-v1" ||
    data.agent_instance_id !== expectedInstanceId ||
    data.backend !== "openbot" ||
    !["not_configured", "stopped", "running", "unavailable"].includes(
      String(data.state),
    ) ||
    !["bot", "human", "none"].includes(String(data.human_control_holder)) ||
    data.activity_tab_visible !== true ||
    data.take_control_via_existing_core_only !== true ||
    data.view_only !== true ||
    data.new_control_granted !== false ||
    data.new_browser_mutation_granted !== false ||
    data.new_file_or_shell_mutation_granted !== false ||
    data.upstream_api_or_runtime_loaded !== false ||
    !Number.isInteger(data.total_events) ||
    (data.total_events as number) < 0 ||
    !Array.isArray(data.recent_event_names) ||
    data.recent_event_names.length > 10 ||
    !data.recent_event_names.every(
      (item: unknown) => typeof item === "string" && item.length >= 1 && item.length <= 64,
    )
  ) {
    throw new Error("computer_panel_data_invalid");
  }
  const booleans = [
    "browser_tab_visible", "files_tab_visible", "terminal_tab_visible",
    "browser_tab_ready", "files_tab_ready", "terminal_tab_ready",
  ];
  if (booleans.some((key) => typeof data[key] !== "boolean")) {
    throw new Error("computer_panel_grants_invalid");
  }
  const online = data.state === "running";
  if (
    (!online && ["browser_tab_ready", "files_tab_ready", "terminal_tab_ready"].some((key) => data[key])) ||
    (data.browser_tab_ready && !data.browser_tab_visible) ||
    (data.files_tab_ready && !data.files_tab_visible) ||
    (data.terminal_tab_ready && !data.terminal_tab_visible)
  ) {
    throw new Error("computer_panel_readiness_invalid");
  }
  return data as AgentComputerPanelProjection;
}

export function visiblePanelTabs(status: AgentComputerPanelProjection): PanelTab[] {
  const tabs: PanelTab[] = [];
  if (status.browser_tab_visible) tabs.push("Browser");
  if (status.files_tab_visible) tabs.push("Files");
  if (status.terminal_tab_visible) tabs.push("Terminal");
  tabs.push("Activity");
  return tabs;
}
