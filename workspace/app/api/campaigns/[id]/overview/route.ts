import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

async function readCoreJson(path: string): Promise<{
  ok: boolean;
  status: number;
  body: unknown;
}> {
  const response = await proxyCore(path);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = { detail: `core_http_${response.status}` };
  }
  return { ok: response.ok, status: response.status, body };
}

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const { id } = await context.params;
  const campaignId = encodeURIComponent(id);
  const [campaign, events, budgets, schedules] = await Promise.all([
    readCoreJson(`/campaigns/${campaignId}`),
    readCoreJson(`/campaigns/${campaignId}/events?limit=100&offset=0`),
    readCoreJson(`/campaigns/${campaignId}/budgets?limit=100&offset=0`),
    readCoreJson(`/campaigns/${campaignId}/schedules?limit=100&offset=0`),
  ]);

  for (const result of [campaign, events, budgets, schedules]) {
    if (!result.ok) {
      return Response.json(result.body, {
        status: result.status,
        headers: { "Cache-Control": "no-store" },
      });
    }
  }

  return Response.json(
    {
      campaign: campaign.body,
      events: events.body,
      budgets: budgets.body,
      schedules: schedules.body,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
