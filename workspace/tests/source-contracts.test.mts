import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function source(path) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

test("Control Desk reads use bounded deadlines while Talk does not", async () => {
  const projectRoute = await source("../app/api/projects/route.ts");
  const goalRoute = await source("../app/api/projects/[id]/goals/route.ts");
  const campaignRoute = await source("../app/api/goals/[id]/campaigns/route.ts");
  const overviewRoute = await source("../app/api/campaigns/[id]/overview/route.ts");
  const talkRoute = await source("../app/api/conversations/[id]/turns/route.ts");

  for (const route of [projectRoute, goalRoute, campaignRoute, overviewRoute]) {
    assert.match(route, /timeoutMs:\s*30000/);
  }
  assert.doesNotMatch(talkRoute, /timeoutMs/);
});

test("overview uses latest events instead of offset tail arithmetic", async () => {
  const overviewRoute = await source("../app/api/campaigns/[id]/overview/route.ts");
  assert.match(overviewRoute, /events\/latest\?limit=100/);
  assert.doesNotMatch(overviewRoute, /eventOffset|events\?limit=1&offset=0/);
});

test("responsive navigation keeps constrained titles and narrow controls", async () => {
  const css = await source("../app/globals.css");
  assert.match(css, /\.topbar > div:first-child,[\s\S]*min-width:\s*0/);
  assert.match(css, /\.topbar h1,[\s\S]*text-overflow:\s*ellipsis/);
  assert.match(
    css,
    /@media \(max-width: 560px\)[\s\S]*\.topbar,[\s\S]*flex-direction:\s*column/,
  );
  assert.match(
    css,
    /grid-template-columns:\s*34px minmax\(0, 1fr\) 34px 34px/,
  );
});
