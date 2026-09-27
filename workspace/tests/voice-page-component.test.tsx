import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Window } from "happy-dom";
import Home from "../app/page";

const windowInstance = new Window({ url: "http://localhost/" });

function installGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  });
}

installGlobal("window", windowInstance);
installGlobal("document", windowInstance.document);
installGlobal("navigator", windowInstance.navigator);
installGlobal("HTMLElement", windowInstance.HTMLElement);
installGlobal("Node", windowInstance.Node);
installGlobal("Event", windowInstance.Event);
installGlobal("KeyboardEvent", windowInstance.KeyboardEvent);
installGlobal("MouseEvent", windowInstance.MouseEvent);
installGlobal("DOMException", windowInstance.DOMException);
installGlobal(
  "requestAnimationFrame",
  windowInstance.requestAnimationFrame.bind(windowInstance),
);
installGlobal(
  "cancelAnimationFrame",
  windowInstance.cancelAnimationFrame.bind(windowInstance),
);
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function flushEffects(rounds = 10) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function mount(): { root: Root; container: HTMLElement } {
  windowInstance.localStorage.clear();
  const container = windowInstance.document.createElement(
    "div",
  ) as unknown as HTMLElement;
  windowInstance.document.body.appendChild(container as unknown as never);
  return { root: createRoot(container), container };
}

function baseFetch(counter?: { transcribe: number }) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url === "/api/voice/health") return json({ status: "healthy" });
    if (url === "/api/health") return json({ status: "healthy" });
    if (url.startsWith("/api/conversations?")) {
      return json({ items: [], limit: 100, offset: 0, total: 0 });
    }
    if (url === "/api/voice/transcribe") {
      if (counter) counter.transcribe += 1;
      return json({ text: "hello zelo", language: "en" });
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
}

test("release before microphone permission resolves never starts a recording", async () => {
  const runtime = globalThis as unknown as {
    fetch: typeof fetch;
    MediaRecorder: typeof MediaRecorder;
  };
  const originalFetch = runtime.fetch;
  const originalMediaRecorder = runtime.MediaRecorder;
  const originalMediaDevices = Object.getOwnPropertyDescriptor(
    windowInstance.navigator,
    "mediaDevices",
  );

  let resolveStream!: (value: MediaStream) => void;
  const streamPromise = new Promise<MediaStream>((resolve) => {
    resolveStream = resolve;
  });
  let trackStops = 0;
  let recorderConstructions = 0;
  const calls = { transcribe: 0 };

  class FakeRecorder {
    static isTypeSupported() {
      return true;
    }

    constructor() {
      recorderConstructions += 1;
    }
  }

  Object.defineProperty(windowInstance.navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: () => streamPromise,
    },
  });
  runtime.MediaRecorder = FakeRecorder as unknown as typeof MediaRecorder;
  runtime.fetch = baseFetch(calls) as typeof fetch;

  const { root, container } = mount();
  try {
    await act(async () => {
      root.render(<Home />);
    });
    await flushEffects();

    const button = container.querySelector<HTMLButtonElement>(".voice-button");
    assert.ok(button);
    assert.match(button.textContent ?? "", /Hold/);

    await act(async () => {
      button.dispatchEvent(
        new windowInstance.KeyboardEvent("keydown", {
          key: " ",
          bubbles: true,
        }),
      );
    });
    assert.match(button.textContent ?? "", /Starting/);

    await act(async () => {
      button.dispatchEvent(
        new windowInstance.KeyboardEvent("keyup", {
          key: " ",
          bubbles: true,
        }),
      );
      resolveStream({
        getTracks: () => [{ stop: () => { trackStops += 1; } }],
      } as unknown as MediaStream);
      await streamPromise;
    });
    await flushEffects();

    assert.equal(trackStops, 1);
    assert.equal(recorderConstructions, 0);
    assert.equal(calls.transcribe, 0);
    assert.match(button.textContent ?? "", /Hold/);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    runtime.fetch = originalFetch;
    runtime.MediaRecorder = originalMediaRecorder;
    if (originalMediaDevices) {
      Object.defineProperty(
        windowInstance.navigator,
        "mediaDevices",
        originalMediaDevices,
      );
    } else {
      delete (windowInstance.navigator as unknown as { mediaDevices?: unknown })
        .mediaDevices;
    }
  }
});

