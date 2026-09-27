import { requireWorkspaceRequest } from "@/lib/core";
import { proxyVoice } from "@/lib/voice";

const MAX_AUDIO_BYTES = 20_000_000;

export async function POST(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const mediaType = request.headers.get("content-type") ?? "";
  if (!mediaType.toLowerCase().startsWith("audio/")) {
    return Response.json({ detail: "audio_content_type_required" }, { status: 415 });
  }
  const audio = await request.arrayBuffer();
  if (audio.byteLength === 0) {
    return Response.json({ detail: "audio_required" }, { status: 400 });
  }
  if (audio.byteLength > MAX_AUDIO_BYTES) {
    return Response.json({ detail: "audio_too_large" }, { status: 413 });
  }
  return proxyVoice("/transcribe", {
    method: "POST",
    headers: { "Content-Type": mediaType },
    body: audio,
    signal: request.signal,
  });
}
