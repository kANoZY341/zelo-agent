# Early Zelo Chat increment

This increment is the first post-v0.11 step on the canonical path to v0.12.

## Goal

Prove one real path:

```text
Talk to Zelo
  -> Zelo Mind reasons and replies
  -> optional typed Core task proposal
  -> Zelo Core validates/submits it
  -> existing runner/approval boundaries execute it
  -> the conversation projects the authoritative task status/result
```

This is conversation and workspace plumbing, not v0.12 autonomous wake cycles,
campaigns, scheduling, v0.13 memory, v0.14 Agent Factory, or v0.16 channels.

## Core-owned contract

Zelo Mind may either answer directly or request one typed Core task. The model
never receives a TaskService callable or direct execution authority. Its JSON
action is Pydantic-validated before Core sees it.

The early allowlist is intentionally narrower than the complete task API:
`echo`, `text_stats`, `tool_call`, `agent_run`, `self_improvement_run`,
and `developer_run`. Promotion, merge, release, and approval decisions are not
part of the Mind output schema.

Developer and later sensitive work still enters the existing approval system.
Conversation does not create a parallel permission model.

## HTTP surface

All conversation routes require the existing operator bearer token.

- `POST /conversations` — create one persistent Zelo conversation.
- `GET /conversations` — list persisted conversations.
- `GET /conversations/{id}` — load the thread plus current linked Core task projections.
- `POST /conversations/{id}/turns` — send one text turn to Zelo Mind.

`Idempotency-Key` is required on every turn. The validated model action is
committed to the conversation before Zelo submits any Core task. If the process
stops after Core submission but before the turn is linked, retrying the same
turn key reuses the persisted action and the existing idempotent Core task; the
model is not called again.

Conversation listing returns summaries rather than complete threads.
`GET /conversations/{id}` accepts bounded `turn_limit` and `turn_offset`
pagination. Model generation queries at most the bounded recent-history window
and then applies the character budget.

Linked Core tasks use a bounded projection in conversation responses and Mind
context: current state/result/error, aggregate attempt and approval counts, and
only the latest attempt/approval status. Full attempt and approval histories
remain available through the canonical task API rather than being embedded in
each conversation turn.

Mind model generation has process-wide admission control, independent of
conversation ID. The operator settings `ZELO_MIND_MAX_CONCURRENCY` and
`ZELO_MIND_ADMISSION_TIMEOUT_SECONDS` bound active provider calls and how long
another conversation waits for capacity.

## Workspace boundary

The repository workspace decision remains authoritative: first prove the complete
conversation -> Core task -> result path, then choose the exact Mission Control,
Agent Town, hybrid, or Zelo-owned UI adaptation. The UI is always a projection
over these Core contracts; it may not create a second task, approval, scheduler,
memory, or authority system.
