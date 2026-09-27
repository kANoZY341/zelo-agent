import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

const MAX_PAGE = 100;

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

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const { id } = await context.params;
  const requestUrl = new URL(request.url);
  const limit = boundedInteger(requestUrl.searchParams.get("limit"), 50, 1, MAX_PAGE);
  const offset = boundedInteger(
    requestUrl.searchParams.get("offset"),
    0,
    0,
    1_000_000,
  );
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });

  return proxyCore(
    `/goals/${encodeURIComponent(id)}/campaigns?${query.toString()}`,
    { signal: request.signal },
  );
}
