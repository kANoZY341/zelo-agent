import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

type CoreRead = {
  ok: boolean;
  status: number;
  body: unknown;
};

const MAX_PAGE = 100;
const MAX_OFFSET = 1_000_000;

function boundedInteger(
  value: string | null,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  if (value === null) return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    return fallback;
  }
  return parsed;
}

async function readCoreJson(
  path: string,
  signal: AbortSignal,
): Promise<CoreRead> {
  const response = await proxyCore(path, { signal });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = { detail: `core_http_${response.status}` };
  }
  return { ok: response.ok, status: response.status, body };
}

function pageTotal(body: unknown): number | null {
  if (
    typeof body !== "object" ||
    body === null ||
    !("total" in body) ||
    typeof body.total !== "number" ||
    !Number.isInteger(body.total) ||
    body.total < 0
  ) {
    return null;
  }
  return body.total;
}

function coreError(result: CoreRead): Response {
  return Response.json(result.body, {
    status: result.status,
    headers: { "Cache-Control": "no-store" },
  });
}

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const { id } = await context.params;
  const requestUrl = new URL(request.url);
  const limit = boundedInteger(requestUrl.searchParams.get("limit"), 25, 1, MAX_PAGE);
  const budgetOffset = boundedInteger(
    requestUrl.searchParams.get("budget_offset"),
    0,
    0,
    MAX_OFFSET,
  );
  const scheduleOffset = boundedInteger(
    requestUrl.searchParams.get("schedule_offset"),
    0,
    0,
    MAX_OFFSET,
  );
  const campaignId = encodeURIComponent(id);
  const [campaign, eventMeta, budgets, schedules] = await Promise.all([
    readCoreJson(`/campaigns/${campaignId}`, request.signal),
    readCoreJson(
      `/campaigns/${campaignId}/events?limit=1&offset=0`,
      request.signal,
    ),
    readCoreJson(
      `/campaigns/${campaignId}/budgets?limit=${limit}&offset=${budgetOffset}`,
      request.signal,
    ),
    readCoreJson(
      `/campaigns/${campaignId}/schedules?limit=${limit}&offset=${scheduleOffset}`,
      request.signal,
    ),
  ]);

  for (const result of [campaign, eventMeta, budgets, schedules]) {
    if (!result.ok) return coreError(result);
  }

  const totalEvents = pageTotal(eventMeta.body);
  if (totalEvents === null) {
    return Response.json(
      { detail: "invalid_campaign_event_page" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }

  const eventOffset = Math.max(totalEvents - 100, 0);
  const events = await readCoreJson(
    `/campaigns/${campaignId}/events?limit=100&offset=${eventOffset}`,
    request.signal,
  );
  if (!events.ok) return coreError(events);

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
