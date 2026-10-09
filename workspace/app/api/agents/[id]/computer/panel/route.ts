import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

const CANONICAL_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;
  const { id } = await context.params;
  if (!CANONICAL_ID.test(id)) {
    return Response.json({ detail: "invalid_agent_instance_id" }, { status: 422 });
  }
  // Server-side bearer is injected only by Zelo's existing loopback proxy.
  // Never forward client-supplied authorization or mutate computer permissions.
  return proxyCore(
    `/agents/${id}/computer/panel`,
    { method: "GET", signal: request.signal },
    { timeoutMs: 20000 },
  );
}
