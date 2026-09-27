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

export type ProjectStatus = "active" | "paused" | "completed" | "archived";

export type Project = {
  id: string;
  name: string;
  summary: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
};

export type ProjectPage = {
  items: Project[];
  limit: number;
  offset: number;
  total: number;
};

export type GoalStatus = "active" | "completed" | "cancelled";

export type GoalSpec = {
  objective: string;
  success_criteria: string[];
  constraints: string[];
};

export type Goal = {
  id: string;
  project_id: string;
  title: string;
  spec: GoalSpec;
  status: GoalStatus;
  created_at: string;
  updated_at: string;
};

export type GoalPage = {
  items: Goal[];
  limit: number;
  offset: number;
  total: number;
};

export type CampaignStatus = "active" | "paused" | "completed" | "cancelled";

export type Campaign = {
  id: string;
  goal_id: string;
  title: string;
  summary: string;
  status: CampaignStatus;
  task_count: number;
  created_at: string;
  updated_at: string;
};

export type CampaignPage = {
  items: Campaign[];
  limit: number;
  offset: number;
  total: number;
};

export type CampaignEvent = {
  id: string;
  campaign_id: string;
  sequence: number;
  task_id: string | null;
  event: string;
  created_at: string;
  details: Record<string, unknown>;
};

export type CampaignEventPage = {
  items: CampaignEvent[];
  limit: number;
  offset: number;
  total: number;
};

export type CampaignBudget = {
  campaign_id: string;
  resource: string;
  limit_units: number;
  spent_units: number;
  reserved_units: number;
  available_units: number;
  updated_at: string;
};

export type CampaignBudgetPage = {
  items: CampaignBudget[];
  limit: number;
  offset: number;
  total: number;
};

export type CampaignSchedule = {
  id: string;
  campaign_id: string;
  conversation_id: string;
  prompt: string;
  status: "active" | "completed" | "cancelled";
  next_run_at: string | null;
  interval_seconds: number | null;
  max_runs: number;
  run_count: number;
  last_run_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type CampaignSchedulePage = {
  items: CampaignSchedule[];
  limit: number;
  offset: number;
  total: number;
};

export type CampaignOverview = {
  campaign: Campaign;
  events: CampaignEventPage;
  budgets: CampaignBudgetPage;
  schedules: CampaignSchedulePage;
};
