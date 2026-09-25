import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const { id } = await context.params;
  return proxyCore(`/conversations/${encodeURIComponent(id)}`);
}
