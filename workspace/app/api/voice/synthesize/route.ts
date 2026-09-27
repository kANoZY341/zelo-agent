import { requireWorkspaceRequest } from "@/lib/core";
import { proxyVoice } from "@/lib/voice";

const MAX_SYNTHESIS_REQUEST_BYTES = 16 * 1024;

async function readBoundedText(
  request: Request,
  maxBytes: number,
): Promise<string> {
  if (!request.body) return "";

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel("voice_synthesis_request_too_large");
        throw new Error("voice_synthesis_request_too_large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(body);
}

export async function POST(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const declaredLength = request.headers.get("content-length");
  if (declaredLength) {
    const length = Number(declaredLength);
    if (
      !Number.isFinite(length) ||
      !Number.isInteger(length) ||
      length < 0
    ) {
      return Response.json(
        { detail: "voice_synthesis_request_invalid" },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (length > MAX_SYNTHESIS_REQUEST_BYTES) {
      return Response.json(
        { detail: "voice_synthesis_request_too_large" },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    }
  }

  let body: string;
  try {
    body = await readBoundedText(request, MAX_SYNTHESIS_REQUEST_BYTES);
  } catch (readError) {
    if (
      readError instanceof Error &&
      readError.message === "voice_synthesis_request_too_large"
    ) {
      return Response.json(
        { detail: "voice_synthesis_request_too_large" },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    }
    return Response.json(
      { detail: "voice_synthesis_request_invalid" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  if (!body) {
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
