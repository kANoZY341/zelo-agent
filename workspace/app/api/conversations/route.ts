import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

export async function POST(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const body = await request.text();
  return proxyCore("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body || "{}",
  });
}
