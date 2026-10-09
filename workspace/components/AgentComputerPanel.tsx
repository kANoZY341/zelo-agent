"use client";

import { useEffect, useMemo, useState } from "react";
import {
  type AgentComputerPanelProjection,
  type PanelTab,
  validComputerInstanceId,
  verifyPanelProjection,
  visiblePanelTabs,
} from "@/lib/computer-panel";

const WORKSPACE_HEADERS = { "X-Zelo-Workspace": "1" };
const REFRESH_MS = 10000;

/**
 * Zelo-owned read-only OpenDots Computer Panel adaptation.
 * It deliberately contains NO start/stop, take/release, URL navigation,
 * file write, shell, or image/browser execution controls. Those existing
 * Core functions require separate operator permissions and future UI review.
 * This component is not mounted in the parked production workspace.
 */
export function AgentComputerPanel({ agentInstanceId }: { agentInstanceId: string }) {
  const [status, setStatus] = useState<AgentComputerPanelProjection | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<PanelTab>("Activity");
  const instanceValid = validComputerInstanceId(agentInstanceId);

  useEffect(() => {
    setStatus(null);
    setError("");
    setLoading(true);
    if (!instanceValid) {
      setError("Invalid agent instance");
      setLoading(false);
      return;
    }
    let active = true;
    let controller: AbortController | null = null;
    const refresh = async () => {
      if (controller) return; // no overlapping or out-of-order polling
      controller = new AbortController();
      try {
        const response = await fetch(
          `/api/agents/${agentInstanceId}/computer/panel`,
          { headers: WORKSPACE_HEADERS, signal: controller.signal, cache: "no-store" },
        );
        if (!response.ok) throw new Error(`Core panel unavailable (${response.status})`);
        const next = verifyPanelProjection(await response.json(), agentInstanceId);
        if (active) {
          setStatus(next);
          setError("");
        }
      } catch (failure) {
        if (active && !controller?.signal.aborted) {
          setError(failure instanceof Error ? failure.message : "Core panel unavailable");
          setStatus(null); // stale readiness may not imply retained authority
        }
      } finally {
        controller = null;
        if (active) setLoading(false);
      }
    };
    void refresh();
    const interval = setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      active = false;
      controller?.abort();
      clearInterval(interval);
    };
  }, [agentInstanceId, instanceValid]);

  const tabs = useMemo(() => status ? visiblePanelTabs(status) : ["Activity" as PanelTab], [status]);
  const selected = tabs.includes(tab) ? tab : tabs[0];
  const badge = status?.state === "running" ? "Running" : status?.state ?? "Unavailable";
  const ready = selected === "Browser" ? status?.browser_tab_ready
    : selected === "Files" ? status?.files_tab_ready
    : selected === "Terminal" ? status?.terminal_tab_ready : true;

  return (
    <section aria-label="Agent Computer Panel" data-testid="zelo-computer-panel" className="zelo-computer-panel">
      <header>
        <h2>Agent Computer</h2>
        <p>Read-only view · {badge} · {status?.human_control_holder ?? "none"} control</p>
      </header>
      {loading && !status && !error ? <p role="status">Reading Core computer state…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      <nav aria-label="Computer tabs">
        {tabs.map((name) => (
          <button
            type="button"
            key={name}
            aria-current={selected === name ? "page" : undefined}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>
      {status ? (
        <div aria-live="polite">
          {selected === "Activity" ? (
            <section aria-label="Recent activity">
              <p>{status.total_events} recorded events (latest {status.recent_event_names.length})</p>
              {status.recent_event_names.length ? (
                <ul>{status.recent_event_names.map((event, i) => <li key={`${i}-${event}`}>{event}</li>)}</ul>
              ) : <p>No recorded computer activity</p>}
            </section>
          ) : (
            <section aria-label={`${selected} status`}>
              <p>{ready ? `${selected} permission is available through Core` : `${selected} is not currently available`}</p>
              <p>Interactive operations require separate operator controls and review.</p>
            </section>
          )}
        </div>
      ) : null}
    </section>
  );
}
