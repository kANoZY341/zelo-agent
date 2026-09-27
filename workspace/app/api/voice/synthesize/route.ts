import { requireWorkspaceRequest } from "@/lib/core";
import { proxyVoice } from "@/lib/voice";

const MAX_SYNTHESIS_REQUEST_BYTES = 16 * 1024;

export async function POST(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const body = await request.text();
  if (!body || new TextEncoder().encode(body).byteLength > MAX_SYNTHESIS_REQUEST_BYTES) {
    return Response.json(
      { detail: "voice_synthesis_request_invalid" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return Response.json(
      { detail: "voice_synthesis_request_invalid" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    !("text" in parsed) ||
    typeof parsed.text !== "string" ||
    parsed.text.trim().length === 0 ||
    parsed.text.length > 4000
  ) {
    return Response.json(
      { detail: "voice_synthesis_request_invalid" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  return proxyVoice("/synthesize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    signal: request.signal,
  });
}
