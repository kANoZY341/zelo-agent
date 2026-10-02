"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Glyph } from "../shared";

export type ConversationScene =
  | "home"
  | "conversation"
  | "listening"
  | "thinking"
  | "speaking"
  | "result"
  | "approval"
  | "history";
type Phase = "idle" | "listening" | "thinking" | "speaking";
type Panel = "history" | "result" | "approval" | "menu" | null;
type Turn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  files?: string[];
  result?: boolean;
  approval?: boolean;
};
type Thread = { id: string; title: string; turns: Turn[] };

const sample: Turn[] = [
  { id: "a", role: "user", text: "Can we make Zelo easier to talk to?" },
  {
    id: "b",
    role: "assistant",
    text: "Keep the conversation in one place. You can type, talk, or interrupt me without switching screens.",
  },
  { id: "c", role: "user", text: "And when you’re working on something?" },
  {
    id: "d",
    role: "assistant",
    text: "The result stays here with our conversation. Open it when you want the detail.",
    result: true,
  },
];
const initialThreads: Thread[] = [
  { id: "workspace", title: "A simpler Zelo workspace", turns: sample },
  {
    id: "voice",
    title: "Talking to Zelo",
    turns: [
      { id: "v1", role: "user", text: "Can I type while we’re talking?" },
      {
        id: "v2",
        role: "assistant",
        text: "Yes. Voice and text stay in the same conversation. You can interrupt a reply or end voice whenever you want.",
      },
    ],
  },
  {
    id: "plan",
    title: "Reviewing a result",
    turns: [
      { id: "p1", role: "user", text: "Show me the conversation plan." },
      {
        id: "p2",
        role: "assistant",
        text: "Here’s the plan. Close it to return to this exact point in our conversation.",
        result: true,
      },
    ],
  },
];
const voicePrompt = "Show me where we left off.";
const id = () => crypto.randomUUID();

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

function Wave({ active = false }: { active?: boolean }) {
  return (
    <span className={`z2-wave${active ? " is-active" : ""}`} aria-hidden="true">
      {[10, 18, 28, 14, 34, 22, 12].map((height, i) => (
        <i key={i} style={{ height, animationDelay: `${i * 90}ms` }} />
      ))}
    </span>
  );
}

