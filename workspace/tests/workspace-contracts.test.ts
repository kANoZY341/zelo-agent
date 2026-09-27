import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  createAbortDeadline,
  pageBackfillOffset,
  PendingRequestTracker,
  shouldShowDetailLoading,
} from "../lib/control-desk-state";

test("empty selection never renders stale campaign loading", () => {
  assert.equal(shouldShowDetailLoading(null, true, false, false), false);
  assert.equal(shouldShowDetailLoading("campaign-a", true, false, false), true);
  assert.equal(shouldShowDetailLoading("campaign-a", true, true, true), false);
});

test("empty final pages retreat by one bounded page", () => {
  assert.equal(pageBackfillOffset(0, 26, 25, 25), 0);
  assert.equal(pageBackfillOffset(0, 51, 50, 25), 25);
  assert.equal(pageBackfillOffset(1, 51, 50, 25), null);
  assert.equal(pageBackfillOffset(0, 0, 25, 25), null);
});

test("hierarchy pending accounting follows request settlement", () => {
  const pending = new PendingRequestTracker();
  assert.equal(pending.isIdle(), true);
  pending.begin();
  pending.begin();
  assert.equal(pending.count(), 2);
  assert.equal(pending.isIdle(), false);
  pending.settle();
  assert.equal(pending.count(), 1);
  pending.settle();
  assert.equal(pending.isIdle(), true);
  assert.throws(() => pending.settle(), /underflow/);
});

test("abort deadlines time out and can be cleared", async () => {
  const timed = new AbortController();
  const deadline = createAbortDeadline(timed, 5);
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(timed.signal.aborted, true);
  assert.equal(deadline.didTimeout(), true);

  const cleared = new AbortController();
  const clearedDeadline = createAbortDeadline(cleared, 10);
  clearedDeadline.clear();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(cleared.signal.aborted, false);
  assert.equal(clearedDeadline.didTimeout(), false);
});

test("proxyCore has no implicit Talk deadline and honors opt-in Desk deadlines", async () => {
  const { proxyCore } = await import("../lib/core");
  const runtimeGlobals = globalThis as unknown as {
    fetch: typeof fetch;
    setTimeout: (...args: unknown[]) => unknown;
  };
  const originalFetch = runtimeGlobals.fetch;
  const originalSetTimeout = runtimeGlobals.setTimeout;
  const previousUrl = process.env.ZELO_CORE_URL;
  const previousToken = process.env.ZELO_OPERATOR_TOKEN;

  process.env.ZELO_CORE_URL = "http://127.0.0.1:8000";
  process.env.ZELO_OPERATOR_TOKEN = "test-only-token";

  try {
    let scheduled = 0;
    runtimeGlobals.setTimeout = (...args: unknown[]) => {
      scheduled += 1;
      return originalSetTimeout(...args);
    };
    runtimeGlobals.fetch = async (_input, init) => {
      assert.equal(init?.signal?.aborted, false);
      return new Response('{"ok":true}', {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };

    const talkResponse = await proxyCore("/talk-like-read");
    assert.equal(talkResponse.status, 200);
    assert.equal(scheduled, 0);

    runtimeGlobals.setTimeout = originalSetTimeout;
    runtimeGlobals.fetch = (_input, init) =>
      new Promise<Response>((resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
        originalSetTimeout(
          () =>
            resolve(
              new Response('{"ok":true}', {
                status: 200,
                headers: { "content-type": "application/json" },
              }),
            ),
          30,
        );
      });

    const deskResponse = await proxyCore(
      "/desk-read",
      {},
      { timeoutMs: 5 },
    );
    assert.equal(deskResponse.status, 503);
  } finally {
    runtimeGlobals.fetch = originalFetch;
    runtimeGlobals.setTimeout = originalSetTimeout;
    if (previousUrl === undefined) delete process.env.ZELO_CORE_URL;
    else process.env.ZELO_CORE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_OPERATOR_TOKEN;
    else process.env.ZELO_OPERATOR_TOKEN = previousToken;
  }
});

test("Control Desk overview uses Core latest-events rather than a calculated tail offset", async () => {
  const overview = await readFile(
    new URL("../app/api/campaigns/[id]/overview/route.ts", import.meta.url),
    "utf8",
  );
  assert.match(overview, /events\/latest\?limit=100/);
  assert.doesNotMatch(overview, /totalEvents|eventOffset|events\?limit=1&offset=0/);
});

test("responsive navigation contracts remain present", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 560px\)/);
  assert.match(css, /\.topbar,\s*\.desk-topbar\s*\{[\s\S]*?flex-direction:\s*column/);
  assert.match(
    css,
    /\.mobile-project-controls\s*\{[\s\S]*?grid-template-columns:\s*34px minmax\(0, 1fr\) 34px 34px/,
  );
  assert.match(css, /\.desk-topbar \.mobile-view-button/);
});


test("proxyVoice keeps its token server-side and preserves binary responses", async () => {
  const { proxyVoice } = await import("../lib/voice");
  const runtimeGlobals = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtimeGlobals.fetch;
  const previousUrl = process.env.ZELO_VOICE_URL;
  const previousToken = process.env.ZELO_VOICE_TOKEN;

  process.env.ZELO_VOICE_URL = "http://127.0.0.1:8765";
  process.env.ZELO_VOICE_TOKEN =
    "server-only-voice-token-0123456789abcdef";

  try {
    runtimeGlobals.fetch = async (input, init) => {
      assert.equal(String(input), "http://127.0.0.1:8765/synthesize");
      const headers = new Headers(init?.headers);
      assert.equal(
        headers.get("authorization"),
        "Bearer server-only-voice-token-0123456789abcdef",
      );
      assert.equal(headers.get("x-attacker-auth"), "ignored-by-route");
      assert.equal(init?.signal?.aborted, false);
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: {
          "content-type": "audio/wav",
          "x-zelo-voice-language": "ar",
        },
      });
    };

    const response = await proxyVoice(
      "/synthesize",
      {
        headers: {
          Authorization: "Bearer attacker-controlled-token",
          "X-Attacker-Auth": "ignored-by-route",
        },
      },
      1000,
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "audio/wav");
    assert.equal(response.headers.get("x-zelo-voice-language"), "ar");
    assert.deepEqual(
      Array.from(new Uint8Array(await response.arrayBuffer())),
      [1, 2, 3],
    );
  } finally {
    runtimeGlobals.fetch = originalFetch;
    if (previousUrl === undefined) delete process.env.ZELO_VOICE_URL;
    else process.env.ZELO_VOICE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_VOICE_TOKEN;
    else process.env.ZELO_VOICE_TOKEN = previousToken;
  }
});

