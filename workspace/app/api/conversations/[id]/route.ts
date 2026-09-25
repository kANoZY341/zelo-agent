import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

const MAX_TURN_PAGE = 100;

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
  const turnLimit = boundedInteger(
    requestUrl.searchParams.get("turn_limit"),
    50,
    1,
    MAX_TURN_PAGE,
  );
  const turnOffset = boundedInteger(
    requestUrl.searchParams.get("turn_offset"),
    0,
    0,
    1_000_000,
  );
  const query = new URLSearchParams({
    turn_limit: String(turnLimit),
    turn_offset: String(turnOffset),
  });

  return proxyCore(
    `/conversations/${encodeURIComponent(id)}?${query.toString()}`,
  );
}
