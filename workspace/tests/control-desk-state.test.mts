import assert from "node:assert/strict";
import test from "node:test";

import {
  canRefreshHierarchy,
  campaignOverviewForSelection,
  recoverPreviousPageOffset,
  shouldShowDetailLoading,
  visibleOverviewForOffsets,
} from "../lib/control-desk-state.ts";

const overview = {
  campaign: { id: "campaign-a" },
  budgets: { offset: 25 },
  schedules: { offset: 50 },
};

test("empty final pages recover to the previous bounded page", () => {
  assert.equal(recoverPreviousPageOffset(50, 50, 0, 25), 25);
  assert.equal(recoverPreviousPageOffset(0, 0, 0, 25), null);
  assert.equal(recoverPreviousPageOffset(25, 50, 25, 25), null);
});

test("overview identity and detail offsets must match current selection", () => {
  const selected = campaignOverviewForSelection(overview, "campaign-a");
  assert.equal(selected, overview);
  assert.equal(campaignOverviewForSelection(overview, "campaign-b"), null);
  assert.equal(visibleOverviewForOffsets(selected, 25, 50), overview);
  assert.equal(visibleOverviewForOffsets(selected, 0, 50), null);
  assert.equal(visibleOverviewForOffsets(selected, 25, 0), null);
});

test("no campaign can never render a stale campaign-loading indicator", () => {
  assert.equal(shouldShowDetailLoading(null, true, null, null), false);
  assert.equal(shouldShowDetailLoading("campaign-a", true, null, null), true);
  assert.equal(
    shouldShowDetailLoading("campaign-a", true, overview, overview),
    false,
  );
});

test("hierarchy revalidation waits for active reads", () => {
  assert.equal(canRefreshHierarchy(0), true);
  assert.equal(canRefreshHierarchy(1), false);
  assert.equal(canRefreshHierarchy(3), false);
});
