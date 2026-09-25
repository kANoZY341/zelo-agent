"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  Conversation,
  ConversationTurn,
  TaskProjection,
} from "@/lib/types";

const ACTIVE_TASK_STATES = new Set(["pending", "awaiting_approval", "running"]);
const WORKSPACE_HEADERS = { "X-Zelo-Workspace": "1" };

type UiMessage = {
  id: string;
  role: "user" | "zelo";
  content: string;
  task?: TaskProjection | null;
};

function friendlyTaskError(error: string): string {
  const knownErrors: Record<string, string> = {
    inspection_repository_dirty:
      "I can’t start self-improvement while the Zelo repository has uncommitted changes. Commit, stash, or remove them, then retry.",
  };
  return knownErrors[error] ?? error.replaceAll("_", " ");
}

function taskResultSummary(task: TaskProjection): string | null {
  if (!task.result) return null;
  const finalResponse = task.result.final_response;
  if (typeof finalResponse === "string") return finalResponse;
  const summary = task.result.summary;
  if (typeof summary === "string") return summary;
  return JSON.stringify(task.result, null, 2);
}

function toMessages(turns: ConversationTurn[]): UiMessage[] {
  return turns.flatMap((turn) => [
    {
      id: `${turn.id}:user`,
      role: "user" as const,
      content: turn.user_content,
    },
    {
      id: `${turn.id}:zelo`,
      role: "zelo" as const,
      content: turn.assistant_content,
      task: turn.core_task,
    },
  ]);
}

async function parseJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body as T;
}

function TaskCard({ task }: { task: TaskProjection }) {
  const result = taskResultSummary(task);
  const active = ACTIVE_TASK_STATES.has(task.status);

  return (
    <div className="task-card">
      <div className="task-card-head">
        <span className={`status-dot ${active ? "pulse" : ""}`} />
        <strong>{task.kind.replaceAll("_", " ")}</strong>
        <span className="task-status">{task.status.replaceAll("_", " ")}</span>
      </div>
      <div className="task-meta">
        <span>
          {task.attempt_count} attempt{task.attempt_count === 1 ? "" : "s"}
        </span>
        {task.selected_model ? <span>{task.selected_model}</span> : null}
        {task.approval_count ? (
          <span>{task.approval_count} approval event(s)</span>
        ) : null}
      </div>
      {task.error ? (
        <div className="task-error">{friendlyTaskError(task.error)}</div>
      ) : null}
      {result ? <div className="task-result">{result}</div> : null}
      {task.result_truncated ? (
        <div className="task-note">Projected result is bounded by Core.</div>
      ) : null}
    </div>
  );
}

