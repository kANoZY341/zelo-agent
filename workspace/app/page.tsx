"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ControlDesk } from "@/components/ControlDesk";
import type {
  Conversation,
  ConversationPage,
  ConversationSummary,
  ConversationTurn,
  TaskProjection,
} from "@/lib/types";

const ACTIVE_TASK_STATES = new Set(["pending", "awaiting_approval", "running"]);
const WORKSPACE_HEADERS = { "X-Zelo-Workspace": "1" };
const ACTIVE_CONVERSATION_STORAGE_KEY = "zelo.workspace.activeConversationId";

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

function detectedSpeechLanguage(text: string): "ar" | "en" {
  const arabic = text.match(/[\u0600-\u06ff]/g)?.length ?? 0;
  const latin = text.match(/[A-Za-z]/g)?.length ?? 0;
  return arabic > latin ? "ar" : "en";
}

function recorderMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ].find((candidate) => MediaRecorder.isTypeSupported(candidate));
}

function conversationTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
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
  const [activeView, setActiveView] = useState<"talk" | "desk">("talk");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [coreHealthy, setCoreHealthy] = useState<boolean | null>(null);
  const [voiceAvailable, setVoiceAvailable] = useState<boolean | null>(null);
  const [recording, setRecording] = useState(false);
  const [voiceStarting, setVoiceStarting] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const voiceChunksRef = useRef<Blob[]>([]);
  const playbackRef = useRef<HTMLAudioElement | null>(null);
  const playbackUrlRef = useRef<string | null>(null);
  const voicePressHeldRef = useRef(false);
  const voiceStartPendingRef = useRef(false);
  const discardRecordingRef = useRef(false);
  const voiceMountedRef = useRef(true);
  const activeConversationIdRef = useRef<string | null>(null);
  const conversationBodyRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const pendingSubmissionRef = useRef<{ prompt: string; key: string } | null>(
    null,
  );

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === conversationId) ?? null,
    [conversations, conversationId],
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

  const loadConversationList = useCallback(async (): Promise<ConversationSummary[]> => {
    const items: ConversationSummary[] = [];
    const pageSize = 100;
    let offset = 0;
    let total = 0;

    do {
      const response = await fetch(
        `/api/conversations?limit=${pageSize}&offset=${offset}`,
        {
          headers: WORKSPACE_HEADERS,
          cache: "no-store",
        },
      );
      const page = await parseJson<ConversationPage>(response);
      items.push(...page.items);
      total = page.total;
      offset += page.items.length;
      if (page.items.length === 0) break;
    } while (offset < total);

    setConversations(items);
    return items;
  }, []);

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
      if (activeConversationIdRef.current === id) {
        setMessages(toMessages(conversation.turns));
      }
      return conversation;
    },
    [],
  );

  const pollUntilTaskSettles = useCallback(
    async (id: string) => {
      if (pollRef.current) clearTimeout(pollRef.current);

      const tick = async () => {
        if (activeConversationIdRef.current !== id) return;
        try {
          const conversation = await refreshConversation(id);
          if (activeConversationIdRef.current !== id) return;
          const hasActiveTask = conversation.turns.some(
            (turn) =>
              turn.core_task && ACTIVE_TASK_STATES.has(turn.core_task.status),
          );
          if (hasActiveTask) {
            pollRef.current = setTimeout(tick, 1200);
          }
        } catch (pollError) {
          if (activeConversationIdRef.current !== id) return;
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

  const openConversation = useCallback(
    async (id: string) => {
      if (
        busy ||
        conversationLoading ||
        recording ||
        voiceStarting ||
        voiceBusy
      ) return;
      if (pendingSubmissionRef.current) {
        setError(
          "Resolve the pending message before switching conversations.",
        );
        return;
      }

      if (pollRef.current) clearTimeout(pollRef.current);
      stickToBottomRef.current = true;
      setConversationLoading(true);
      stopPlayback();
      setHistoryOpen(false);
      setPendingPrompt(null);
      setError(null);
      activeConversationIdRef.current = id;
      setConversationId(id);
      window.localStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, id);

      try {
        const conversation = await refreshConversation(id);
        const hasActiveTask = conversation.turns.some(
          (turn) =>
            turn.core_task && ACTIVE_TASK_STATES.has(turn.core_task.status),
        );
        if (hasActiveTask) {
          await pollUntilTaskSettles(id);
        }
      } catch (openError) {
        if (
          window.localStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY) === id
        ) {
          window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
        }
        if (activeConversationIdRef.current === id) {
          activeConversationIdRef.current = null;
          setConversationId(null);
          setMessages([]);
        }
        setError(
          openError instanceof Error
            ? openError.message
            : "conversation_open_failed",
        );
      } finally {
        setConversationLoading(false);
      }
    },
    [
      busy,
      conversationLoading,
      pollUntilTaskSettles,
      recording,
      refreshConversation,
      voiceBusy,
      voiceStarting,
    ],
  );

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    const frame = requestAnimationFrame(() => {
      const body = conversationBodyRef.current;
      if (body) body.scrollTo({ top: body.scrollHeight, behavior: "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, pendingPrompt]);

  useEffect(() => {
    let cancelled = false;

    fetch("/api/voice/health", {
      headers: WORKSPACE_HEADERS,
      cache: "no-store",
    })
      .then((response) => parseJson<{ status: string }>(response))
      .then((health) => {
        if (!cancelled) setVoiceAvailable(health.status === "healthy");
      })
      .catch(() => {
        if (!cancelled) setVoiceAvailable(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    voiceMountedRef.current = true;

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
      voiceMountedRef.current = false;
      discardRecordingRef.current = true;
      voicePressHeldRef.current = false;
      const recorder = mediaRecorderRef.current;
      if (recorder?.state === "recording") recorder.stop();
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      stopPlayback();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const restore = async () => {
      setHistoryLoading(true);
      try {
        const saved = await loadConversationList();
        if (cancelled) return;

        const storedId = window.localStorage.getItem(
          ACTIVE_CONVERSATION_STORAGE_KEY,
        );
        if (storedId && saved.some((conversation) => conversation.id === storedId)) {
          setConversationLoading(true);
          activeConversationIdRef.current = storedId;
          setConversationId(storedId);
          try {
            const conversation = await refreshConversation(storedId);
            const hasActiveTask = conversation.turns.some(
              (turn) =>
                turn.core_task && ACTIVE_TASK_STATES.has(turn.core_task.status),
            );
            if (hasActiveTask) {
              await pollUntilTaskSettles(storedId);
            }
          } catch (restoreError) {
            window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
            if (activeConversationIdRef.current === storedId) {
              activeConversationIdRef.current = null;
              setConversationId(null);
              setMessages([]);
            }
            throw restoreError;
          } finally {
            if (!cancelled) setConversationLoading(false);
          }
        } else if (storedId) {
          window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
        }
      } catch (historyError) {
        if (!cancelled) {
          setError(
            historyError instanceof Error
              ? historyError.message
              : "conversation_history_failed",
          );
        }
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    };

    void restore();
    return () => {
      cancelled = true;
    };
  }, [loadConversationList, pollUntilTaskSettles, refreshConversation]);

  function stopPlayback() {
    playbackRef.current?.pause();
    playbackRef.current = null;
    if (playbackUrlRef.current) {
      URL.revokeObjectURL(playbackUrlRef.current);
      playbackUrlRef.current = null;
    }
  }

  async function speakAssistant(text: string) {
    const spoken = text.trim();
    if (!spoken || voiceAvailable !== true) return;
    const response = await fetch("/api/voice/synthesize", {
      method: "POST",
      headers: {
        ...WORKSPACE_HEADERS,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: spoken,
        language: detectedSpeechLanguage(spoken),
      }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(
        typeof detail?.detail === "string" ? detail.detail : "voice_synthesis_failed",
      );
    }
    stopPlayback();
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    playbackRef.current = audio;
    playbackUrlRef.current = url;
    const release = () => {
      if (playbackRef.current === audio) playbackRef.current = null;
      if (playbackUrlRef.current === url) {
        URL.revokeObjectURL(url);
        playbackUrlRef.current = null;
      }
    };
    audio.addEventListener("ended", release, { once: true });
    audio.addEventListener("error", release, { once: true });
    await audio.play();
  }

  async function ensureConversation(): Promise<string> {
    if (conversationId) return conversationId;

    const response = await fetch("/api/conversations", {
      method: "POST",
      headers: {
        ...WORKSPACE_HEADERS,
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    const conversation = await parseJson<Conversation>(response);
    activeConversationIdRef.current = conversation.id;
    setConversationId(conversation.id);
    window.localStorage.setItem(
      ACTIVE_CONVERSATION_STORAGE_KEY,
      conversation.id,
    );
    return conversation.id;
  }

  async function sendPrompt(prompt: string, speakReply: boolean) {
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

    stickToBottomRef.current = true;
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
      void loadConversationList().catch(() => undefined);

      if (speakReply) {
        void speakAssistant(turn.assistant_content).catch((voiceError) => {
          setError(
            voiceError instanceof Error ? voiceError.message : "voice_synthesis_failed",
          );
        });
      }

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

  async function submit(event: FormEvent) {
    event.preventDefault();
    const prompt = text.trim();
    await sendPrompt(prompt, false);
  }

  async function handleVoiceBlob(blob: Blob) {
    if (!blob.size) return;
    setVoiceBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/voice/transcribe", {
        method: "POST",
        headers: {
          ...WORKSPACE_HEADERS,
          "Content-Type": blob.type || "audio/webm",
        },
        body: blob,
      });
      const result = await parseJson<{ text: string; language: string | null }>(
        response,
      );
      await sendPrompt(result.text.trim(), true);
    } catch (voiceError) {
      setError(
        voiceError instanceof Error ? voiceError.message : "voice_transcription_failed",
      );
    } finally {
      setVoiceBusy(false);
    }
  }

  async function startVoiceRecording() {
    if (
      voiceAvailable !== true ||
      recording ||
      voiceStartPendingRef.current ||
      voiceBusy ||
      busy ||
      conversationLoading
    ) {
      return;
    }
    if (
      typeof MediaRecorder === "undefined" ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      setError("voice_recording_unsupported");
      return;
    }

    stopPlayback();
    voiceStartPendingRef.current = true;
    setVoiceStarting(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (!voicePressHeldRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      microphoneStreamRef.current = stream;
      const mimeType = recorderMimeType();
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      mediaRecorderRef.current = recorder;
      voiceChunksRef.current = [];
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) voiceChunksRef.current.push(event.data);
      });
      recorder.addEventListener(
        "stop",
        () => {
          const recordedType =
            recorder.mimeType || voiceChunksRef.current[0]?.type || "audio/webm";
          const blob = new Blob(voiceChunksRef.current, { type: recordedType });
          voiceChunksRef.current = [];
          microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
          microphoneStreamRef.current = null;
          mediaRecorderRef.current = null;
          setRecording(false);
          if (discardRecordingRef.current || !voiceMountedRef.current) {
            discardRecordingRef.current = false;
            return;
          }
          void handleVoiceBlob(blob);
        },
        { once: true },
      );
      recorder.start();
      setRecording(true);
      if (!voicePressHeldRef.current && recorder.state === "recording") {
        recorder.stop();
      }
    } catch (voiceError) {
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      microphoneStreamRef.current = null;
      mediaRecorderRef.current = null;
      setRecording(false);
      setError(
        voiceError instanceof Error ? voiceError.message : "microphone_unavailable",
      );
    } finally {
      voiceStartPendingRef.current = false;
      if (voiceMountedRef.current) setVoiceStarting(false);
    }
  }

  function beginVoicePress() {
    voicePressHeldRef.current = true;
    void startVoiceRecording();
  }

  function stopVoiceRecording() {
    voicePressHeldRef.current = false;
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === "recording") recorder.stop();
  }

  function newConversation() {
    if (
      busy ||
      conversationLoading ||
      recording ||
      voiceStarting ||
      voiceBusy
    ) return;
    if (pendingSubmissionRef.current) {
      setError("Resolve the pending message before starting a new conversation.");
      return;
    }
    if (pollRef.current) clearTimeout(pollRef.current);
    stopPlayback();
    stickToBottomRef.current = true;
    setHistoryOpen(false);
    window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
    activeConversationIdRef.current = null;
    setConversationId(null);
    setMessages([]);
    setPendingPrompt(null);
    setError(null);
    setText("");
  }

  return (
    <main className={`workspace-shell ${activeView === "desk" ? "desk-mode" : ""}`}>
      <aside className="rail">
        <div className="brand-mark">Z</div>
        <button
          className={`rail-item ${activeView === "talk" ? "active" : ""}`}
          title="Talk to Zelo"
          onClick={() => setActiveView("talk")}
        >
          Talk
        </button>
        <button className="rail-item" title="Company World" disabled>
          World
        </button>
        <button
          className={`rail-item ${activeView === "desk" ? "active" : ""}`}
          title="Control Desk"
          onClick={() => setActiveView("desk")}
          disabled={recording || voiceStarting || voiceBusy || busy}
        >
          Desk
        </button>
        <div className="rail-spacer" />
        <span className="local-badge">LOCAL</span>
      </aside>

      {activeView === "desk" ? (
        <ControlDesk
          coreHealthy={coreHealthy}
          onShowTalk={() => setActiveView("talk")}
        />
      ) : (
        <>
      <aside className={`history-panel ${historyOpen ? "mobile-open" : ""}`}>
        <div className="history-head">
          <div>
            <p className="eyebrow">TALK / HISTORY</p>
            <h2>Conversations</h2>
          </div>
          <button
            className="history-new"
            onClick={newConversation}
            disabled={busy || conversationLoading || recording || voiceStarting || voiceBusy}
            title="New conversation"
            aria-label="New conversation"
          >
            +
          </button>
        </div>

        <div className="history-list">
          {historyLoading ? (
            <div className="history-empty">Loading conversations…</div>
          ) : conversations.length === 0 ? (
            <div className="history-empty">
              No saved conversations yet. Start talking to Zelo.
            </div>
          ) : (
            conversations.map((conversation) => (
              <button
                key={conversation.id}
                className={`history-item ${
                  conversation.id === conversationId ? "active" : ""
                }`}
                onClick={() => void openConversation(conversation.id)}
                disabled={busy || conversationLoading || recording || voiceStarting || voiceBusy}
                aria-pressed={conversation.id === conversationId}
              >
                <span className="history-title">{conversation.title}</span>
                <span className="history-meta">
                  {conversation.turn_count} turn
                  {conversation.turn_count === 1 ? "" : "s"}
                  <span aria-hidden="true"> · </span>
                  {conversationTimestamp(conversation.updated_at)}
                </span>
              </button>
            ))
          )}
        </div>
      </aside>

      {historyOpen ? (
        <button
          className="history-backdrop"
          onClick={() => setHistoryOpen(false)}
          aria-label="Close conversation history"
        />
      ) : null}

      <section className="conversation-panel">
        <header className="topbar">
          <div>
            <p className="eyebrow">ZELO / MIND</p>
            <h1>{activeConversation?.title ?? "Talk to Zelo"}</h1>
          </div>
          <div className="topbar-actions">
            <button
              className="mobile-view-button"
              onClick={() => setActiveView("desk")}
              disabled={recording || voiceStarting || voiceBusy || busy}
            >
              Desk
            </button>
            <button
              className="mobile-history-button"
              onClick={() => setHistoryOpen(true)}
              disabled={busy || conversationLoading || recording || voiceStarting || voiceBusy}
            >
              History
            </button>
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
            <button
              className="secondary-button"
              onClick={newConversation}
              disabled={busy || conversationLoading || recording || voiceStarting || voiceBusy}
            >
              New conversation
            </button>
          </div>
        </header>

        <div
          className="conversation-body"
          ref={conversationBodyRef}
          onScroll={(event) => {
            const node = event.currentTarget;
            const distanceFromBottom =
              node.scrollHeight - node.scrollTop - node.clientHeight;
            stickToBottomRef.current = distanceFromBottom < 120;
          }}
        >
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
              disabled={busy || conversationLoading || recording || voiceStarting || voiceBusy}
            />
            <div className="composer-footer">
              <span>
                {conversationId
                  ? `Conversation ${conversationId.slice(0, 8)}`
                  : "New conversation"}
              </span>
              <div className="composer-actions">
                <button
                  type="button"
                  className={`voice-button ${recording ? "recording" : ""}`}
                  disabled={
                    voiceAvailable !== true ||
                    voiceBusy ||
                    busy ||
                    conversationLoading
                  }
                  aria-pressed={recording}
                  title={
                    voiceAvailable === false
                      ? "Local voice service is offline"
                      : "Hold to talk to Zelo"
                  }
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.currentTarget.setPointerCapture(event.pointerId);
                    beginVoicePress();
                  }}
                  onPointerUp={(event) => {
                    event.preventDefault();
                    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                      event.currentTarget.releasePointerCapture(event.pointerId);
                    }
                    stopVoiceRecording();
                  }}
                  onPointerCancel={() => stopVoiceRecording()}
                  onKeyDown={(event) => {
                    if (
                      !event.repeat &&
                      (event.key === " " || event.key === "Enter")
                    ) {
                      event.preventDefault();
                      beginVoicePress();
                    }
                  }}
                  onKeyUp={(event) => {
                    if (event.key === " " || event.key === "Enter") {
                      event.preventDefault();
                      stopVoiceRecording();
                    }
                  }}
                >
                  {recording
                    ? "Listening…"
                    : voiceStarting
                      ? "Starting…"
                      : voiceBusy
                        ? "Transcribing…"
                      : voiceAvailable === null
                        ? "Voice…"
                        : voiceAvailable
                          ? "🎙 Hold"
                          : "Voice offline"}
                </button>
                <button
                  className="send-button"
                  disabled={
                    busy ||
                    conversationLoading ||
                    recording ||
                    voiceStarting ||
                    voiceBusy ||
                    !text.trim()
                  }
                >
                  {busy ? "Thinking..." : "Send"}
                </button>
              </div>
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
        </>
      )}
    </main>
  );
}