test("TTS failure after a voice turn never reposts the accepted Mind turn", async () => {
  const runtime = globalThis as unknown as {
    fetch: typeof fetch;
    MediaRecorder: typeof MediaRecorder;
  };
  const originalFetch = runtime.fetch;
  const originalMediaRecorder = runtime.MediaRecorder;
  const originalMediaDevices = Object.getOwnPropertyDescriptor(
    windowInstance.navigator,
    "mediaDevices",
  );

  let turnPosts = 0;
  let synthPosts = 0;
  let transcribePosts = 0;
  let trackStops = 0;

  class FakeRecorder {
    static isTypeSupported() {
      return true;
    }

    state: RecordingState = "inactive";
    mimeType = "audio/webm";
    private listeners = new Map<string, Array<(event: unknown) => void>>();

    addEventListener(type: string, listener: (event: unknown) => void) {
      const current = this.listeners.get(type) ?? [];
      current.push(listener);
      this.listeners.set(type, current);
    }

    start() {
      this.state = "recording";
    }

    stop() {
      if (this.state !== "recording") return;
      this.state = "inactive";
      for (const listener of this.listeners.get("dataavailable") ?? []) {
        listener({
          data: new Blob(["voice"], { type: "audio/webm" }),
        });
      }
      for (const listener of this.listeners.get("stop") ?? []) {
        listener(new Event("stop"));
      }
    }
  }

  Object.defineProperty(windowInstance.navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: async () =>
        ({
          getTracks: () => [{ stop: () => { trackStops += 1; } }],
        }) as unknown as MediaStream,
    },
  });
  runtime.MediaRecorder = FakeRecorder as unknown as typeof MediaRecorder;

  const turn = {
    id: "turn-1",
    number: 1,
    user_content: "hello zelo",
    assistant_content: "Hello back.",
    selected_alias: "local_general",
    selected_model: "test-model",
    usage: {},
    core_task: null,
    created_at: "2026-09-27T00:00:00Z",
  };
  const conversation = {
    id: "conversation-1",
    title: "Voice test",
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    turns: [turn],
    turn_limit: 50,
    turn_offset: 0,
    turn_total: 1,
  };

  runtime.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/voice/health") return json({ status: "healthy" });
    if (url === "/api/health") return json({ status: "healthy" });
    if (url.startsWith("/api/conversations?")) {
      return json({
        items: method === "GET" && turnPosts > 0
          ? [{
              id: conversation.id,
              title: conversation.title,
              created_at: conversation.created_at,
              updated_at: conversation.updated_at,
              turn_count: 1,
            }]
          : [],
        limit: 100,
        offset: 0,
        total: method === "GET" && turnPosts > 0 ? 1 : 0,
      });
    }
    if (url === "/api/voice/transcribe") {
      transcribePosts += 1;
      return json({ text: "hello zelo", language: "en" });
    }
    if (url === "/api/conversations" && method === "POST") {
      return json({ ...conversation, turns: [], turn_total: 0 });
    }
    if (url === "/api/conversations/conversation-1/turns" && method === "POST") {
      turnPosts += 1;
      return json(turn);
    }
    if (
      url ===
      "/api/conversations/conversation-1?turn_limit=1&turn_offset=0"
    ) {
      return json({ ...conversation, turns: [], turn_limit: 1 });
    }
    if (
      url ===
      "/api/conversations/conversation-1?turn_limit=50&turn_offset=0"
    ) {
      return json(conversation);
    }
    if (url === "/api/voice/synthesize" && method === "POST") {
      synthPosts += 1;
      return json({ detail: "tts_unavailable" }, 503);
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  }) as typeof fetch;

  const { root, container } = mount();
  try {
    await act(async () => {
      root.render(<Home />);
    });
    await flushEffects();

    const button = container.querySelector<HTMLButtonElement>(".voice-button");
    assert.ok(button);

    await act(async () => {
      button.dispatchEvent(
        new windowInstance.KeyboardEvent("keydown", {
          key: " ",
          bubbles: true,
        }),
      );
    });
    await flushEffects();
    assert.match(button.textContent ?? "", /Listening/);

    await act(async () => {
      button.dispatchEvent(
        new windowInstance.KeyboardEvent("keyup", {
          key: " ",
          bubbles: true,
        }),
      );
    });
    await flushEffects(16);

    assert.equal(transcribePosts, 1);
    assert.equal(turnPosts, 1);
    assert.equal(synthPosts, 1);
    assert.ok(trackStops >= 1);
    assert.match(container.textContent ?? "", /Hello back/);
    assert.match(container.textContent ?? "", /tts unavailable/);
  } finally {
    await act(async () => root.unmount());
    container.remove();
    runtime.fetch = originalFetch;
    runtime.MediaRecorder = originalMediaRecorder;
    if (originalMediaDevices) {
      Object.defineProperty(
        windowInstance.navigator,
        "mediaDevices",
        originalMediaDevices,
      );
    } else {
      delete (windowInstance.navigator as unknown as { mediaDevices?: unknown })
        .mediaDevices;
    }
  }
});


