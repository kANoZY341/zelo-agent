import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

export async function GET(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;
  return proxyCore("/health");
}