export default function Home() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [coreHealthy, setCoreHealthy] = useState<boolean | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSubmissionRef = useRef<{ prompt: string; key: string } | null>(
    null,
  );

  const latestTask = useMemo(() => {
    const tasks = messages
      .map((message) => message.task)
      .filter((task): task is TaskProjection => Boolean(task));
    return (
      [...tasks].reverse().find((task) => ACTIVE_TASK_STATES.has(task.status)) ??
      tasks.at(-1) ??
      null
    );
  }, [messages]);

  const refreshConversation = useCallback(
    async (id: string): Promise<Conversation> => {
      const encodedId = encodeURIComponent(id);
      const metadataResponse = await fetch(
        `/api/conversations/${encodedId}?turn_limit=1&turn_offset=0`,
        {
          headers: WORKSPACE_HEADERS,
          cache: "no-store",
        },
      );
      const metadata = await parseJson<Conversation>(metadataResponse);
      const turnLimit = 50;
      const turnOffset = Math.max(metadata.turn_total - turnLimit, 0);

      const response = await fetch(
        `/api/conversations/${encodedId}?turn_limit=${turnLimit}&turn_offset=${turnOffset}`,
        {
          headers: WORKSPACE_HEADERS,
          cache: "no-store",
        },
      );
      const conversation = await parseJson<Conversation>(response);
      setMessages(toMessages(conversation.turns));
      return conversation;
    },
    [],
  );

  const pollUntilTaskSettles = useCallback(
    async (id: string) => {
      if (pollRef.current) clearTimeout(pollRef.current);

      const tick = async () => {
        try {
          const conversation = await refreshConversation(id);
          const hasActiveTask = conversation.turns.some(
            (turn) =>
              turn.core_task && ACTIVE_TASK_STATES.has(turn.core_task.status),
          );
          if (hasActiveTask) {
            pollRef.current = setTimeout(tick, 1200);
          }
        } catch (pollError) {
          setError(
            pollError instanceof Error
              ? pollError.message
              : "conversation_refresh_failed",
          );
        }
      };

      pollRef.current = setTimeout(tick, 800);
    },
    [refreshConversation],
  );

  useEffect(() => {
    let cancelled = false;

    fetch("/api/health", {
      headers: WORKSPACE_HEADERS,
      cache: "no-store",
    })
      .then((response) => parseJson<{ status: string }>(response))
      .then((health) => {
        if (!cancelled) setCoreHealthy(health.status === "healthy");
      })
      .catch(() => {
        if (!cancelled) setCoreHealthy(false);
      });

    return () => {
      cancelled = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);

  async function ensureConversation(): Promise<string> {
    if (conversationId) return conversationId;

    const response = await fetch("/api/conversations", {
      method: "POST",
      headers: {
        ...WORKSPACE_HEADERS,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title: "Talk to Zelo" }),
    });
    const conversation = await parseJson<Conversation>(response);
    setConversationId(conversation.id);
    return conversation.id;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const prompt = text.trim();
    if (!prompt || busy) return;

    const previousSubmission = pendingSubmissionRef.current;
    if (previousSubmission && previousSubmission.prompt !== prompt) {
      setError(
        "The previous turn has an uncertain delivery state. Retry that exact message before sending a different one.",
      );
      setText(previousSubmission.prompt);
      return;
    }

    const submission =
      previousSubmission ?? { prompt, key: crypto.randomUUID() };
    pendingSubmissionRef.current = submission;

    setBusy(true);
    setPendingPrompt(prompt);
    setError(null);
    setText("");

    let activeConversationId: string | null = null;

    const postTurn = async (id: string): Promise<ConversationTurn> => {
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(id)}/turns`,
        {
          method: "POST",
          headers: {
            ...WORKSPACE_HEADERS,
            "Content-Type": "application/json",
            "Idempotency-Key": submission.key,
          },
          body: JSON.stringify({ text: prompt }),
        },
      );

      if (!response.ok && response.status >= 400 && response.status < 500) {
        pendingSubmissionRef.current = null;
      }

      return parseJson<ConversationTurn>(response);
    };

    const acceptTurn = async (id: string, turn: ConversationTurn) => {
      pendingSubmissionRef.current = null;
      setPendingPrompt(null);

      const incoming = toMessages([turn]);
      const incomingIds = new Set(incoming.map((message) => message.id));
      setMessages((current) => [
        ...current.filter((message) => !incomingIds.has(message.id)),
        ...incoming,
      ]);

      void refreshConversation(id).catch((refreshError) => {
        setError(
          refreshError instanceof Error
            ? refreshError.message
            : "conversation_refresh_failed",
        );
      });

      if (turn.core_task && ACTIVE_TASK_STATES.has(turn.core_task.status)) {
        await pollUntilTaskSettles(id);
      }
    };

    try {
      const id = await ensureConversation();
      activeConversationId = id;
      const turn = await postTurn(id);
      await acceptTurn(id, turn);
    } catch (submitError) {
      const retryable =
        activeConversationId !== null &&
        pendingSubmissionRef.current?.key === submission.key;

      if (retryable && activeConversationId) {
        try {
          const recoveredTurn = await postTurn(activeConversationId);
          await acceptTurn(activeConversationId, recoveredTurn);
          setError(null);
          return;
        } catch (retryError) {
          setPendingPrompt(null);
          setError(
            retryError instanceof Error
              ? retryError.message
              : "turn_delivery_uncertain",
          );
          setText(prompt);
          return;
        }
      }

      setPendingPrompt(null);
      setError(
        submitError instanceof Error ? submitError.message : "turn_failed",
      );
      setText(prompt);
    } finally {
      setBusy(false);
    }
  }

  function newConversation() {
    if (pollRef.current) clearTimeout(pollRef.current);
    pendingSubmissionRef.current = null;
    setConversationId(null);
    setMessages([]);
    setPendingPrompt(null);
    setError(null);
    setText("");
  }

  return (
    <main className="workspace-shell">
      <aside className="rail">
        <div className="brand-mark">Z</div>
        <button className="rail-item active" title="Talk to Zelo">
          Talk
        </button>
        <button className="rail-item" title="Company World" disabled>
          World
        </button>
        <button className="rail-item" title="Control Desk" disabled>
          Desk
        </button>
        <div className="rail-spacer" />
        <span className="local-badge">LOCAL</span>
      </aside>

      <section className="conversation-panel">
        <header className="topbar">
          <div>
            <p className="eyebrow">ZELO / MIND</p>
            <h1>Talk to Zelo</h1>
          </div>
          <div className="topbar-actions">
            <span className="core-state">
              <span
                className={`status-dot ${coreHealthy === false ? "offline" : ""}`}
              />
              {coreHealthy === null
                ? "Checking Core"
                : coreHealthy
                  ? "Core connected"
                  : "Core unavailable"}
            </span>
            <button className="secondary-button" onClick={newConversation}>
              New conversation
            </button>
          </div>
        </header>

        <div className="conversation-body">
          {messages.length === 0 && !pendingPrompt ? (
            <div className="hero">
              <div className="hero-orb">Z</div>
              <p className="eyebrow">PERSISTENT PERSONAL AI</p>
              <h2>What are we working on?</h2>
              <p>
                Talk normally. When real work is needed, Zelo asks Core to execute
                it through the same trusted task, approval, worker and audit
                boundaries.
              </p>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <article
                  key={message.id}
                  className={`message ${message.role}`}
                >
                  <div className="message-label">
                    {message.role === "zelo" ? "ZELO" : "YOU"}
                  </div>
                  <div className="message-content">{message.content}</div>
                  {message.task ? <TaskCard task={message.task} /> : null}
                </article>
              ))}
              {pendingPrompt ? (
                <>
                  <article className="message user pending-message">
                    <div className="message-label">YOU</div>
                    <div className="message-content">{pendingPrompt}</div>
                  </article>
                  <article className="message zelo pending-message">
                    <div className="message-label">ZELO</div>
                    <div className="thinking-line">
                      Thinking
                      <span className="thinking-dots" aria-hidden="true">
                        <span />
                        <span />
                        <span />
                      </span>
                    </div>
                  </article>
                </>
              ) : null}
            </div>
          )}
        </div>

        <form className="composer" onSubmit={submit}>
          {error ? <div className="error-banner">{error}</div> : null}
          <div className="composer-box">
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="Tell Zelo what you need..."
              rows={3}
              disabled={busy}
            />
            <div className="composer-footer">
              <span>
                {conversationId
                  ? `Conversation ${conversationId.slice(0, 8)}`
                  : "New conversation"}
              </span>
              <button
                className="send-button"
                disabled={busy || !text.trim()}
              >
                {busy ? "Thinking..." : "Send"}
              </button>
            </div>
          </div>
        </form>
      </section>

      <aside className="inspector">
        <p className="eyebrow">CORE / LIVE STATE</p>
        <h2>Execution</h2>
        {latestTask ? (
          <TaskCard task={latestTask} />
        ) : (
          <div className="empty-card">
            No Core task yet. Zelo can answer directly until execution is actually
            needed.
          </div>
        )}
        <div className="boundary-card">
          <strong>Authority boundary</strong>
          <p>Mind reasons. Core authorizes. Workers work. Tools act.</p>
          <p>
            The workspace only projects Core state; it never invents task state.
          </p>
        </div>
      </aside>
    </main>
  );
}
