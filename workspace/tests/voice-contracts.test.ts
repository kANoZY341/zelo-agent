import assert from "node:assert/strict";
import test from "node:test";
import { proxyVoice } from "../lib/voice";

test("voice proxy is loopback-only and preserves local audio responses", async () => {
  const runtime = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtime.fetch;
  const previousUrl = process.env.ZELO_VOICE_URL;
  const previousToken = process.env.ZELO_VOICE_SERVICE_TOKEN;
  process.env.ZELO_VOICE_URL = "http://127.0.0.1:8765";
  process.env.ZELO_VOICE_SERVICE_TOKEN = "workspace-voice-test-token-" + "x".repeat(32);

  try {
    let requested = "";
    runtime.fetch = async (input, init) => {
      requested = String(input);
      assert.equal(init?.cache, "no-store");
      assert.ok(init?.signal);
      const headers = new Headers(init?.headers);
      assert.equal(
        headers.get("authorization"),
        `Bearer ${process.env.ZELO_VOICE_SERVICE_TOKEN}`,
      );
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "audio/wav" },
      });
    };

    const response = await proxyVoice("/synthesize", {
      method: "POST",
      headers: { Authorization: "Bearer browser-controlled" },
    });
    assert.equal(requested, "http://127.0.0.1:8765/synthesize");
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "audio/wav");
    assert.deepEqual(
      Array.from(new Uint8Array(await response.arrayBuffer())),
      [1, 2, 3],
    );
  } finally {
    runtime.fetch = originalFetch;
    if (previousUrl === undefined) delete process.env.ZELO_VOICE_URL;
    else process.env.ZELO_VOICE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_VOICE_SERVICE_TOKEN;
    else process.env.ZELO_VOICE_SERVICE_TOKEN = previousToken;
  }
});

test("voice proxy refuses non-loopback configuration without making a request", async () => {
  const runtime = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtime.fetch;
  const previousUrl = process.env.ZELO_VOICE_URL;
  const previousToken = process.env.ZELO_VOICE_SERVICE_TOKEN;
  process.env.ZELO_VOICE_URL = "https://example.com";
  process.env.ZELO_VOICE_SERVICE_TOKEN = "workspace-voice-test-token-" + "x".repeat(32);

  try {
    let called = false;
    runtime.fetch = async () => {
      called = true;
      throw new Error("must not be called");
    };
    const response = await proxyVoice("/health");
    assert.equal(called, false);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { detail: "voice_service_unavailable" });
  } finally {
    runtime.fetch = originalFetch;
    if (previousUrl === undefined) delete process.env.ZELO_VOICE_URL;
    else process.env.ZELO_VOICE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_VOICE_SERVICE_TOKEN;
    else process.env.ZELO_VOICE_SERVICE_TOKEN = previousToken;
  }
});

test("voice routes retain Workspace request gate and bounded inputs", async () => {
  const transcribe = await import("../app/api/voice/transcribe/route");
  const synthesize = await import("../app/api/voice/synthesize/route");

  const missingGate = new Request("http://localhost/api/voice/transcribe", {
    method: "POST",
    headers: { "content-type": "audio/webm" },
    body: new Uint8Array([1]),
  });
  assert.equal((await transcribe.POST(missingGate)).status, 403);

  const wrongType = new Request("http://localhost/api/voice/transcribe", {
    method: "POST",
    headers: {
      "x-zelo-workspace": "1",
      "content-type": "application/octet-stream",
    },
    body: new Uint8Array([1]),
  });
  assert.equal((await transcribe.POST(wrongType)).status, 415);

  const emptySynthesis = new Request("http://localhost/api/voice/synthesize", {
    method: "POST",
    headers: { "x-zelo-workspace": "1" },
    body: "",
  });
  assert.equal((await synthesize.POST(emptySynthesis)).status, 400);
});
