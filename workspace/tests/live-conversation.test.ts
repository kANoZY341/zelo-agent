import assert from "node:assert/strict";
import test from "node:test";
import { ACTIVE_ID, LiveConversationStore } from "../lib/live-conversation";
import type {
  Conversation,
  ConversationTurn,
  TaskProjection,
} from "../lib/types";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
const turn = (task: TaskProjection | null = null): ConversationTurn => ({
  id: "turn-1",
  number: 1,
  user_content: "Hello",
  assistant_content: "Real Core reply",
  selected_alias: "default",
  selected_model: "core-model",
  usage: {},
  core_task: task,
  created_at: "2026-10-02",
});
const conversation = (
  id = "a",
  turns: ConversationTurn[] = [],
): Conversation => ({
  id,
  title: id,
  turns,
  turn_total: turns.length,
  turn_offset: 0,
  turn_limit: 50,
  created_at: "2026-10-02",
  updated_at: "2026-10-02",
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
function storage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}
function harness(
  handler?: (url: string, init?: RequestInit) => Promise<Response> | undefined,
  saved = storage(),
) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const store = new LiveConversationStore({
    storage: () => saved,
    fetch: async (input, init) => {
      const url = String(input);
      calls.push({ url, init });
      assert.equal(
        (init?.headers as Record<string, string>)["X-Zelo-Workspace"],
        "1",
      );
      const handled = handler?.(url, init);
      if (handled) return handled;
      if (url === "/api/health") return json({ status: "healthy" });
      if (url.includes("?limit="))
        return json({ items: [], total: 0, limit: 50, offset: 0 });
      if (init?.method === "POST" && url.endsWith("/turns"))
        return json(turn());
      return json(conversation(url.includes("/b?") ? "b" : "a"));
    },
  });
  return { store, calls, saved };
}

test("real send uses proxy contracts, preserves model fields, persists only ID and prevents duplicate creation/send", async () => {
  const reply = deferred<Response>();
  let posted = false;
  const { store, calls, saved } = harness((url, init) => {
    if (url.endsWith("/turns")) {
      posted = true;
      return reply.promise;
    }
    if (posted && init?.method !== "POST" && url.includes("/a?"))
      return Promise.resolve(json(conversation("a", [turn()])));
  });
  try {
    store.setDraft("Hello");
    const first = store.send();
    await store.send();
    await Promise.resolve();
    await Promise.resolve();
    reply.resolve(json(turn()));
    await first;
    assert.equal(
      calls.filter(
        (call) =>
          call.init?.method === "POST" && call.url === "/api/conversations",
      ).length,
      1,
    );
    assert.equal(calls.filter((call) => call.url.endsWith("/turns")).length, 1);
    assert.equal(
      store.getSnapshot().conversation?.turns[0].assistant_content,
      "Real Core reply",
    );
    assert.equal(
      store.getSnapshot().conversation?.turns[0].selected_model,
      "core-model",
    );
    assert.equal(store.getSnapshot().draft, "");
    assert.deepEqual([...saved.data], [[ACTIVE_ID, "a"]]);
  } finally {
    store.dispose();
  }
});

test("ambiguous turn failure preserves input and exact retry identity; edited content never reuses the key", async () => {
  let attempts = 0;
  const { store, calls } = harness((url) => {
    if (url.endsWith("/turns")) {
      ++attempts;
      return attempts === 1
        ? Promise.reject(new TypeError("offline"))
        : Promise.resolve(json(turn()));
    }
  });
  try {
    store.setDraft("Hello");
    await store.send();
    assert.equal(store.getSnapshot().draft, "Hello");
    assert.match(store.getSnapshot().error!, /could not be confirmed/);
    store.setDraft("Changed");
    await store.send();
    assert.equal(attempts, 1);
    await store.open("b");
    assert.equal(store.getSnapshot().id, "a");
    await store.retry();
    const posts = calls.filter((call) => call.url.endsWith("/turns"));
    assert.equal(posts.length, 2);
    assert.equal(posts[0].init?.body, posts[1].init?.body);
    assert.equal(
      (posts[0].init?.headers as Record<string, string>)["Idempotency-Key"],
      (posts[1].init?.headers as Record<string, string>)["Idempotency-Key"],
    );
    assert.equal(store.getSnapshot().pending, null);
  } finally {
    store.dispose();
  }
});

