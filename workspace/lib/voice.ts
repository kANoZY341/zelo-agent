const DEFAULT_VOICE_URL = "http://127.0.0.1:8765";
const DEFAULT_VOICE_TIMEOUT_MS = 120000;

function voiceBaseUrl(): URL {
  const configured = process.env.ZELO_VOICE_URL ?? DEFAULT_VOICE_URL;
  const url = new URL(configured);
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "localhost";

  if (
    url.protocol !== "http:" ||
    !loopback ||
    url.username ||
    url.password ||
    (url.pathname !== "/" && url.pathname !== "")
  ) {
    throw new Error(
      "ZELO_VOICE_URL must be loopback HTTP without credentials or a path",
    );
  }
  return url;
}

function voiceToken(): string {
  const token = process.env.ZELO_VOICE_TOKEN;
  if (!token) {
    throw new Error("ZELO_VOICE_TOKEN is required by the workspace server");
  }
  return token;
}

export async function proxyVoice(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_VOICE_TIMEOUT_MS,
): Promise<Response> {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("voice proxy path must be absolute-path relative");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error("voice proxy timeout must be a positive finite number");
  }

  const controller = new AbortController();
  const upstreamSignal = init.signal;
  const abortFromUpstream = () => controller.abort();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  if (upstreamSignal?.aborted) controller.abort();
  else upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });

  try {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${voiceToken()}`);

    const response = await fetch(new URL(path, voiceBaseUrl()), {
      ...init,
      headers,
      signal: controller.signal,
      cache: "no-store",
    });
    const body = await response.arrayBuffer();
    const responseHeaders = new Headers({
      "Content-Type":
        response.headers.get("content-type") ?? "application/octet-stream",
      "Cache-Control": "no-store",
    });
    const language = response.headers.get("x-zelo-voice-language");
    if (language) responseHeaders.set("X-Zelo-Voice-Language", language);

    return new Response(body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { detail: "voice_adapter_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  } finally {
    clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}
