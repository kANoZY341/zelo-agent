import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  createAbortDeadline,
  pageBackfillOffset,
  PendingRequestTracker,
  shouldShowDetailLoading,
} from "../lib/control-desk-state.ts";

test("empty selection never renders stale campaign loading", () => {
  assert.equal(shouldShowDetailLoading(null, true, false, false), false);
  assert.equal(shouldShowDetailLoading("campaign-a", true, false, false), true);
  assert.equal(shouldShowDetailLoading("campaign-a", true, true, true), false);
});

test("empty final pages retreat by one bounded page", () => {
  assert.equal(pageBackfillOffset(0, 26, 25, 25), 0);
  assert.equal(pageBackfillOffset(0, 51, 50, 25), 25);
  assert.equal(pageBackfillOffset(1, 51, 50, 25), null);
  assert.equal(pageBackfillOffset(0, 0, 25, 25), null);
});

test("hierarchy pending accounting follows request settlement", () => {
  const pending = new PendingRequestTracker();
  assert.equal(pending.isIdle(), true);
  pending.begin();
  pending.begin();
  assert.equal(pending.count(), 2);
  assert.equal(pending.isIdle(), false);
  pending.settle();
  assert.equal(pending.count(), 1);
  pending.settle();
  assert.equal(pending.isIdle(), true);
  assert.throws(() => pending.settle(), /underflow/);
});

test("abort deadlines time out and can be cleared", async () => {
  const timed = new AbortController();
  const deadline = createAbortDeadline(timed, 5);
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(timed.signal.aborted, true);
  assert.equal(deadline.didTimeout(), true);

  const cleared = new AbortController();
  const clearedDeadline = createAbortDeadline(cleared, 10);
  clearedDeadline.clear();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(cleared.signal.aborted, false);
  assert.equal(clearedDeadline.didTimeout(), false);
});

test("proxyCore has no implicit Talk deadline and honors opt-in Desk deadlines", async () => {
  const { proxyCore } = await import("../lib/core.ts");
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const previousUrl = process.env.ZELO_CORE_URL;
  const previousToken = process.env.ZELO_OPERATOR_TOKEN;

  process.env.ZELO_CORE_URL = "http://127.0.0.1:8000";
  process.env.ZELO_OPERATOR_TOKEN = "test-only-token";

  try {
    let scheduled = 0;
    globalThis.setTimeout = ((...args) => {
      scheduled += 1;
      return originalSetTimeout(...args);
    }) as typeof setTimeout;
    globalThis.fetch = (async (_input, init) => {
      assert.equal(init?.signal?.aborted, false);
      return new Response('{"ok":true}', {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    const talkResponse = await proxyCore("/talk-like-read");
    assert.equal(talkResponse.status, 200);
    assert.equal(scheduled, 0);

    globalThis.setTimeout = originalSetTimeout;
    globalThis.fetch = ((_input, init) =>
      new Promise<Response>((resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
        originalSetTimeout(
          () =>
            resolve(
              new Response('{"ok":true}', {
                status: 200,
                headers: { "content-type": "application/json" },
              }),
            ),
          30,
        );
      })) as typeof fetch;

    const deskResponse = await proxyCore(
      "/desk-read",
      {},
      { timeoutMs: 5 },
    );
    assert.equal(deskResponse.status, 503);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    if (previousUrl === undefined) delete process.env.ZELO_CORE_URL;
    else process.env.ZELO_CORE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_OPERATOR_TOKEN;
    else process.env.ZELO_OPERATOR_TOKEN = previousToken;
  }
});

test("Control Desk overview uses Core latest-events rather than a calculated tail offset", async () => {
  const overview = await readFile(
    new URL("../app/api/campaigns/[id]/overview/route.ts", import.meta.url),
    "utf8",
  );
  assert.match(overview, /events\/latest\?limit=100/);
  assert.doesNotMatch(overview, /totalEvents|eventOffset|events\?limit=1&offset=0/);
});

test("responsive navigation contracts remain present", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 560px\)/);
  assert.match(css, /\.topbar,\s*\.desk-topbar\s*\{[\s\S]*?flex-direction:\s*column/);
  assert.match(
    css,
    /\.mobile-project-controls\s*\{[\s\S]*?grid-template-columns:\s*34px minmax\(0, 1fr\) 34px 34px/,
  );
  assert.match(css, /\.desk-topbar \.mobile-view-button/);
});
