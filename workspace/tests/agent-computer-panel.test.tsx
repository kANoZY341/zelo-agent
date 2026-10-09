import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { Window } from "happy-dom";
import { AgentComputerPanel } from "../components/AgentComputerPanel";
import {
  verifyPanelProjection,
  validComputerInstanceId,
  visiblePanelTabs,
} from "../lib/computer-panel";
import { GET as proxyComputerPanel } from "../app/api/agents/[id]/computer/panel/route";

const INSTANCE = "11111111-1111-4111-8111-111111111111";
const windowInstance = new Window({ url: "http://localhost/" });

function installGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
}
installGlobal("window", windowInstance);
installGlobal("document", windowInstance.document);
installGlobal("navigator", windowInstance.navigator);
installGlobal("HTMLElement", windowInstance.HTMLElement);
installGlobal("Node", windowInstance.Node);
installGlobal("Event", windowInstance.Event);
installGlobal("MouseEvent", windowInstance.MouseEvent);
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);

function projection(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "zelo-opendots-computer-panel-preview-v1",
    agent_instance_id: INSTANCE,
    backend: "openbot",
    state: "running",
    browser_tab_visible: true,
    files_tab_visible: true,
    terminal_tab_visible: false,
    activity_tab_visible: true,
    browser_tab_ready: true,
    files_tab_ready: false,
    terminal_tab_ready: false,
    recent_event_names: ["started", "taken_by_human"],
    total_events: 25,
    human_control_holder: "human",
    take_control_via_existing_core_only: true,
    new_control_granted: false,
    new_browser_mutation_granted: false,
    new_file_or_shell_mutation_granted: false,
    upstream_api_or_runtime_loaded: false,
    view_only: true,
    ...overrides,
  };
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

test("projection refuses forged permission flags, mismatched IDs and unsafe metadata", () => {
  const valid = verifyPanelProjection(projection(), INSTANCE);
  assert.deepEqual(visiblePanelTabs(valid), ["Browser", "Files", "Activity"]);
  assert.equal(valid.view_only, true);
  assert.equal(valid.new_control_granted, false);
  assert.equal(validComputerInstanceId(INSTANCE), true);
  assert.equal(validComputerInstanceId("../../etc/passwd"), false);
  for (const changed of [
    { view_only: false },
    { new_control_granted: true },
    { new_browser_mutation_granted: true },
    { new_file_or_shell_mutation_granted: true },
    { agent_instance_id: "22222222-2222-4222-8222-222222222222" },
    { recent_event_names: Array.from({ length: 11 }, () => "event") },
    { state: "stopped", browser_tab_ready: true },
    { browser_tab_ready: true, browser_tab_visible: false },
  ]) {
    assert.throws(() => verifyPanelProjection(projection(changed), INSTANCE));
  }
});

test("owner-only Next route proxies exactly one read and never forwards credentials", async () => {
  const runtime = globalThis as unknown as { fetch: typeof fetch };
  const previousFetch = runtime.fetch;
  const previousUrl = process.env.ZELO_CORE_URL;
  const previousToken = process.env.ZELO_OPERATOR_TOKEN;
  process.env.ZELO_CORE_URL = "http://127.0.0.1:8000";
  process.env.ZELO_OPERATOR_TOKEN = "trusted-server-only-test-token";
  let called = 0;
  try {
    runtime.fetch = async (input, init) => {
      called += 1;
      assert.equal(String(input), `http://127.0.0.1:8000/agents/${INSTANCE}/computer/panel`);
      assert.equal(init?.method, "GET");
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("authorization"), "Bearer trusted-server-only-test-token");
      assert.equal(headers.get("x-attacker"), null);
      return Response.json(projection());
    };
    const route = `http://localhost/api/agents/${INSTANCE}/computer/panel`;
    const blocked = await proxyComputerPanel(new Request(route), { params: Promise.resolve({ id: INSTANCE }) });
    assert.equal(blocked.status, 403);
    assert.equal(called, 0);
    const wrong = await proxyComputerPanel(
      new Request(route, { headers: { "X-Zelo-Workspace": "1" } }),
      { params: Promise.resolve({ id: "../etc" }) },
    );
    assert.equal(wrong.status, 422);
    assert.equal(called, 0);
    const response = await proxyComputerPanel(
      new Request(route, {
        headers: { "X-Zelo-Workspace": "1", "Authorization": "Bearer attacker", "X-Attacker": "evil" },
      }),
      { params: Promise.resolve({ id: INSTANCE }) },
    );
    assert.equal(response.status, 200);
    assert.equal(called, 1);
    assert.equal((await response.json()).view_only, true);
  } finally {
    runtime.fetch = previousFetch;
    if (previousUrl === undefined) delete process.env.ZELO_CORE_URL;
    else process.env.ZELO_CORE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_OPERATOR_TOKEN;
    else process.env.ZELO_OPERATOR_TOKEN = previousToken;
  }
});

test("standalone React panel displays state without providing mutation controls", async () => {
  const runtime = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtime.fetch;
  const container = windowInstance.document.createElement("div") as unknown as HTMLElement;
  windowInstance.document.body.appendChild(container as unknown as never);
  const root = createRoot(container);
  const calls: string[] = [];
  try {
    runtime.fetch = async (input, init) => {
      calls.push(String(input));
      assert.equal(new Headers(init?.headers).get("x-zelo-workspace"), "1");
      assert.equal(init?.cache, "no-store");
      return Response.json(projection());
    };
    await act(async () => root.render(<AgentComputerPanel agentInstanceId={INSTANCE} />));
    await settle();
    assert.equal(calls.length, 1);
    assert.match(container.textContent ?? "", /Agent Computer/);
    assert.match(container.textContent ?? "", /25 recorded events/);
    assert.match(container.textContent ?? "", /taken_by_human/);
    assert.doesNotMatch(container.textContent ?? "", /Run Shell|Write File|Take Control/);
    const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>("button"));
    assert.deepEqual(buttons.map((button) => button.textContent?.trim()), ["Browser", "Files", "Activity"]);
    await act(async () => buttons[0].click());
    await settle();
    assert.match(container.textContent ?? "", /Browser permission is available through Core/);
    assert.equal(calls.length, 1); // switching a tab never invokes Core mutation
  } finally {
    await act(async () => root.unmount());
    container.remove();
    runtime.fetch = originalFetch;
  }
});

test("bad agent identity refuses before network access", async () => {
  const runtime = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtime.fetch;
  const container = windowInstance.document.createElement("div") as unknown as HTMLElement;
  windowInstance.document.body.appendChild(container as unknown as never);
  const root = createRoot(container);
  try {
    runtime.fetch = () => { throw new Error("network not allowed"); };
    await act(async () => root.render(<AgentComputerPanel agentInstanceId="../bad" />));
    await settle();
    assert.match(container.textContent ?? "", /Invalid agent instance/);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    runtime.fetch = originalFetch;
  }
});
