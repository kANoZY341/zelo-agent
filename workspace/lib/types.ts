export type TaskState =
  | "pending"
  | "awaiting_approval"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

export type TaskProjection = {
  id: string;
  kind: string;
  status: TaskState;
  created_at: string;
  updated_at: string;
  cancel_requested: boolean;
  result: Record<string, unknown> | null;
  result_truncated: boolean;
  error: string | null;
  attempt_count: number;
  latest_attempt_outcome: TaskState | null;
  approval_count: number;
  latest_approval_id: string | null;
  latest_approval_status: string | null;
  selected_alias: string | null;
  selected_model: string | null;
};

export type ConversationTurn = {
  id: string;
  number: number;
  user_content: string;
  assistant_content: string;
  selected_alias: string;
  selected_model: string;
  usage: Record<string, number | null>;
  core_task: TaskProjection | null;
  created_at: string;
};

export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turns: ConversationTurn[];
  turn_limit: number;
  turn_offset: number;
  turn_total: number;
};


export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turn_count: number;
};

export type ConversationPage = {
  items: ConversationSummary[];
  limit: number;
  offset: number;
  total: number;
};
