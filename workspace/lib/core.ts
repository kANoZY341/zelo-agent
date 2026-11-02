import { timingSafeEqual } from "node:crypto";

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

export function requireWorkspaceAccess(request: Request): Response | null {
  const secret = process.env.ZELO_WORKSPACE_ACCESS_TOKEN;
  if (
    !secret ||
    secret.length < 32 ||
    secret.length > 256 ||
    secret === process.env.ZELO_OPERATOR_TOKEN ||
    secret === process.env.ZELO_VOICE_TOKEN ||
    /[\x00-\x1f\x7f]/.test(secret)
  ) {
    return Response.json(
      { detail: "workspace_access_unconfigured" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  const expected = Buffer.from(
    `Basic ${Buffer.from(`zelo:${secret}`).toString("base64")}`,
  );
  const supplied = Buffer.from(request.headers.get("authorization") ?? "");
  if (
    supplied.length !== expected.length ||
    !timingSafeEqual(supplied, expected)
  ) {
    return Response.json(
      { detail: "workspace_access_required" },
      {
        status: 401,
        headers: {
          "WWW-Authenticate": 'Basic realm="Zelo Workspace", charset="UTF-8"',
          "Cache-Control": "no-store",
        },
      },
    );
  }
  return null;
}

export function requireWorkspaceRequest(request: Request): Response | null {
  const rejected = requireWorkspaceAccess(request);
  if (rejected) return rejected;
  if (request.headers.get("x-zelo-workspace") !== "1") {
    return Response.json({ detail: "workspace_request_required" }, { status: 403 });
  }
  return null;
}

export const MAX_WORKSPACE_CONVERSATION_BODY_BYTES = 128 * 1024;

export async function readBoundedWorkspaceBody(
  request: Request,
  maxBytes: number,
): Promise<string | Response> {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) {
    throw new Error("maxBytes must be a positive safe integer");
  }
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    if (
      !/^(0|[1-9][0-9]*)$/.test(declared) ||
      !Number.isSafeInteger(Number(declared))
    ) {
      return Response.json({ detail: "workspace_body_invalid" }, { status: 400 });
    }
    if (Number(declared) > maxBytes) {
      return Response.json({ detail: "workspace_body_too_large" }, { status: 413 });
    }
  }
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
        try {
          await reader.cancel("workspace_body_too_large");
        } catch {
          // The size refusal is authoritative even if the client disconnects.
        }
        return Response.json({ detail: "workspace_body_too_large" }, { status: 413 });
      }
      chunks.push(value);
    }
  } catch {
    return Response.json({ detail: "workspace_body_invalid" }, { status: 400 });
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
