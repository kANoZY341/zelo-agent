import assert from "node:assert/strict";
import test from "node:test";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { Window } from "happy-dom";
import { LiveConversation } from "../components/live-conversation/LiveConversation";
import { LiveConversationStore } from "../lib/live-conversation";

const browser = new Window({ url: "http://localhost/talk" });
for (const [name, value] of Object.entries({
  window: browser,
  document: browser.document,
  navigator: browser.navigator,
  HTMLElement: browser.HTMLElement,
  Node: browser.Node,
  Event: browser.Event,
  MouseEvent: browser.MouseEvent,
  IS_REACT_ACT_ENVIRONMENT: true,
})) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  });
}
const json = (body: unknown) => new Response(JSON.stringify(body));
const injection = '<img src=x onerror="alert(1)">';

test("live component sends text, escapes Core content, exposes actual task state and disables unsupported controls", async () => {
  let sent = false;
  const calls: string[] = [];
  const conversation = () => ({
    id: "a",
    title: "Core conversation",
    turn_total: sent ? 1 : 0,
    turn_offset: 0,
    turns: sent
      ? [
          {
            id: "t",
            number: 1,
            user_content: "Hello",
            assistant_content: injection,
            selected_alias: "default",
            selected_model: "actual-model",
            core_task: {
              id: "task",
              kind: "work",
              status: "awaiting_approval",
              attempt_count: 1,
              result: { summary: injection },
            },
          },
        ]
      : [],
  });
  const store = new LiveConversationStore({
    fetch: async (input, init) => {
      const url = String(input);
      calls.push(url);
      if (url === "/api/health") return json({ status: "healthy" });
      if (url.includes("?limit="))
        return json({ items: [], total: 0, offset: 0, limit: 50 });
      if (url.endsWith("/turns") && init?.method === "POST") {
        sent = true;
        return json(conversation().turns[0]);
      }
      return json(conversation());
    },
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(<LiveConversation store={store} />);
    });
    const voice = container.querySelector<HTMLButtonElement>(
      '[aria-label="Voice unavailable in text beta"]',
    )!;
    const attachment = container.querySelector<HTMLButtonElement>(
      '[aria-label="Attachments unavailable in text beta"]',
    )!;
    assert.equal(voice.disabled, true);
    assert.equal(attachment.disabled, true);
    assert.ok(
      container
        .querySelector(`#${voice.getAttribute("aria-describedby")}`)
        ?.textContent?.includes("unavailable"),
    );
    assert.equal(container.querySelector('input[type="file"]'), null);
    const before = calls.length;
    await act(async () => {
      voice.click();
      attachment.click();
    });
    assert.equal(calls.length, before);
    await act(async () => {
      store.setDraft("Hello");
    });
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      textarea.dispatchEvent(
        new browser.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
          shiftKey: true,
        }) as unknown as Event,
      );
      textarea.dispatchEvent(
        new browser.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
          isComposing: true,
        }) as unknown as Event,
      );
    });
    assert.equal(sent, false);
    await act(async () => {
      textarea.dispatchEvent(
        new browser.KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
          cancelable: true,
        }) as unknown as Event,
      );
    });
    await act(async () => {
      container.querySelector("form")!.dispatchEvent(
        new browser.Event("submit", {
          bubbles: true,
          cancelable: true,
        }) as unknown as Event,
      );
    });
    assert.equal(sent, true);
    assert.equal(calls.filter((url) => url.endsWith("/turns")).length, 1);
    assert.match(container.textContent!, /actual-model/);
    assert.ok(container.textContent!.includes(injection));
    assert.equal(container.querySelector("img"), null);
    assert.match(container.textContent!, /awaiting approval/);
    await act(async () => {
      container.querySelector<HTMLButtonElement>(".z2-result-card")!.click();
    });
    assert.match(
      container.querySelector("dialog")!.textContent!,
      /Approval actions are unavailable/,
    );
    assert.equal(
      container.querySelector<HTMLButtonElement>(
        'button[aria-describedby="approval-unavailable"]',
      )!.disabled,
      true,
    );
    assert.ok(
      container
        .querySelector("pre")!
        .textContent!.includes(injection.replaceAll('"', '\\"')),
    );
    assert.equal(container.querySelector("pre img"), null);
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[aria-label="Close panel"]')!
        .click();
      container.querySelector<HTMLButtonElement>(".z2-header-button")!.click();
    });
    const search = container.querySelector<HTMLInputElement>(
      'input[type="search"]',
    )!;
    search.focus();
    const escape = new browser.KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    await act(async () => {
      search.dispatchEvent(escape as unknown as Event);
    });
    assert.equal(
      escape.defaultPrevented,
      true,
      "Escape must close the panel rather than only clear search",
    );
    assert.equal(
      container.querySelector<HTMLDialogElement>("dialog")!.open,
      false,
    );
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
