import { requireWorkspaceRequest } from "@/lib/core";
import { proxyVoice } from "@/lib/voice";

const MAX_AUDIO_BYTES = 12 * 1024 * 1024;
const ALLOWED_AUDIO_TYPES = new Set([
  "audio/webm",
  "audio/ogg",
  "audio/wav",
  "audio/x-wav",
  "audio/mpeg",
  "audio/mp4",
]);

async function readBoundedAudio(
  request: Request,
  maxBytes: number,
): Promise<ArrayBuffer> {
  if (!request.body) return new ArrayBuffer(0);

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
        await reader.cancel("voice_audio_too_large");
        throw new Error("voice_audio_too_large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const audio = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    audio.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return audio.buffer;
}

export async function POST(request: Request): Promise<Response> {
  const rejected = requireWorkspaceRequest(request);
  if (rejected) return rejected;

  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim();
  if (!contentType || !ALLOWED_AUDIO_TYPES.has(contentType)) {
    return Response.json(
      { detail: "unsupported_voice_audio_type" },
      { status: 415, headers: { "Cache-Control": "no-store" } },
    );
  }

  const declaredLength = request.headers.get("content-length");
  if (declaredLength) {
    const length = Number(declaredLength);
    if (
      !Number.isFinite(length) ||
      !Number.isInteger(length) ||
      length < 0 ||
      length > MAX_AUDIO_BYTES
    ) {
      return Response.json(
        { detail: "voice_audio_too_large" },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    }
  }

  let audio: ArrayBuffer;
  try {
    audio = await readBoundedAudio(request, MAX_AUDIO_BYTES);
  } catch (readError) {
    if (readError instanceof Error && readError.message === "voice_audio_too_large") {
      return Response.json(
        { detail: "voice_audio_too_large" },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    }
    return Response.json(
      { detail: "voice_audio_read_failed" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (audio.byteLength === 0) {
    return Response.json(
      { detail: "voice_audio_empty" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  return proxyVoice("/transcribe", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Audio-Type": contentType,
    },
    body: audio,
    signal: request.signal,
  });
}
