import { proxyCore, requireWorkspaceRequest } from "@/lib/core";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const idempotencyKey = request.headers.get("idempotency-key");
  if (!idempotencyKey) {
    return Response.json({ detail: "idempotency_key_required" }, { status: 400 });
  }

  const { id } = await context.params;
  const body = await request.text();
  return proxyCore(`/conversations/${encodeURIComponent(id)}/turns`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body,
  });
}
