const DEFAULT_VOICE_URL = "http://127.0.0.1:8765";
const VOICE_TIMEOUT_MS = 120000;

function voiceBaseUrl(): URL {
  const configured = process.env.ZELO_VOICE_URL ?? DEFAULT_VOICE_URL;
  const url = new URL(configured);
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  if (url.protocol !== "http:" || !loopback || url.username || url.password) {
    throw new Error("ZELO_VOICE_URL must be loopback HTTP without credentials");
  }
  return url;
}

export async function proxyVoice(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const controller = new AbortController();
  const upstreamSignal = init.signal;
  const abortFromUpstream = () => controller.abort();
  const timeout = setTimeout(() => controller.abort(), VOICE_TIMEOUT_MS);

  if (upstreamSignal?.aborted) controller.abort();
  else upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });

  try {
    const response = await fetch(new URL(path, voiceBaseUrl()), {
      ...init,
      signal: controller.signal,
      cache: "no-store",
    });
    const body = await response.arrayBuffer();
    return new Response(body, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/octet-stream",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "voice_service_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  } finally {
    clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}
