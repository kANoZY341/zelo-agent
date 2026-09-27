import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { proxyCore } from "../lib/core.ts";

const originalFetch = globalThis.fetch;
const originalToken = process.env.ZELO_OPERATOR_TOKEN;
const originalCoreUrl = process.env.ZELO_CORE_URL;

beforeEach(() => {
  process.env.ZELO_OPERATOR_TOKEN = "x".repeat(32);
  process.env.ZELO_CORE_URL = "http://127.0.0.1:8000";
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalToken === undefined) delete process.env.ZELO_OPERATOR_TOKEN;
  else process.env.ZELO_OPERATOR_TOKEN = originalToken;
  if (originalCoreUrl === undefined) delete process.env.ZELO_CORE_URL;
  else process.env.ZELO_CORE_URL = originalCoreUrl;
});

test("proxyCore has no implicit Desk timeout", async () => {
  globalThis.fetch = async (_input, init) => {
    assert.equal(init?.signal?.aborted, false);
    return new Response('{"ok":true}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const response = await proxyCore("/health");
  assert.equal(response.status, 200);
});

test("proxyCore explicit timeout aborts the underlying Core fetch", async () => {
  let observedSignal;
  globalThis.fetch = async (_input, init) => {
    observedSignal = init?.signal;
    return new Promise((_resolve, reject) => {
      observedSignal?.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    });
  };

  const response = await proxyCore("/projects", {}, { timeoutMs: 5 });
  assert.equal(response.status, 503);
  assert.equal(observedSignal?.aborted, true);
});

test("proxyCore propagates caller cancellation without a timeout", async () => {
  const caller = new AbortController();
  let observedSignal;
  globalThis.fetch = async (_input, init) => {
    observedSignal = init?.signal;
    return new Promise((_resolve, reject) => {
      observedSignal?.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    });
  };

  const pending = proxyCore("/projects", { signal: caller.signal });
  caller.abort();
  const response = await pending;

  assert.equal(response.status, 503);
  assert.equal(observedSignal?.aborted, true);
});

test("proxyCore rejects invalid timeout values", async () => {
  await assert.rejects(
    () => proxyCore("/projects", {}, { timeoutMs: 0 }),
    /positive finite number/,
  );
});