export function ConversationPreview({ scene }: { scene: ConversationScene }) {
  const starting = scene === "home" ? "new" : "workspace";
  const [threads, setThreads] = useState<Thread[]>(() => {
    const copy = initialThreads.map((thread) => ({
      ...thread,
      turns: [...thread.turns],
    }));
    if (scene === "thinking")
      copy[0].turns.push({
        id: "pending",
        role: "user",
        text: "What would the voice interaction look like?",
      });
    if (scene === "approval")
      copy[0].turns.push({
        id: "decision",
        role: "assistant",
        text: "Review the proposed change before it goes ahead.",
        approval: true,
      });
    return [{ id: "new", title: "New conversation", turns: [] }, ...copy];
  });
  const [currentId, setCurrentId] = useState(starting);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>(
    ["listening", "thinking", "speaking"].includes(scene)
      ? (scene as Phase)
      : "idle",
  );
  const [voice, setVoice] = useState(
    scene === "listening" || scene === "speaking",
  );
  const [playing, setPlaying] = useState(false);
  const [transcript, setTranscript] = useState(
    scene === "listening" ? voicePrompt : "",
  );
  const [panel, setPanel] = useState<Panel>(
    scene === "history" ? "history" : scene === "result" ? "result" : null,
  );
  const [search, setSearch] = useState("");
  const [decision, setDecision] = useState<"approved" | "declined" | null>(
    null,
  );
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const replyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generation = useRef(0);
  const current = threads.find((thread) => thread.id === currentId)!;
  const empty = current.turns.length === 0;
  const busy = phase === "thinking";

  function cancelReply() {
    generation.current += 1;
    if (replyTimer.current) clearTimeout(replyTimer.current);
    replyTimer.current = null;
    setPhase("idle");
  }

  function endVoice() {
    cancelReply();
    setVoice(false);
    setPlaying(false);
    setTranscript("");
    input.current?.focus();
  }

  function startVoice() {
    cancelReply();
    setVoice(true);
    setPlaying(true);
    setTranscript("");
    setPhase("listening");
  }

  function openThread(threadId: string) {
    endVoice();
    setCurrentId(threadId);
    setDraft("");
    setFiles([]);
    setDecision(null);
    if (fileInput.current) fileInput.current.value = "";
    setPanel(null);
    requestAnimationFrame(() => input.current?.focus());
  }

  function newConversation() {
    const newId = id();
    setThreads((old) => [
      { id: newId, title: "New conversation", turns: [] },
      ...old.filter((thread) => thread.turns.length),
    ]);
    openThread(newId);
  }

  function send(text = draft, spoken = false) {
    if (busy || (!text.trim() && !files.length)) return;
    cancelReply();
    const token = generation.current;
    const target = currentId;
    const value = text.trim();
    const attached = [...files];
    const turn: Turn = { id: id(), role: "user", text: value, files: attached };
    setThreads((old) =>
      old.map((thread) =>
        thread.id === target
          ? {
              ...thread,
              title: thread.turns.length
                ? thread.title
                : (value || attached[0]).slice(0, 48),
              turns: [...thread.turns, turn],
            }
          : thread,
      ),
    );
    setDraft("");
    setFiles([]);
    if (fileInput.current) fileInput.current.value = "";
    setTranscript("");
    setPhase("thinking");
    const result = /plan|result|research|left off/i.test(value);
    const approval = /apply|change|edit|approve/i.test(value) && !result;
    replyTimer.current = setTimeout(() => {
      if (generation.current !== token) return;
      const response: Turn = {
        id: id(),
        role: "assistant",
        result,
        approval,
        text: result
          ? "Here’s the conversation plan. Open it when you want the details."
          : approval
            ? "Here’s how a proposed change will appear in our conversation. You can review it before deciding."
            : attached.length
              ? "The file names are attached to this preview conversation. Their contents haven’t been uploaded."
              : "This is a preview reply. Your message stays in this conversation while you try the controls.",
      };
      setThreads((old) =>
        old.map((thread) =>
          thread.id === target
            ? { ...thread, turns: [...thread.turns, response] }
            : thread,
        ),
      );
      replyTimer.current = null;
      setPhase(voice || spoken ? "speaking" : "idle");
    }, 800);
  }

  useEffect(
    () => () => {
      generation.current += 1;
      if (replyTimer.current) clearTimeout(replyTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (!voice || !playing || phase !== "listening") return;
    let at = 0;
    const timer = setInterval(() => {
      at += 2;
      setTranscript(voicePrompt.slice(0, at));
      if (at >= voicePrompt.length) clearInterval(timer);
    }, 65);
    return () => clearInterval(timer);
  }, [voice, playing, phase]);
  useEffect(() => {
    if (!voice || !playing || phase !== "speaking") return;
    const timer = setTimeout(() => {
      setTranscript("");
      setPhase("listening");
    }, 7000);
    return () => clearTimeout(timer);
  }, [voice, playing, phase]);
  useEffect(() => {
    if (panel && !dialog.current?.open) dialog.current?.showModal();
    if (!panel && dialog.current?.open) dialog.current?.close();
  }, [panel]);
  useEffect(() => {
    scroll.current?.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: "instant",
    });
  }, [current.turns.length, currentId, phase]);
  useLayoutEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 128)}px`;
  }, [draft]);

  const stateLabel =
    phase === "listening"
      ? "Listening"
      : phase === "thinking"
        ? "Thinking"
        : phase === "speaking"
          ? "Speaking"
          : "Conversation";
  const hasResult = current.turns.some((turn) => turn.result);

  return (
    <main className={`z2 ${empty ? "z2-home" : "z2-conversation"} z2-${phase}`}>
      <header className="z2-header">
        <button
          className="z2-brand"
          onClick={newConversation}
          aria-label="Zelo — new conversation"
        >
          <Mark />
          <span>zelo</span>
        </button>
        <div className="z2-context">
          <span>{current.title}</span>
          <span className="z2-preview">Preview</span>
        </div>
        <nav aria-label="Conversation navigation">
          <button
            className="z2-header-button"
            onClick={() => {
              setSearch("");
              setPanel("history");
            }}
          >
            <Glyph name="history" size={18} />
            <span>History</span>
          </button>
          <button
            className="z2-icon-button"
            onClick={() => setPanel("menu")}
            aria-label="Conversation options"
          >
            <Glyph name="system" size={19} />
          </button>
        </nav>
      </header>

      {empty ? (
        <section className="z2-welcome" aria-label="Start a conversation">
          <Mark large />
          <h1>Talk to Zelo.</h1>
          <p>Start with your voice, or type below.</p>
        </section>
      ) : (
        <div
          ref={scroll}
          className="z2-scroll"
          role="region"
          aria-label="Conversation"
          tabIndex={0}
        >
          <div className="z2-thread">
            <div className="z2-thread-start">
              <span>Conversation</span>
              <span>Text + voice</span>
            </div>
            {current.turns.map((turn) => (
              <article
                key={turn.id}
                className={`z2-turn z2-turn-${turn.role}`}
                aria-label={
                  turn.role === "user" ? "Your message" : "Zelo reply"
                }
              >
                <div className="z2-turn-identity">
                  {turn.role === "assistant" ? (
                    <Mark />
                  ) : (
                    <span className="z2-you">A</span>
                  )}
                  <span>{turn.role === "assistant" ? "Zelo" : "You"}</span>
                </div>
                <div className="z2-turn-body">
                  {turn.text && <p>{turn.text}</p>}
                  {!!turn.files?.length && (
                    <div className="z2-inline-files">
                      {turn.files.map((name) => (
                        <span key={name}>
                          <Glyph name="plus" size={14} />
                          {name}
                        </span>
                      ))}
                    </div>
                  )}
                  {turn.result && (
                    <button
                      className="z2-result-card"
                      onClick={() => setPanel("result")}
                    >
                      <span className="z2-result-icon">
                        <Glyph name="keyboard" size={22} />
                      </span>
                      <span>
                        <strong>Conversation flow</strong>
                        <small>Workspace plan · preview document</small>
                      </span>
                      <Glyph name="arrow" size={20} />
                    </button>
                  )}
                  {turn.approval && (
                    <div className="z2-decision-card">
                      <div>
                        <span className="z2-attention-dot" />
                        <strong>
                          {decision
                            ? `Change ${decision} in preview`
                            : "Your decision"}
                        </strong>
                      </div>
                      <p>Workspace interaction update</p>
                      <button onClick={() => setPanel("approval")}>
                        {decision ? "View decision" : "Review change"}
                        <Glyph name="arrow" size={17} />
                      </button>
                    </div>
                  )}
                </div>
              </article>
            ))}
            {busy && (
              <div className="z2-pending" role="status">
                <Mark />
                <span>
                  Zelo is thinking<span className="z2-thinking-dots">…</span>
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="z2-compose-wrap">
        {(voice || busy) && (
          <section
            className={`z2-voice-bar z2-voice-${phase}`}
            aria-label="Voice controls"
          >
            <div className="z2-voice-heading">
              <span className="z2-state-dot" />
              <strong>{stateLabel}</strong>
              <span>{voice ? "Voice preview" : "Preview reply"}</span>
              <button onClick={voice ? endVoice : cancelReply}>
                {voice ? "End voice" : "Cancel"}
                <Glyph name="close" size={14} />
              </button>
            </div>
            {voice && (
              <div className="z2-voice-content">
                <Wave active={phase === "listening" || phase === "speaking"} />
                <p aria-live="polite">
                  {phase === "listening"
                    ? transcript || "Listening…"
                    : phase === "speaking"
                      ? "Zelo’s reply is in the conversation."
                      : "Preparing a reply…"}
                </p>
                {phase === "speaking" ? (
                  <button className="z2-voice-next" onClick={startVoice}>
                    Interrupt
                    <Glyph name="mic" size={15} />
                  </button>
                ) : phase === "listening" ? (
                  <button
                    className="z2-voice-next"
                    disabled={!transcript.trim()}
                    onClick={() => send(transcript, true)}
                  >
                    Send voice
                    <Glyph name="arrow" size={15} />
                  </button>
                ) : null}
              </div>
            )}
          </section>
        )}
        {!!files.length && (
          <div className="z2-file-list" aria-label="Selected files">
            {files.map((name) => (
              <button
                key={name}
                onClick={() =>
                  setFiles((old) => old.filter((item) => item !== name))
                }
                aria-label={`Remove ${name}`}
              >
                <span>{name}</span>
                <Glyph name="close" size={13} />
              </button>
            ))}
          </div>
        )}
        <form
          className="z2-composer"
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
        >
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            aria-label="Choose files"
            onChange={(event) => {
              const names = Array.from(event.target.files ?? []).map(
                (file) => file.name,
              );
              setFiles((old) => [...new Set([...old, ...names])]);
              event.target.value = "";
            }}
          />
          <button
            type="button"
            className="z2-attach z2-icon-button"
            aria-label="Attach files"
            onClick={() => fileInput.current?.click()}
          >
            <Glyph name="plus" size={22} />
          </button>
          <textarea
            ref={input}
            rows={1}
            aria-label="Message Zelo"
            placeholder="Message Zelo…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                send();
              }
            }}
          />
          <button
            type="button"
            className={`z2-talk${voice ? " is-on" : ""}`}
            onClick={voice ? endVoice : startVoice}
            aria-label={
              voice ? "End voice conversation" : "Start voice conversation"
            }
          >
            <Glyph name={voice ? "stop" : "mic"} size={19} />
            <span>{voice ? "End voice" : "Talk"}</span>
          </button>
          {busy ? (
            <button
              type="button"
              className="z2-send"
              aria-label="Cancel reply"
              onClick={cancelReply}
            >
              <Glyph name="stop" size={19} />
            </button>
          ) : (
            <button
              type="submit"
              className="z2-send"
              aria-label="Send message"
              disabled={!draft.trim() && !files.length}
            >
              <Glyph name="arrow" size={21} />
            </button>
          )}
        </form>
        <div className="z2-composer-note">
          <span>One conversation. Voice or text.</span>
          <span>Interactive preview</span>
        </div>
      </div>

      <dialog
        ref={dialog}
        className={`z2-dialog z2-panel-${panel ?? "closed"}`}
        onCancel={() => setPanel(null)}
        onClose={() => setPanel(null)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setPanel(null);
        }}
        aria-label={
          panel === "history"
            ? "Conversation history"
            : panel === "result"
              ? "Conversation flow"
              : panel === "approval"
                ? "Review proposed change"
                : "Conversation options"
        }
      >
        <div className="z2-panel-content">
          <header>
            <div>
              <span className="z2-panel-eyebrow">
                {panel === "history"
                  ? "CONVERSATIONS"
                  : panel === "result"
                    ? "PREVIEW DOCUMENT"
                    : panel === "approval"
                      ? "APPROVAL PREVIEW"
                      : "ZELO"}
              </span>
              <h2>
                {panel === "history"
                  ? "Pick up a conversation"
                  : panel === "result"
                    ? "Conversation flow"
                    : panel === "approval"
                      ? "Review the change"
                      : "Conversation options"}
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
              <button className="z2-new-thread" onClick={newConversation}>
                <Glyph name="plus" size={18} />
                New conversation
              </button>
              <label className="z2-search">
                <span>Search conversations</span>
                <input
                  type="search"
                  placeholder="Find a conversation…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <div className="z2-history-list">
                {threads
                  .filter(
                    (thread) =>
                      thread.turns.length &&
                      thread.title.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((thread) => (
                    <button
                      key={thread.id}
                      onClick={() => openThread(thread.id)}
                      aria-current={
                        thread.id === currentId ? "true" : undefined
                      }
                    >
                      <div>
                        <strong>{thread.title}</strong>
                        <small>{thread.turns.at(-1)?.text}</small>
                      </div>
                      <Glyph name="arrow" size={19} />
                    </button>
                  ))}
                {!threads.some(
                  (thread) =>
                    thread.turns.length &&
                    thread.title.toLowerCase().includes(search.toLowerCase()),
                ) && (
                  <p className="z2-no-results">No matching conversations.</p>
                )}
              </div>
            </>
          ) : panel === "result" ? (
            <div className="z2-document">
              <p className="z2-document-lead">
                Talk, type, and review work in one conversation.
              </p>
              <section>
                <span>01</span>
                <div>
                  <h3>Start talking</h3>
                  <p>
                    One voice session stays with the conversation. Listening,
                    thinking, and speaking appear beside the controls.
                  </p>
                </div>
              </section>
              <section>
                <span>02</span>
                <div>
                  <h3>Keep your place</h3>
                  <p>
                    Typing and voice share the same thread. Interrupt a reply,
                    add a file, or continue with a message.
                  </p>
                </div>
              </section>
              <section>
                <span>03</span>
                <div>
                  <h3>Open work when you need it</h3>
                  <p>
                    Results and approvals sit inside the conversation. Detail
                    opens on demand and closes back to the same point.
                  </p>
                </div>
              </section>
              <button
                className="z2-primary-button"
                onClick={() => setPanel(null)}
              >
                Back to the conversation
                <Glyph name="back" size={17} />
              </button>
            </div>
          ) : panel === "approval" ? (
            <div className="z2-review">
              <div className="z2-review-summary">
                <Glyph name="keyboard" size={28} />
                <div>
                  <strong>Workspace interaction update</strong>
                  <p>Demonstration of a proposed change.</p>
                </div>
              </div>
              <p>
                Replace the scattered controls with one conversation surface, a
                voice session, and results you open from the thread.
              </p>
              <p className="z2-preview-note">
                This decision is part of the preview. It will not modify files
                or run work.
              </p>
              {decision ? (
                <div className="z2-decision-status" role="status">
                  <Glyph name="check" size={20} />
                  {decision === "approved"
                    ? "Approved in preview"
                    : "Declined in preview"}
                </div>
              ) : (
                <div className="z2-review-actions">
                  <button
                    className="z2-primary-button"
                    onClick={() => setDecision("approved")}
                  >
                    Approve in preview
                    <Glyph name="check" size={17} />
                  </button>
                  <button
                    className="z2-secondary-button"
                    onClick={() => setDecision("declined")}
                  >
                    Decline
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="z2-menu">
              <button onClick={newConversation}>
                <Glyph name="plus" />
                New conversation
                <Glyph name="arrow" size={17} />
              </button>
              <button
                onClick={() => {
                  setSearch("");
                  setPanel("history");
                }}
              >
                <Glyph name="history" />
                Conversation history
                <Glyph name="arrow" size={17} />
              </button>
              <button disabled={!hasResult} onClick={() => setPanel("result")}>
                <Glyph name="keyboard" />
                Latest result
                <Glyph name="arrow" size={17} />
              </button>
              <button
                onClick={() => {
                  setPanel(null);
                  if (voice) endVoice();
                  else startVoice();
                }}
              >
                <Glyph name="mic" />
                {voice ? "End voice conversation" : "Start voice conversation"}
                <Glyph name="arrow" size={17} />
              </button>
            </div>
          )}
        </div>
      </dialog>
    </main>
  );
}
