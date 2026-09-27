import { requireWorkspaceRequest } from "@/lib/core";
import { proxyVoice } from "@/lib/voice";

const MAX_BODY_CHARS = 10000;

export async function POST(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const body = await request.text();
  if (!body || body.length > MAX_BODY_CHARS) {
    return Response.json(
      { detail: body ? "voice_request_too_large" : "voice_request_required" },
      { status: body ? 413 : 400 },
    );
  }
  return proxyVoice("/synthesize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    signal: request.signal,
  });
}
