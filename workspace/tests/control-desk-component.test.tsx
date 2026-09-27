import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { ControlDesk } from "../components/ControlDesk";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const now = "2026-09-27T00:00:00Z";

function page(items: unknown[], total = items.length) {
  return { items, limit: 25, offset: 0, total };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

async function flushEffects(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

test("Campaign A -> empty Goal aborts overview and clears loading state", async () => {
  const runtimeGlobals = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtimeGlobals.fetch;
  let overviewAborted = false;
  let overviewStarted = false;

  const project = {
    id: "project-a",
    name: "Zelo",
    summary: "test",
    status: "active",
    created_at: now,
    updated_at: now,
  };
  const goalA = {
    id: "goal-a",
    project_id: project.id,
    title: "Goal A",
    spec: { objective: "A", success_criteria: [], constraints: [] },
    status: "active",
    created_at: now,
    updated_at: now,
  };
  const emptyGoal = {
    ...goalA,
    id: "goal-empty",
    title: "Empty Goal",
    spec: { objective: "Empty", success_criteria: [], constraints: [] },
  };
  const campaign = {
    id: "campaign-a",
    goal_id: goalA.id,
    title: "Campaign A",
    summary: "test",
    status: "active",
    task_count: 0,
    created_at: now,
    updated_at: now,
  };

  runtimeGlobals.fetch = (input, init) => {
    const url = String(input);
    if (url.startsWith("/api/projects?")) return Promise.resolve(json(page([project])));
    if (url.includes("/projects/project-a/goals")) {
      return Promise.resolve(json(page([goalA, emptyGoal])));
    }
    if (url.includes("/goals/goal-a/campaigns")) {
      return Promise.resolve(json(page([campaign])));
    }
    if (url.includes("/goals/goal-empty/campaigns")) {
      return Promise.resolve(json(page([])));
    }
    if (url.includes("/campaigns/campaign-a/overview")) {
      overviewStarted = true;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => {
            overviewAborted = true;
            reject(new DOMException("Aborted", "AbortError"));
          },
          { once: true },
        );
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  };

  let renderer: TestRenderer.ReactTestRenderer | null = null;
  try {
    await act(async () => {
      renderer = TestRenderer.create(
        <ControlDesk coreHealthy={true} onShowTalk={() => undefined} />,
      );
    });
    await flushEffects();
    assert.equal(overviewStarted, true);

    const goalButtons = renderer!.root.findAll(
      (node) =>
        node.type === "button" &&
        typeof node.props.className === "string" &&
        node.props.className.includes("goal-card"),
    );
    assert.equal(goalButtons.length, 2);

    await act(async () => {
      goalButtons[1].props.onClick();
    });
    await flushEffects();

    assert.equal(overviewAborted, true);
    const rendered = JSON.stringify(renderer!.toJSON());
    assert.doesNotMatch(rendered, /Loading campaign state/);
    assert.match(rendered, /No campaigns attached to this goal/);
    assert.match(rendered, /Select a campaign to inspect/);
  } finally {
    if (renderer) {
      await act(async () => renderer!.unmount());
    }
    runtimeGlobals.fetch = originalFetch;
  }
});

test("empty parent hierarchy leaves no 30-second deadline timers after settlement/unmount", async () => {
  const runtimeGlobals = globalThis as unknown as {
    fetch: typeof fetch;
    setTimeout: (...args: unknown[]) => unknown;
    clearTimeout: (...args: unknown[]) => unknown;
  };
  const originalFetch = runtimeGlobals.fetch;
  const originalSetTimeout = runtimeGlobals.setTimeout;
  const originalClearTimeout = runtimeGlobals.clearTimeout;
  const activeDeadlines = new Set<unknown>();

  runtimeGlobals.setTimeout = (
    handler: unknown,
    timeout?: unknown,
    ...args: unknown[]
  ) => {
    let timer: unknown;
    timer = originalSetTimeout(
      () => {
        activeDeadlines.delete(timer);
        if (typeof handler === "function") {
          (handler as (...values: unknown[]) => void)(...args);
        }
      },
      timeout,
    );
    if (timeout === 30000) activeDeadlines.add(timer);
    return timer;
  };
  runtimeGlobals.clearTimeout = (timer: unknown) => {
    activeDeadlines.delete(timer);
    return originalClearTimeout(timer);
  };
  runtimeGlobals.fetch = async (input) => {
    const url = String(input);
    if (url.startsWith("/api/projects?")) return json(page([]));
    throw new Error(`unexpected fetch: ${url}`);
  };

  let renderer: TestRenderer.ReactTestRenderer | null = null;
  try {
    await act(async () => {
      renderer = TestRenderer.create(
        <ControlDesk coreHealthy={true} onShowTalk={() => undefined} />,
      );
    });
    await flushEffects();
    assert.equal(activeDeadlines.size, 0);

    await act(async () => renderer!.unmount());
    renderer = null;
    assert.equal(activeDeadlines.size, 0);
  } finally {
    if (renderer) await act(async () => renderer!.unmount());
    runtimeGlobals.fetch = originalFetch;
    runtimeGlobals.setTimeout = originalSetTimeout;
    runtimeGlobals.clearTimeout = originalClearTimeout;
  }
});
