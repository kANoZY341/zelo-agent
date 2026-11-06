import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { Window } from "happy-dom";

const browser = new Window({ url: "http://localhost/" });

function installGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  });
}

installGlobal("window", browser);
installGlobal("document", browser.document);
installGlobal("navigator", browser.navigator);
installGlobal("HTMLElement", browser.HTMLElement);
installGlobal("Node", browser.Node);
installGlobal("Event", browser.Event);
installGlobal("MouseEvent", browser.MouseEvent);
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);
installGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
  setTimeout(() => callback(0), 0),
);
installGlobal("cancelAnimationFrame", clearTimeout);

class FakeRecorder {
  static isTypeSupported() {
    return false;
  }

  mimeType = "audio/webm";
  state = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(_stream: MediaStream) {}

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({
      data: new Blob(["sample"], { type: "audio/webm" }),
    });
    this.onstop?.();
  }
}

class FakeAudio {
  static instances: FakeAudio[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(_url: string) {
    FakeAudio.instances.push(this);
  }

  pause() {}

  async play() {}
}

installGlobal("MediaRecorder", FakeRecorder);
installGlobal("Audio", FakeAudio);
Object.defineProperty(browser.navigator, "mediaDevices", {
  configurable: true,
  value: {
    getUserMedia: async () => ({
      getTracks: () => [{ stop: () => undefined }],
    }),
  },
});

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

async function flush(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function pressVoice(button: HTMLButtonElement) {
  Object.defineProperty(button, "setPointerCapture", {
    configurable: true,
    value: () => undefined,
  });
  const pointer = (type: string) => {
    const event = new browser.Event(type, { bubbles: true });
    Object.defineProperty(event, "pointerId", { value: 1 });
    button.dispatchEvent(event as unknown as Event);
  };
  await act(async () => pointer("pointerdown"));
  await flush();
  assert.match(button.textContent ?? "", /Release/);
  await act(async () => pointer("pointerup"));
  await flush();
}

test("stale playback callbacks cannot affect the newer spoken reply", async () => {
  const { default: Home } = await import("../app/page");
  const originalFetch = globalThis.fetch;
  const container = browser.document.createElement("div") as unknown as HTMLElement;
  browser.document.body.appendChild(container as unknown as never);
  browser.localStorage.clear();

  const turns: Array<Record<string, unknown>> = [];
  const synthesis: Array<{
    signal: AbortSignal;
    reject: (reason: unknown) => void;
    resolve: (response: Response) => void;
  }> = [];

  globalThis.fetch = (input, init) => {
    const url = String(input);
    if (url === "/api/health") return Promise.resolve(json({ status: "healthy" }));
    if (url.startsWith("/api/conversations?")) {
      return Promise.resolve(json({ items: [], total: 0 }));
    }
    if (url === "/api/conversations" && init?.method === "POST") {
      return Promise.resolve(json({ id: "conversation-1", title: "Voice" }));
    }
    if (url.startsWith("/api/conversations/conversation-1?")) {
      return Promise.resolve(
        json({
          id: "conversation-1",
          title: "Voice",
          turn_total: turns.length,
          turns,
        }),
      );
    }
    if (url === "/api/conversations/conversation-1/turns") {
      const number = turns.length + 1;
      const turn = {
        id: `turn-${number}`,
        user_content: "hello",
        assistant_content: `reply ${number}`,
        core_task: null,
      };
      turns.push(turn);
      return Promise.resolve(json(turn));
    }
    if (url === "/api/voice/transcribe") {
      return Promise.resolve(json({ text: "hello" }));
    }
    if (url === "/api/voice/synthesize") {
      return new Promise<Response>((resolve, reject) => {
        synthesis.push({ signal: init?.signal as AbortSignal, reject, resolve });
      });
    }
    throw new Error(`unexpected request: ${url}`);
  };

  const root = createRoot(container);
  try {
    await act(async () => root.render(<Home />));
    await flush();
    const button = container.querySelector<HTMLButtonElement>(".voice-button");
    assert.ok(button);

    await pressVoice(button);
    assert.equal(synthesis.length, 1);

    await pressVoice(button);
    assert.equal(synthesis.length, 2);
    assert.equal(synthesis[0].signal.aborted, true);
    assert.equal(synthesis[1].signal.aborted, false);

    await act(async () => {
      synthesis[0].reject(new DOMException("Aborted", "AbortError"));
    });
    await flush();
    assert.equal(
      synthesis[1].signal.aborted,
      false,
      "late cancellation of the first reply must leave the newer reply active",
    );

    await act(async () => {
      synthesis[1].resolve(
        new Response(new Blob(["RIFF"], { type: "audio/wav" }), {
          status: 200,
          headers: { "content-type": "audio/wav" },
        }),
      );
    });
    await flush();
    assert.equal(FakeAudio.instances.length, 1);

    await pressVoice(button);
    assert.equal(synthesis.length, 3);
    assert.equal(Boolean(container.querySelector(".error-banner")), false);

    act(() => FakeAudio.instances[0].onerror?.());
    await flush();
    assert.equal(
      Boolean(container.querySelector(".error-banner")),
      false,
      "an old audio error must not surface during a newer reply",
    );
    assert.equal(synthesis[2].signal.aborted, false);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    globalThis.fetch = originalFetch;
  }
});