test("offline create failure is truthful, preserves draft and never retries non-idempotent creation automatically", async () => {
  const { store, calls } = harness((url, init) =>
    init?.method === "POST"
      ? Promise.reject(new TypeError("offline"))
      : undefined,
  );
  try {
    store.setDraft("Hello");
    await store.send();
    await store.send();
    assert.equal(store.getSnapshot().createUncertain, true);
    assert.equal(store.getSnapshot().draft, "Hello");
    assert.equal(
      calls.filter((call) => call.init?.method === "POST").length,
      1,
    );
    await store.open("a");
    assert.equal(store.getSnapshot().draft, "Hello");
  } finally {
    store.dispose();
  }
});

test("refresh restores an ID independently of history page; blocked and corrupt storage are tolerated", async () => {
  const saved = storage();
  saved.setItem(ACTIVE_ID, "older-thread");
  const first = harness(undefined, saved);
  try {
    await first.store.start();
    assert.equal(first.store.getSnapshot().id, "older-thread");
    assert.ok(first.calls.some((c) => c.url.includes("/older-thread?")));
  } finally {
    first.store.dispose();
  }
  saved.setItem(ACTIVE_ID, '{"corrupt":true}');
  const corrupt = harness(undefined, saved);
  try {
    await corrupt.store.start();
    assert.equal(corrupt.store.getSnapshot().id, null);
  } finally {
    corrupt.store.dispose();
  }
  const blocked = new LiveConversationStore({
    storage: () => {
      throw new Error("blocked");
    },
    fetch: async () => json({ status: "healthy", items: [] }),
  });
  try {
    await blocked.start();
    blocked.newChat();
    assert.equal(blocked.getSnapshot().id, null);
  } finally {
    blocked.dispose();
  }
});

test("late history load from A cannot overwrite B even if the transport ignores abort", async () => {
  const late = deferred<Response>();
  let signal: AbortSignal | null | undefined;
  const { store } = harness((url, init) => {
    if (url.includes("/a?")) {
      signal = init?.signal;
      return late.promise;
    }
  });
  try {
    const a = store.open("a");
    await store.open("b");
    assert.equal(signal?.aborted, true);
    late.resolve(json(conversation("a", [turn()])));
    await a;
    assert.equal(store.getSnapshot().id, "b");
    assert.equal(store.getSnapshot().conversation?.id, "b");
    assert.equal(store.getSnapshot().loading, false);
  } finally {
    store.dispose();
  }
});

test("turn pagination is bounded and exposes earlier pages", async () => {
  const { store, calls } = harness((url) =>
    url.includes("/a?")
      ? Promise.resolve(json({ ...conversation(), turn_total: 175 }))
      : undefined,
  );
  try {
    await store.open("a");
    assert.ok(
      calls.some((c) => c.url.endsWith("turn_limit=50&turn_offset=125")),
    );
    await store.loadTurns(75);
    assert.ok(
      calls.some((c) => c.url.endsWith("turn_limit=50&turn_offset=75")),
    );
  } finally {
    store.dispose();
  }
});

test("reload resumes task polling, updates real results, and disposal clears timers and aborts requests", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reads = 0;
  const task = {
    id: "task",
    kind: "work",
    status: "running",
    result: null,
    attempt_count: 1,
  } as TaskProjection;
  const saved = storage();
  saved.setItem(ACTIVE_ID, "a");
  const { store, calls } = harness((url) => {
    if (url.includes("/a?")) {
      ++reads;
      return Promise.resolve(
        json(
          conversation("a", [
            turn(
              reads > 2
                ? {
                    ...task,
                    status: "succeeded",
                    result: { summary: "Actual result" },
                  }
                : task,
            ),
          ]),
        ),
      );
    }
  }, saved);
  try {
    await store.start();
    assert.equal(
      store.getSnapshot().conversation?.turns[0].core_task?.status,
      "running",
    );
    t.mock.timers.tick(1500);
    for (let i = 0; i < 15; i++) await Promise.resolve();
    assert.equal(
      store.getSnapshot().conversation?.turns[0].core_task?.status,
      "succeeded",
    );
    assert.deepEqual(
      store.getSnapshot().conversation?.turns[0].core_task?.result,
      { summary: "Actual result" },
    );
    const count = calls.length;
    store.dispose();
    t.mock.timers.tick(120000);
    assert.equal(calls.length, count);
  } finally {
    store.dispose();
    t.mock.timers.reset();
  }
});

