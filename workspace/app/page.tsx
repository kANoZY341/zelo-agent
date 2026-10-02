"use client";

import {
  FormEvent,
  PointerEvent,
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

type VoiceState = "idle" | "recording" | "transcribing" | "speaking";

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
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeConversationIdRef = useRef<string | null>(null);
  const conversationBodyRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const voiceChunksRef = useRef<Blob[]>([]);
  const voiceHeldRef = useRef(false);
  const voiceDiscardRecordingRef = useRef(false);
  const voiceTranscriptionAbortRef = useRef<AbortController | null>(null);
  const voiceInputGenerationRef = useRef(0);
  const voiceSynthesisAbortRef = useRef<AbortController | null>(null);
  const spokenAudioRef = useRef<HTMLAudioElement | null>(null);
  const spokenUrlRef = useRef<string | null>(null);
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
      if (busy || conversationLoading) return;
      if (pendingSubmissionRef.current) {
        setError(
          "Resolve the pending message before switching conversations.",
        );
        return;
      }

      invalidateVoiceInput();
      stopVoicePlayback();
      if (pollRef.current) clearTimeout(pollRef.current);
      stickToBottomRef.current = true;
      setConversationLoading(true);
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
    [busy, conversationLoading, pollUntilTaskSettles, refreshConversation],
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
      voiceInputGenerationRef.current += 1;
      voiceTranscriptionAbortRef.current?.abort();
      voiceTranscriptionAbortRef.current = null;
      voiceDiscardRecordingRef.current = true;
      mediaRecorderRef.current?.stop();
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      voiceSynthesisAbortRef.current?.abort();
      spokenAudioRef.current?.pause();
      if (spokenUrlRef.current) URL.revokeObjectURL(spokenUrlRef.current);
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

  function stopVoicePlayback() {
    voiceSynthesisAbortRef.current?.abort();
    voiceSynthesisAbortRef.current = null;
    spokenAudioRef.current?.pause();
    spokenAudioRef.current = null;
    if (spokenUrlRef.current) {
      URL.revokeObjectURL(spokenUrlRef.current);
      spokenUrlRef.current = null;
    }
    setVoiceState((current) => (current === "speaking" ? "idle" : current));
  }

  async function speakReply(reply: string): Promise<void> {
    if (!reply.trim()) return;
    stopVoicePlayback();
    setVoiceState("speaking");

    const controller = new AbortController();
    voiceSynthesisAbortRef.current = controller;

    try {
      const response = await fetch("/api/voice/synthesize", {
        method: "POST",
        headers: {
          ...WORKSPACE_HEADERS,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text: reply, language: "auto" }),
        signal: controller.signal,
      });
      if (voiceSynthesisAbortRef.current !== controller) return;
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail =
          body && typeof body.detail === "string"
            ? body.detail
            : `HTTP ${response.status}`;
        throw new Error(detail);
      }

      const blob = await response.blob();
      if (voiceSynthesisAbortRef.current !== controller) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      spokenUrlRef.current = url;
      spokenAudioRef.current = audio;

      const finish = () => {
        if (spokenAudioRef.current !== audio) return;
        spokenAudioRef.current = null;
        if (spokenUrlRef.current === url) {
          URL.revokeObjectURL(url);
          spokenUrlRef.current = null;
        }
        if (voiceSynthesisAbortRef.current === controller) {
          voiceSynthesisAbortRef.current = null;
        }
        setVoiceState("idle");
      };
      audio.onended = finish;
      audio.onerror = () => {
        finish();
        setError("voice_playback_failed");
      };
      await audio.play();
    } catch (voiceError) {
      const aborted =
        voiceError instanceof DOMException && voiceError.name === "AbortError";
      stopVoicePlayback();
      if (!aborted) {
        setError(
          voiceError instanceof Error ? voiceError.message : "voice_playback_failed",
        );
      }
    }
  }

  async function sendPrompt(
    rawPrompt: string,
    options: { speakResponse?: boolean } = {},
  ): Promise<void> {
    const prompt = rawPrompt.trim();
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

      if (turn.core_task && ACTIVE_TASK_STATES.has(turn.core_task.status)) {
        await pollUntilTaskSettles(id);
      }
      if (options.speakResponse) {
        void speakReply(turn.assistant_content);
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
    await sendPrompt(text);
  }

  async function startVoiceRecording(event: PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    if (
      busy ||
      conversationLoading ||
      voiceState === "recording" ||
      voiceState === "transcribing"
    ) {
      return;
    }

    stopVoicePlayback();
    voiceHeldRef.current = true;
    voiceDiscardRecordingRef.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);

    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        throw new Error("voice_recording_not_supported");
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!voiceHeldRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      const preferredTypes = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ];
      const mimeType =
        preferredTypes.find((candidate) => MediaRecorder.isTypeSupported(candidate)) ??
        "";
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      voiceChunksRef.current = [];

      recorder.ondataavailable = (dataEvent) => {
        if (dataEvent.data.size > 0) voiceChunksRef.current.push(dataEvent.data);
      };

      recorder.onerror = () => {
        voiceDiscardRecordingRef.current = true;
        stream.getTracks().forEach((track) => track.stop());
        mediaRecorderRef.current = null;
        mediaStreamRef.current = null;
        setError("voice_recording_failed");
        setVoiceState("idle");
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        mediaRecorderRef.current = null;
        mediaStreamRef.current = null;

        if (voiceDiscardRecordingRef.current) {
          voiceChunksRef.current = [];
          voiceDiscardRecordingRef.current = false;
          setVoiceState("idle");
          return;
        }

        const blob = new Blob(voiceChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        voiceChunksRef.current = [];
        if (blob.size === 0) {
          setVoiceState("idle");
          return;
        }

        const transcriptionGeneration = voiceInputGenerationRef.current;
        const transcriptionConversationId = activeConversationIdRef.current;
        const controller = new AbortController();
        voiceTranscriptionAbortRef.current?.abort();
        voiceTranscriptionAbortRef.current = controller;
        setVoiceState("transcribing");
        try {
          const response = await fetch("/api/voice/transcribe", {
            method: "POST",
            headers: {
              ...WORKSPACE_HEADERS,
              "Content-Type": blob.type.split(";", 1)[0] || "audio/webm",
            },
            body: blob,
            signal: controller.signal,
          });
          const result = await parseJson<{ text: string }>(response);
          if (
            controller.signal.aborted ||
            voiceInputGenerationRef.current !== transcriptionGeneration ||
            activeConversationIdRef.current !== transcriptionConversationId
          ) {
            return;
          }
          const transcript = result.text.trim();
          if (!transcript) throw new Error("voice_no_speech_detected");
          setVoiceState("idle");
          await sendPrompt(transcript, { speakResponse: true });
        } catch (voiceError) {
          const aborted =
            controller.signal.aborted ||
            (voiceError instanceof DOMException && voiceError.name === "AbortError");
          setVoiceState("idle");
          if (!aborted) {
            setError(
              voiceError instanceof Error
                ? voiceError.message
                : "voice_transcription_failed",
            );
          }
        } finally {
          if (voiceTranscriptionAbortRef.current === controller) {
            voiceTranscriptionAbortRef.current = null;
          }
        }
      };

      recorder.start(250);
      setVoiceState("recording");
    } catch (voiceError) {
      voiceHeldRef.current = false;
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
      mediaRecorderRef.current = null;
      setVoiceState("idle");
      setError(
        voiceError instanceof Error ? voiceError.message : "voice_recording_failed",
      );
    }
  }

  function stopVoiceRecording() {
    voiceHeldRef.current = false;
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === "recording") recorder.stop();
  }

  function cancelVoiceRecording() {
    voiceHeldRef.current = false;
    voiceDiscardRecordingRef.current = true;
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === "recording") recorder.stop();
    else {
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
      voiceDiscardRecordingRef.current = false;
      setVoiceState("idle");
    }
  }

  function invalidateVoiceInput() {
    voiceInputGenerationRef.current += 1;
    voiceTranscriptionAbortRef.current?.abort();
    voiceTranscriptionAbortRef.current = null;
    cancelVoiceRecording();
  }

  function newConversation() {
    if (busy || conversationLoading) return;
    invalidateVoiceInput();
    stopVoicePlayback();
    if (pendingSubmissionRef.current) {
      setError("Resolve the pending message before starting a new conversation.");
      return;
    }
    if (pollRef.current) clearTimeout(pollRef.current);
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
            disabled={busy || conversationLoading}
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
                disabled={busy || conversationLoading}
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
            <a href="/talk" className="secondary-button">Try text beta</a>
            <button
              className="mobile-view-button"
              onClick={() => setActiveView("desk")}
            >
              Desk
            </button>
            <button
              className="mobile-history-button"
              onClick={() => setHistoryOpen(true)}
              disabled={busy || conversationLoading}
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
              disabled={busy || conversationLoading}
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
              disabled={busy || conversationLoading}
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
                  className={`voice-button ${voiceState === "recording" ? "recording" : ""}`}
                  onPointerDown={(event) => void startVoiceRecording(event)}
                  onPointerUp={stopVoiceRecording}
                  onPointerCancel={cancelVoiceRecording}
                  disabled={
                    busy ||
                    conversationLoading ||
                    voiceState === "transcribing"
                  }
                  aria-label="Hold to talk to Zelo"
                  title="Hold to talk"
                >
                  {voiceState === "recording"
                    ? "Release"
                    : voiceState === "transcribing"
                      ? "Transcribing…"
                      : voiceState === "speaking"
                        ? "Interrupt & talk"
                        : "Hold to talk"}
                </button>
                <button
                  className="send-button"
                  disabled={busy || conversationLoading || !text.trim()}
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
