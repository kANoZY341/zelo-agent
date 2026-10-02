"use client";

import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Glyph } from "../design-exploration/shared";
import { LiveConversationStore } from "../../lib/live-conversation";
import type { TaskProjection } from "../../lib/types";

function Mark({ large = false }: { large?: boolean }) {
  return (
    <span
      className={`z2-mark${large ? " z2-mark-large" : ""}`}
      aria-hidden="true"
    >
      <svg viewBox="0 0 40 40" fill="none" focusable="false">
        <path
          d="M9 8H32V13L18 27H32L28 32H8V27L22 13H5L9 8Z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}
const bounded = (text: string, limit = 16384) =>
  text.length > limit ? `${text.slice(0, limit)}\n[Display shortened]` : text;
function TaskResult({ task }: { task: TaskProjection }) {
  return (
    <>
      <p>Status: {task.status.replaceAll("_", " ")}</p>
      <p>
        {task.attempt_count} attempts ·{" "}
        {task.selected_model ?? "No model reported"}
      </p>
      {task.cancel_requested && (
        <p>
          Core reports cancellation requested. The task may still be running.
        </p>
      )}
      {task.error && <p role="alert">{bounded(task.error)}</p>}
      {task.result && (
        <pre className="live-result">
          {bounded(JSON.stringify(task.result, null, 2), 24000)}
        </pre>
      )}
      {task.result_truncated && <p>Core has truncated this result.</p>}
      {task.status === "awaiting_approval" && (
        <>
          <p>
            Awaiting approval in Core. Approval actions are unavailable in this
            text beta.
          </p>
          <button disabled aria-describedby="approval-unavailable">
            Approve
          </button>
          <span id="approval-unavailable">
            Use an authorized Core approval surface to review this request.
          </span>
        </>
      )}
    </>
  );
}

export function LiveConversation({
  store: supplied,
}: {
  store?: LiveConversationStore;
}) {
  const [store] = useState(
    () =>
      supplied ??
      new LiveConversationStore({ storage: () => window.localStorage }),
  );
  const s = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
  const [panel, setPanel] = useState<"history" | string | null>(null);
  const [historySearch, setHistorySearch] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const compose = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const stick = useRef(true);
  const [composerHeight, setComposerHeight] = useState(164);
  const turns = s.conversation?.turns ?? [];
  const visibleHistory = s.history.items.filter((item) =>
    item.title.toLowerCase().includes(historySearch.trim().toLowerCase()),
  );
  const task = turns.find((turn) => turn.core_task?.id === panel)?.core_task;
  const empty = !s.id && !turns.length;
  const locked = s.busy || !!s.pending;
  useEffect(() => {
    void store.start();
    return () => store.dispose();
  }, [store]);
  useLayoutEffect(() => {
    if (input.current) {
      input.current.style.height = "auto";
      input.current.style.height = `${Math.min(input.current.scrollHeight, 160)}px`;
    }
    if (compose.current) setComposerHeight(compose.current.offsetHeight + 55);
  }, [s.draft, s.error, s.busy, s.pending, s.pollError]);
  useEffect(() => {
    if (!compose.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (compose.current) setComposerHeight(compose.current.offsetHeight + 55);
    });
    observer.observe(compose.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (stick.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [s.conversation, s.busy, composerHeight]);
  useEffect(() => {
    if (panel) dialog.current?.showModal();
    else dialog.current?.close();
  }, [panel]);
  useEffect(() => {
    setPanel(null);
    stick.current = true;
  }, [s.id]);
  const send = () => {
    stick.current = true;
    void store.send();
  };

  return (
    <main
      className={`z2 live-conversation ${empty ? "z2-home" : "z2-conversation"} z2-idle`}
    >
      <header className="z2-header">
        <button
          className="z2-brand"
          disabled={locked}
          onClick={() => store.newChat()}
          aria-label="Zelo — new conversation"
        >
          <Mark />
          <span>zelo</span>
        </button>
        <div className="z2-context">
          <span>
            {s.conversation?.title ??
              (s.id ? "Conversation" : "New conversation")}
          </span>
          <span className="z2-preview">Text beta</span>
        </div>
        <nav aria-label="Conversation navigation">
          <button
            className="z2-header-button"
            onClick={() => {
              setPanel("history");
              void store.loadHistory();
            }}
          >
            <Glyph name="history" size={18} />
            <span>History</span>
          </button>
          <button
            className="z2-icon-button"
            disabled={locked}
            onClick={() => store.newChat()}
            aria-label="New conversation"
          >
            <Glyph name="plus" />
          </button>
          <a className="live-fallback" href="/">
            Workspace
          </a>
        </nav>
      </header>
      {empty ? (
        <section className="z2-welcome" aria-label="Start a conversation">
          <Mark large />
          <h1>Talk to Zelo.</h1>
          <p>Start with a message below.</p>
        </section>
      ) : (
        <div
          ref={scroll}
          className="z2-scroll"
          style={{ bottom: composerHeight }}
          role="region"
          aria-label="Conversation"
          tabIndex={0}
          onScroll={() => {
            const node = scroll.current;
            if (node)
              stick.current =
                node.scrollHeight - node.scrollTop - node.clientHeight < 80;
          }}
        >
          <div className="z2-thread">
            <div className="z2-thread-start">
              <span>Conversation</span>
              <span>Live text</span>
            </div>
            {s.loading && <p role="status">Loading conversation…</p>}
            {s.id && !s.conversation && !s.loading && (
              <button onClick={() => void store.loadTurns()}>
                Retry loading conversation
              </button>
            )}
            {s.conversation && s.conversation.turn_total > 50 && (
              <div className="live-pagination">
                <button
                  disabled={
                    s.loading || locked || s.conversation.turn_offset === 0
                  }
                  onClick={() => {
                    stick.current = false;
                    void store.loadTurns(
                      Math.max(0, s.conversation!.turn_offset - 50),
                    );
                  }}
                >
                  Earlier messages
                </button>
                <span>
                  {s.conversation.turn_offset + 1}–
                  {Math.min(
                    s.conversation.turn_offset + turns.length,
                    s.conversation.turn_total,
                  )}{" "}
                  of {s.conversation.turn_total}
                </span>
                <button
                  disabled={
                    s.loading ||
                    locked ||
                    s.conversation.turn_offset + turns.length >=
                      s.conversation.turn_total
                  }
                  onClick={() => {
                    stick.current = true;
                    void store.loadTurns();
                  }}
                >
                  Latest messages
                </button>
              </div>
            )}
            {!s.loading && s.conversation && !turns.length && (
              <p>No messages yet. Write the first message below.</p>
            )}
            {turns.map((turn) => (
              <Fragment key={turn.id}>
                <article
                  className="z2-turn z2-turn-user"
                  aria-label="Your message"
                >
                  <div className="z2-turn-identity">
                    <span className="z2-you">Y</span>
                    <span>You</span>
                  </div>
                  <div className="z2-turn-body">
                    <p>{bounded(turn.user_content)}</p>
                  </div>
                </article>
                <article
                  className="z2-turn z2-turn-assistant"
                  aria-label="Zelo reply"
                >
                  <div className="z2-turn-identity">
                    <Mark />
                    <span>Zelo</span>
                  </div>
                  <div className="z2-turn-body">
                    <p>{bounded(turn.assistant_content)}</p>
                    <small className="live-model">
                      {turn.selected_alias} · {turn.selected_model}
                    </small>
                    {turn.core_task && (
                      <button
                        className="z2-result-card"
                        onClick={() => setPanel(turn.core_task!.id)}
                      >
                        <span className="z2-result-icon">
                          <Glyph name="keyboard" size={22} />
                        </span>
                        <span>
                          <strong>
                            {turn.core_task.kind.replaceAll("_", " ")}
                          </strong>
                          <small>
                            {turn.core_task.status.replaceAll("_", " ")} · View
                            details
                          </small>
                        </span>
                        <Glyph name="arrow" />
                      </button>
                    )}
                  </div>
                </article>
              </Fragment>
            ))}
          </div>
        </div>
      )}
      <div ref={compose} className="z2-compose-wrap">
        <div className="live-status" role="status">
          {s.busy
            ? "Waiting for Core’s reply…"
            : s.connection === "online"
              ? "Connected to Core"
              : s.connection === "checking"
                ? "Checking Core connection…"
                : "Core unavailable — you can keep writing."}
          {s.connection === "offline" && (
            <button onClick={() => void store.health()}>
              Check connection
            </button>
          )}
        </div>
        {s.error && (
          <div className="live-error" role="alert">
            {s.error}
            {s.pending && (
              <button disabled={s.busy} onClick={() => void store.retry()}>
                Retry original message
              </button>
            )}
          </div>
        )}
        {s.pollError && (
          <p className="live-error" role="status">
            {s.pollError}
          </p>
        )}
        <form
          className="z2-composer"
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
        >
          <button
            type="button"
            className="z2-attach z2-icon-button"
            disabled
            aria-label="Attachments unavailable in text beta"
            aria-describedby="live-unavailable"
          >
            <Glyph name="plus" size={22} />
          </button>
          <textarea
            ref={input}
            rows={1}
            aria-label="Message Zelo"
            placeholder="Message Zelo…"
            maxLength={16384}
            value={s.draft}
            readOnly={s.busy}
            onChange={(event) => store.setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing &&
                event.keyCode !== 229
              ) {
                event.preventDefault();
                send();
              }
            }}
          />
          <button
            type="button"
            className="z2-talk"
            disabled
            aria-label="Voice unavailable in text beta"
            aria-describedby="live-unavailable"
          >
            <Glyph name="mic" size={19} />
            <span>Talk</span>
          </button>
          <button
            type="submit"
            className="z2-send"
            aria-label="Send message"
            disabled={
              s.busy ||
              s.loading ||
              s.createUncertain ||
              (!!s.id && !s.conversation) ||
              !s.draft.trim()
            }
          >
            <Glyph name="arrow" size={21} />
          </button>
        </form>
        <div className="z2-composer-note">
          <span id="live-unavailable">
            Voice and attachments unavailable in text beta.
          </span>
          <span>Live Core</span>
        </div>
      </div>
      <dialog
        ref={dialog}
        className={`z2-dialog z2-panel-${panel === "history" ? "history" : "result"}`}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            setPanel(null);
          }
        }}
        onCancel={() => setPanel(null)}
        onClose={() => setPanel(null)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setPanel(null);
        }}
        aria-label={
          panel === "history" ? "Conversation history" : "Core task details"
        }
      >
        <div className="z2-panel-content">
          <header>
            <div>
              <span className="z2-panel-eyebrow">
                {panel === "history" ? "CONVERSATIONS" : "CORE TASK"}
              </span>
              <h2>
                {panel === "history"
                  ? "Pick up a conversation"
                  : "Task details"}
              </h2>
            </div>
            <button
              className="z2-icon-button"
              aria-label="Close panel"
              onClick={() => setPanel(null)}
            >
              <Glyph name="close" />
            </button>
          </header>
          {panel === "history" ? (
            <>
              {locked && (
                <p>
                  Finish or retry the pending message before switching
                  conversations.
                </p>
              )}
              {s.historyLoading && <p role="status">Loading history…</p>}
              {s.historyError && <p role="alert">{s.historyError}</p>}
              <button
                disabled={s.historyLoading}
                onClick={() => void store.loadHistory()}
              >
                Refresh history
              </button>
              {!s.historyLoading &&
                !s.historyError &&
                !s.history.items.length && <p>No saved conversations yet.</p>}
              <label className="z2-search">
                <span>Search this history page</span>
                <input
                  type="search"
                  placeholder="Find a conversation on this page…"
                  value={historySearch}
                  onChange={(event) => setHistorySearch(event.target.value)}
                />
              </label>
              {!s.historyLoading &&
                historySearch.trim() &&
                !visibleHistory.length && (
                  <p className="z2-no-results">No matches on this page.</p>
                )}
              <div className="z2-history-list">
                {visibleHistory.map((item) => (
                  <button
                    key={item.id}
                    disabled={locked}
                    aria-current={item.id === s.id ? "true" : undefined}
                    onClick={() => {
                      setPanel(null);
                      void store.open(item.id);
                    }}
                  >
                    <div>
                      <strong>{bounded(item.title, 120)}</strong>
                      <small>
                        {item.turn_count} turns ·{" "}
                        {new Date(item.updated_at).toLocaleDateString()}
                      </small>
                    </div>
                    <Glyph name="arrow" />
                  </button>
                ))}
              </div>
              <div className="live-pagination">
                <span>
                  Showing {s.history.offset + s.history.items.length} of{" "}
                  {s.history.total}
                </span>
                {s.history.offset > 0 && (
                  <button
                    disabled={s.historyLoading}
                    onClick={() => void store.loadHistory()}
                  >
                    First page
                  </button>
                )}
                {s.history.offset + s.history.items.length <
                  s.history.total && (
                  <button
                    disabled={s.historyLoading}
                    onClick={() => void store.loadHistory(true)}
                  >
                    Load more conversations
                  </button>
                )}
              </div>
            </>
          ) : task ? (
            <TaskResult task={task} />
          ) : (
            <p>No task details available in this page of history.</p>
          )}
        </div>
      </dialog>
    </main>
  );
}