test("late task poll cannot inject a result into another thread", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const late = deferred<Response>();
  let reads = 0;
  const task = { id: "task", status: "awaiting_approval" } as TaskProjection;
  const { store } = harness((url) => {
    if (url.includes("/a?"))
      return ++reads > 2
        ? late.promise
        : Promise.resolve(json(conversation("a", [turn(task)])));
  });
  try {
    await store.open("a");
    t.mock.timers.tick(1500);
    await store.open("b");
    late.resolve(
      json(conversation("a", [turn({ ...task, status: "succeeded" })])),
    );
    for (let i = 0; i < 15; i++) await Promise.resolve();
    assert.equal(store.getSnapshot().conversation?.id, "b");
    assert.deepEqual(store.getSnapshot().conversation?.turns, []);
  } finally {
    store.dispose();
    t.mock.timers.reset();
  }
});

test("unmount aborts a pending send and ignores its late reply without claiming cancellation", async () => {
  const late = deferred<Response>();
  let signal: AbortSignal | null | undefined;
  const { store } = harness((url, init) => {
    if (url.endsWith("/turns")) {
      signal = init?.signal;
      return late.promise;
    }
  });
  await store.open("a");
  store.setDraft("Hello");
  const sending = store.send();
  const snapshot = store.getSnapshot();
  store.dispose();
  assert.equal(signal?.aborted, true);
  late.resolve(json(turn()));
  await sending;
  assert.equal(store.getSnapshot(), snapshot);
  assert.equal(store.getSnapshot().conversation?.turns.length, 0);
});

test("history Load More uses bounded pages instead of retaining an unbounded copy", async () => {
  const { store } = harness((url) => {
    if (url.includes("?limit=")) {
      const offset = Number(
        new URL(url, "http://localhost").searchParams.get("offset"),
      );
      return Promise.resolve(
        json({
          items: Array.from({ length: 50 }, (_, i) => ({
            id: String(offset + i),
            title: "Saved conversation",
            turn_count: 0,
          })),
          total: 150,
          offset,
          limit: 50,
        }),
      );
    }
  });
  try {
    await store.loadHistory();
    await store.loadHistory(true);
    assert.equal(store.getSnapshot().history.offset, 50);
    assert.equal(store.getSnapshot().history.items.length, 50);
    assert.equal(store.getSnapshot().history.items[0].id, "50");
  } finally {
    store.dispose();
  }
});

test("failed task polling retains last projection and retries until Core responds", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reads = 0;
  const task = { id: "task", status: "pending" } as TaskProjection;
  const { store } = harness((url) => {
    if (url.includes("/a?")) {
      if (++reads === 3) return Promise.reject(new TypeError("offline"));
      return Promise.resolve(
        json(
          conversation("a", [
            turn(reads > 3 ? { ...task, status: "succeeded" } : task),
          ]),
        ),
      );
    }
  });
  try {
    await store.open("a");
    t.mock.timers.tick(1500);
    for (let i = 0; i < 15; i++) await Promise.resolve();
    assert.match(store.getSnapshot().pollError!, /out of date/);
    assert.equal(
      store.getSnapshot().conversation?.turns[0].core_task?.status,
      "pending",
    );
    t.mock.timers.tick(1500);
    for (let i = 0; i < 15; i++) await Promise.resolve();
    assert.equal(store.getSnapshot().pollError, null);
    assert.equal(
      store.getSnapshot().conversation?.turns[0].core_task?.status,
      "succeeded",
    );
  } finally {
    store.dispose();
    t.mock.timers.reset();
  }
});

test("aborted history requests cannot block or overwrite a fresh history page", async () => {
  const stale = deferred<Response>();
  let reads = 0;
  const page = (id: string) => ({
    items: [{ id, title: id, turn_count: 0 }],
    total: 1,
    offset: 0,
    limit: 50,
  });
  const { store } = harness((url) => {
    if (url.includes("?limit="))
      return ++reads === 1
        ? stale.promise
        : Promise.resolve(json(page("fresh")));
  });
  const first = store.loadHistory();
  try {
    store.newChat();
    await store.loadHistory();
    assert.equal(
      reads,
      2,
      "Navigation must release an aborted history request's loading state",
    );
    stale.resolve(json(page("obsolete")));
    await first;
    assert.equal(store.getSnapshot().history.items[0].id, "fresh");
    assert.equal(store.getSnapshot().historyLoading, false);
    assert.equal(store.getSnapshot().historyError, null);
  } finally {
    stale.resolve(json(page("obsolete")));
    await first;
    store.dispose();
  }
});