test("proxyVoice rejects non-loopback adapter URLs", async () => {
  const { proxyVoice } = await import("../lib/voice");
  const runtimeGlobals = globalThis as unknown as { fetch: typeof fetch };
  const originalFetch = runtimeGlobals.fetch;
  const previousUrl = process.env.ZELO_VOICE_URL;
  const previousToken = process.env.ZELO_VOICE_TOKEN;
  let fetched = false;

  process.env.ZELO_VOICE_URL = "https://example.com";
  process.env.ZELO_VOICE_TOKEN =
    "server-only-voice-token-0123456789abcdef";
  runtimeGlobals.fetch = async () => {
    fetched = true;
    throw new Error("must not fetch");
  };

  try {
    const response = await proxyVoice("/health", {}, 1000);
    assert.equal(response.status, 503);
    assert.equal(fetched, false);
  } finally {
    runtimeGlobals.fetch = originalFetch;
    if (previousUrl === undefined) delete process.env.ZELO_VOICE_URL;
    else process.env.ZELO_VOICE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.ZELO_VOICE_TOKEN;
    else process.env.ZELO_VOICE_TOKEN = previousToken;
  }
});

test("voice transcription is invalidated before conversation changes", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /voiceTranscriptionAbortRef/);
  assert.match(page, /voiceInputGenerationRef/);
  assert.match(page, /invalidateVoiceInput\(\);[\s\S]*?activeConversationIdRef\.current = id/);
  assert.match(page, /controller\.signal\.aborted[\s\S]*?voiceInputGenerationRef\.current !== transcriptionGeneration/);
  assert.match(page, /activeConversationIdRef\.current !== transcriptionConversationId/);
  assert.match(page, /onPointerCancel=\{cancelVoiceRecording\}/);
});

test("voice synthesis proxy bounds the stream before JSON buffering", async () => {
  const route = await readFile(
    new URL("../app/api/voice/synthesize/route.ts", import.meta.url),
    "utf8",
  );
  assert.match(route, /request\.body\.getReader\(\)/);
  assert.match(route, /total > maxBytes/);
  assert.match(route, /voice_synthesis_request_too_large/);
  assert.doesNotMatch(route, /request\.text\(\)/);
});
