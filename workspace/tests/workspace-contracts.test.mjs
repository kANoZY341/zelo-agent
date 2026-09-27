import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  createAbortDeadline,
  pageBackfillOffset,
  pendingAfterSettlement,
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

test("hierarchy pending accounting cannot go negative", () => {
  assert.equal(pendingAfterSettlement(3), 2);
  assert.equal(pendingAfterSettlement(1), 0);
  assert.equal(pendingAfterSettlement(0), 0);
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

test("Control Desk uses latest-events and Talk keeps its normal timing contract", async () => {
  const overview = await readFile(
    new URL("../app/api/campaigns/[id]/overview/route.ts", import.meta.url),
    "utf8",
  );
  const talkTurns = await readFile(
    new URL("../app/api/conversations/[id]/turns/route.ts", import.meta.url),
    "utf8",
  );
  const projects = await readFile(
    new URL("../app/api/projects/route.ts", import.meta.url),
    "utf8",
  );

  assert.match(overview, /events\/latest\?limit=100/);
  assert.doesNotMatch(overview, /totalEvents|eventOffset|events\?limit=1&offset=0/);
  assert.doesNotMatch(talkTurns, /timeoutMs/);
  assert.match(projects, /timeoutMs:\s*30000/);
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
