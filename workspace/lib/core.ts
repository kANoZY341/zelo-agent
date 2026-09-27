const DEFAULT_CORE_URL = "http://127.0.0.1:8000";

export type ProxyCoreOptions = {
  timeoutMs?: number;
};

function coreBaseUrl(): URL {
  const configured = process.env.ZELO_CORE_URL ?? DEFAULT_CORE_URL;
  const url = new URL(configured);
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "localhost";

  if (url.protocol !== "http:" || !loopback || url.username || url.password) {
    throw new Error("ZELO_CORE_URL must be loopback HTTP without credentials");
  }

  return url;
}

function operatorToken(): string {
  const token = process.env.ZELO_OPERATOR_TOKEN;
  if (!token) {
    throw new Error("ZELO_OPERATOR_TOKEN is required by the workspace server");
  }
  return token;
}

export function requireWorkspaceRequest(request: Request): Response | null {
  if (request.headers.get("x-zelo-workspace") !== "1") {
    return Response.json({ detail: "workspace_request_required" }, { status: 403 });
  }
  return null;
}

export async function proxyCore(
  path: string,
  init: RequestInit = {},
  options: ProxyCoreOptions = {},
): Promise<Response> {
  if (
    options.timeoutMs !== undefined &&
    (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0)
  ) {
    throw new Error("proxyCore timeoutMs must be a positive finite number");
  }

  const controller = new AbortController();
  const upstreamSignal = init.signal;
  const abortFromUpstream = () => controller.abort();
  const timeout =
    options.timeoutMs === undefined
      ? null
      : setTimeout(() => controller.abort(), options.timeoutMs);

  if (upstreamSignal?.aborted) {
    controller.abort();
  } else {
    upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });
  }

  try {
    const url = new URL(path, coreBaseUrl());
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${operatorToken()}`);
    headers.set("Accept", "application/json");

    const response = await fetch(url, {
      ...init,
      headers,
      signal: controller.signal,
      cache: "no-store",
    });
    const body = await response.text();

    return new Response(body, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json({ detail: "core_proxy_unavailable" }, { status: 503 });
  } finally {
    if (timeout) clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}
