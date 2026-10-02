import type { Conversation, ConversationPage, ConversationTurn } from "./types";

export const ACTIVE_ID = "zelo.workspace.activeConversationId";
export const ACTIVE_TASKS = new Set([
  "pending",
  "running",
  "awaiting_approval",
]);
const PAGE = 50;
const HEADERS = { "X-Zelo-Workspace": "1" };
type Submission = { key: string; body: string; text: string };
export type LiveState = {
  id: string | null;
  conversation: Conversation | null;
  draft: string;
  busy: boolean;
  loading: boolean;
  error: string | null;
  pollError: string | null;
  pending: Submission | null;
  createUncertain: boolean;
  history: ConversationPage;
  historyLoading: boolean;
  historyError: string | null;
  connection: "checking" | "online" | "offline";
};
type Storage = Pick<globalThis.Storage, "getItem" | "setItem" | "removeItem">;
type Options = {
  fetch?: typeof fetch;
  storage?: () => Storage;
  pollMs?: number;
};

/** An ephemeral projection of Core. Only the selected ID is persisted. */
export class LiveConversationStore {
  private state: LiveState = {
    id: null,
    conversation: null,
    draft: "",
    busy: false,
    loading: false,
    error: null,
    pollError: null,
    pending: null,
    createUncertain: false,
    history: { items: [], total: 0, offset: 0, limit: PAGE },
    historyLoading: false,
    historyError: null,
    connection: "checking",
  };
  private listeners = new Set<() => void>();
  private controllers = new Set<AbortController>();
  private epoch = 0;
  private readVersion = 0;
  private historyVersion = 0;
  private disposed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  constructor(private options: Options = {}) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(patch: Partial<LiveState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  private remember(id: string | null) {
    try {
      const storage = this.options.storage?.();
      if (id) storage?.setItem(ACTIVE_ID, id);
      else storage?.removeItem(ACTIVE_ID);
    } catch {
      /* Storage is optional, never authoritative. */
    }
  }
  private async request<T>(url: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    this.controllers.add(controller);
    const deadline = setTimeout(
      () => controller.abort(),
      init.method === "POST" ? 120000 : 30000,
    );
    try {
      const response = await (this.options.fetch ?? fetch)(url, {
        ...init,
        cache: "no-store",
        signal: controller.signal,
        headers: { ...HEADERS, ...init.headers },
      });
      if (!response.ok)
        throw new Error(`Core returned HTTP ${response.status}.`);
      return (await response.json()) as T;
    } finally {
      clearTimeout(deadline);
      this.controllers.delete(controller);
    }
  }
  private current(epoch: number) {
    return !this.disposed && epoch === this.epoch;
  }
  private resetRequests() {
    ++this.epoch;
    ++this.readVersion;
    ++this.historyVersion;
    this.update({ historyLoading: false, historyError: null });
    clearTimeout(this.timer);
    this.controllers.forEach((controller) => controller.abort());
  }
  async start() {
    this.disposed = false;
    ++this.historyVersion;
    this.update({ historyLoading: false });
    const epoch = this.epoch;
    let saved: string | null = null;
    try {
      saved = this.options.storage?.().getItem(ACTIVE_ID) ?? null;
    } catch {
      /* optional */
    }
    if (saved && /^[a-zA-Z0-9_-]{1,128}$/.test(saved) && this.current(epoch)) {
      const opening = this.open(saved);
      void this.loadHistory();
      await opening;
    } else {
      void this.health();
      void this.loadHistory();
    }
  }
  dispose() {
    this.disposed = true;
    this.resetRequests();
  }
  async health() {
    const epoch = this.epoch;
    this.update({ connection: "checking" });
    try {
      const health = await this.request<{ status: string }>("/api/health");
      if (this.current(epoch))
        this.update({
          connection: health.status === "healthy" ? "online" : "offline",
        });
    } catch {
      if (this.current(epoch)) this.update({ connection: "offline" });
    }
  }
  setDraft(draft: string) {
    this.update({ draft });
  }
  async loadHistory(more = false) {
    if (this.state.historyLoading) return;
    const version = ++this.historyVersion;
    this.update({ historyLoading: true, historyError: null });
    const offset = more ? this.state.history.offset + PAGE : 0;
    try {
      const history = await this.request<ConversationPage>(
        `/api/conversations?limit=${PAGE}&offset=${offset}`,
      );
      if (!this.disposed && version === this.historyVersion)
        this.update({ history });
    } catch {
      if (!this.disposed && version === this.historyVersion)
        this.update({ historyError: "Could not load history. Try again." });
    } finally {
      if (version === this.historyVersion)
        this.update({ historyLoading: false });
    }
  }
  newChat() {
    if (this.state.busy || this.state.pending) return;
    this.resetRequests();
    this.remember(null);
    this.update({
      id: null,
      conversation: null,
      draft: "",
      error: null,
      pollError: null,
      loading: false,
      createUncertain: false,
    });
    void this.health();
  }
  async open(id: string) {
    if (this.state.busy || this.state.pending) return;
    this.resetRequests();
    this.remember(id);
    this.update({
      id,
      conversation: null,
      draft: this.state.createUncertain ? this.state.draft : "",
      error: null,
      pollError: null,
      createUncertain: false,
    });
    void this.health();
    await this.loadTurns();
  }
  async loadTurns(offset?: number, polling = false) {
    const id = this.state.id;
    if (!id || this.state.busy) return;
    const epoch = this.epoch;
    const version = ++this.readVersion;
    clearTimeout(this.timer);
    if (!polling) this.update({ loading: true, error: null });
    const valid = () => this.current(epoch) && version === this.readVersion;
    try {
      const base = `/api/conversations/${encodeURIComponent(id)}`;
      if (offset === undefined) {
        const metadata = await this.request<Conversation>(
          `${base}?turn_limit=1&turn_offset=0`,
        );
        if (!valid()) return;
        offset = Math.max(0, metadata.turn_total - PAGE);
      }
      const conversation = await this.request<Conversation>(
        `${base}?turn_limit=${PAGE}&turn_offset=${offset}`,
      );
      if (!valid()) return;
      this.update({ conversation, pollError: null, connection: "online" });
    } catch {
      if (valid())
        this.update(
          polling
            ? {
                pollError:
                  "Task updates unavailable. Retrying; displayed status may be out of date.",
              }
            : {
                error:
                  "Could not load this conversation. Retry loading or choose another from history.",
              },
        );
    } finally {
      if (valid()) {
        this.update({ loading: false });
        this.schedulePoll();
      }
    }
  }
  private schedulePoll() {
    clearTimeout(this.timer);
    if (
      this.disposed ||
      !this.state.conversation?.turns.some(
        (turn) => turn.core_task && ACTIVE_TASKS.has(turn.core_task.status),
      )
    )
      return;
    const epoch = this.epoch;
    this.timer = setTimeout(() => {
      if (this.current(epoch))
        void this.loadTurns(this.state.conversation?.turn_offset, true);
    }, this.options.pollMs ?? 1500);
  }
  async send() {
    const s = this.state;
    if (s.busy || s.loading || s.createUncertain || (s.id && !s.conversation))
      return;
    const text = s.draft.trim();
    if (!text || text.length > 16384) return;
    if (s.pending && s.pending.text !== text) {
      this.update({
        error:
          "Delivery is uncertain. Restore and retry the original message before sending different text.",
      });
      return;
    }
    const submission = s.pending ?? {
      text,
      key: crypto.randomUUID(),
      body: JSON.stringify({ text }),
    };
    const epoch = this.epoch;
    ++this.readVersion;
    clearTimeout(this.timer);
    this.update({ busy: true, pending: submission, error: null });
    let creating = !s.id;
    let accepted = false;
    try {
      let id = s.id;
      if (!id) {
        const conversation = await this.request<Conversation>(
          "/api/conversations",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          },
        );
        if (!this.current(epoch)) return;
        id = conversation.id;
        creating = false;
        this.remember(id);
        this.update({ id, conversation });
      }
      const turn = await this.request<ConversationTurn>(
        `/api/conversations/${encodeURIComponent(id)}/turns`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": submission.key,
          },
          body: submission.body,
        },
      );
      if (!this.current(epoch)) return;
      const conversation = this.state.conversation!;
      const recent =
        conversation.turn_offset + conversation.turns.length >=
        conversation.turn_total
          ? conversation.turns
          : [];
      const turns = [...recent.filter((item) => item.id !== turn.id), turn]
        .sort((a, b) => a.number - b.number)
        .slice(-PAGE);
      this.update({
        conversation: {
          ...conversation,
          turns,
          turn_total: Math.max(conversation.turn_total, turn.number),
          turn_offset: Math.max(0, turn.number - turns.length),
        },
        draft: "",
        pending: null,
        connection: "online",
      });
      accepted = true;
      void this.loadHistory();
    } catch {
      if (!this.current(epoch)) return;
      this.update(
        creating
          ? {
              pending: null,
              createUncertain: true,
              error:
                "Conversation creation could not be confirmed. Your text is preserved. Check history and reopen the created conversation, or explicitly start a new chat. No automatic create retry was made.",
            }
          : {
              error:
                "Reply delivery could not be confirmed. Your text is preserved. Retry the original message safely; Core may still be working.",
            },
      );
      void this.health();
    } finally {
      if (this.current(epoch)) {
        this.update({ busy: false });
        if (accepted) await this.loadTurns();
        else this.schedulePoll();
      }
    }
  }
  retry() {
    if (this.state.pending) this.update({ draft: this.state.pending.text });
    return this.send();
  }
}