test("autoplay rejection exposes manual reply playback without reposting the turn", async () => {
  const runtime = globalThis as unknown as {
    fetch: typeof fetch;
    MediaRecorder: typeof MediaRecorder;
    Audio: typeof Audio;
    URL: typeof URL;
  };
  const originalFetch = runtime.fetch;
  const originalMediaRecorder = runtime.MediaRecorder;
  const originalAudio = runtime.Audio;
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;
  const originalMediaDevices = Object.getOwnPropertyDescriptor(
    windowInstance.navigator,
    "mediaDevices",
  );

  let turnPosts = 0;
  let synthPosts = 0;
  let playCalls = 0;
  let revoked = 0;

  class FakeRecorder {
    static isTypeSupported() {
      return true;
    }

    state: RecordingState = "inactive";
    mimeType = "audio/webm";
    private listeners = new Map<string, Array<(event: unknown) => void>>();

    addEventListener(type: string, listener: (event: unknown) => void) {
      const current = this.listeners.get(type) ?? [];
      current.push(listener);
      this.listeners.set(type, current);
    }

    start() {
      this.state = "recording";
    }

    stop() {
      if (this.state !== "recording") return;
      this.state = "inactive";
      for (const listener of this.listeners.get("dataavailable") ?? []) {
        listener({ data: new Blob(["voice"], { type: "audio/webm" }) });
      }
      for (const listener of this.listeners.get("stop") ?? []) {
        listener(new Event("stop"));
      }
    }
  }

  class FakeAudio {
    private listeners = new Map<string, Array<() => void>>();

    constructor(public src: string) {}

    addEventListener(type: string, listener: () => void) {
      const current = this.listeners.get(type) ?? [];
      current.push(listener);
      this.listeners.set(type, current);
    }

    pause() {}

    async play() {
      playCalls += 1;
      if (playCalls === 1) {
        throw new DOMException("Autoplay blocked", "NotAllowedError");
      }
      queueMicrotask(() => {
        for (const listener of this.listeners.get("ended") ?? []) listener();
      });
    }
  }

  Object.defineProperty(windowInstance.navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: async () =>
        ({
          getTracks: () => [{ stop: () => undefined }],
        }) as unknown as MediaStream,
    },
  });
  runtime.MediaRecorder = FakeRecorder as unknown as typeof MediaRecorder;
  runtime.Audio = FakeAudio as unknown as typeof Audio;
  URL.createObjectURL = () => "blob:zelo-reply";
  URL.revokeObjectURL = () => {
    revoked += 1;
  };

  const turn = {
    id: "turn-autoplay",
    number: 1,
    user_content: "voice prompt",
    assistant_content: "Spoken answer.",
    selected_alias: "local_general",
    selected_model: "test-model",
    usage: {},
    core_task: null,
    created_at: "2026-09-27T00:00:00Z",
  };
  const conversation = {
    id: "conversation-autoplay",
    title: "Autoplay test",
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    turns: [turn],
    turn_limit: 50,
    turn_offset: 0,
    turn_total: 1,
  };

  runtime.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/voice/health") return json({ status: "healthy" });
    if (url === "/api/health") return json({ status: "healthy" });
    if (url.startsWith("/api/conversations?")) {
      return json({ items: [], limit: 100, offset: 0, total: 0 });
    }
    if (url === "/api/voice/transcribe") {
      return json({ text: "voice prompt", language: "en" });
    }
    if (url === "/api/conversations" && method === "POST") {
      return json({ ...conversation, turns: [], turn_total: 0 });
    }
    if (
      url === "/api/conversations/conversation-autoplay/turns" &&
      method === "POST"
    ) {
      turnPosts += 1;
      return json(turn);
    }
    if (
      url ===
      "/api/conversations/conversation-autoplay?turn_limit=1&turn_offset=0"
    ) {
      return json({ ...conversation, turns: [], turn_limit: 1 });
    }
    if (
      url ===
      "/api/conversations/conversation-autoplay?turn_limit=50&turn_offset=0"
    ) {
      return json(conversation);
    }
    if (url === "/api/voice/synthesize" && method === "POST") {
      synthPosts += 1;
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "audio/wav" },
      });
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  }) as typeof fetch;

  const { root, container } = mount();
  try {
    await act(async () => {
      root.render(<Home />);
    });
    await flushEffects();

    const voiceButton =
      container.querySelector<HTMLButtonElement>(".voice-button");
    assert.ok(voiceButton);

    await act(async () => {
      voiceButton.dispatchEvent(
        new windowInstance.KeyboardEvent("keydown", {
          key: " ",
          bubbles: true,
        }),
      );
    });
    await flushEffects();

    await act(async () => {
      voiceButton.dispatchEvent(
        new windowInstance.KeyboardEvent("keyup", {
          key: " ",
          bubbles: true,
        }),
      );
    });
    await flushEffects(16);

    assert.equal(turnPosts, 1);
    assert.equal(synthPosts, 1);
    assert.equal(playCalls, 1);

    const manual =
      container.querySelector<HTMLButtonElement>(".voice-playback-button");
    assert.ok(manual);
    assert.match(manual.textContent ?? "", /Play reply/);

    await act(async () => {
      manual.click();
      await Promise.resolve();
    });
    await flushEffects();

    assert.equal(playCalls, 2);
    assert.equal(turnPosts, 1);
    assert.equal(synthPosts, 1);
    assert.ok(revoked >= 1);
    assert.equal(
      container.querySelector(".voice-playback-button"),
      null,
    );
  } finally {
    await act(async () => root.unmount());
    container.remove();
    runtime.fetch = originalFetch;
    runtime.MediaRecorder = originalMediaRecorder;
    runtime.Audio = originalAudio;
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    if (originalMediaDevices) {
      Object.defineProperty(
        windowInstance.navigator,
        "mediaDevices",
        originalMediaDevices,
      );
    } else {
      delete (windowInstance.navigator as unknown as { mediaDevices?: unknown })
        .mediaDevices;
    }
  }
});
