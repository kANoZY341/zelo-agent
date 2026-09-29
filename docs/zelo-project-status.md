# Zelo live project status

Verified: 29 September 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Published release

- Zelo Core v0.12.0 — Durable Goals and Bounded Autonomy is published.
- Release commit:
  `9f4d1c76b555fdca912e47ec8f5f90efd31217fb`.
- Release tag: `v0.12.0`.
- GitHub Release published on 29 September 2026.

## v0.12 released behavior

The v0.12 implementation is complete and released from the commit above.

The milestone now includes:

- persistent Talk-to-Zelo conversation and Core task projection;
- Project/Goal/Campaign ownership and durable operational state;
- Campaign leases, checkpoints, shared budgets, cancellation and recovery;
- bounded scheduler and Mind wake cycles;
- bounded autonomous continuation with terminal-task acceleration plus cadence fallback;
- the fixed Research specialist with narrow read-only evidence retrieval;
- Core-backed Workspace project/progress views and Workspace hardening;
- local push-to-talk voice through the existing conversation/action contract.

Relevant merged v0.12 work includes PRs #28, #30, #31, #32, #33, #34, #36, #37,
and #39.

PR #39's final reviewed head
`3857fda12eb0079bdcc3204ca7b106414fadf96d` passed the full CI gate with
`776 passed, 2 skipped`. The connected Codex review reported no major issues
after its findings were addressed.

v0.12 does not grant autonomous merge/release, approval bypasses, unrestricted
worker authority, a second scheduler authority, or self-created continuation
schedules.

## Active increment: v0.13 Worker/model performance and cost evidence

The third v0.13 memory evaluation/contamination slice merged to `main` at:

`0ac35db90445a0a97201d584710faed90b7b6633`

The active implementation branch is:

`feat/v0.13-performance-cost-evidence`

This fourth v0.13 slice adds read-only evidence derived from existing Core state:

- Zelo Mind turn model identity and reported token usage;
- model-task attempt identity, outcome, duration and reported token usage;
- developer worker identity/version/config, outcome, duration and reported usage;
- independent reviewer worker evidence when already persisted in developer results;
- per-model and per-worker summaries;
- explicit succeeded/failed/other outcome counts;
- bounded source records with exact IDs;
- an authenticated `GET /performance/evidence` surface;
- explicit monetary-cost status when current providers/workers report no price.

The evidence layer does **not** estimate token prices or invent monetary spend.
Current sources expose usage/timing but not monetary billing data, so monetary cost
is reported as unavailable/not reported.

The slice does not change worker/model routing, execution authority, approvals,
merge/release policy, or memory retention.

## Remaining v0.13 slice

After performance/cost evidence is verified:

1. storage abstraction for future Postgres/Supabase/vector backends.

Do not advance into v0.14 Agent Factory work from this branch.
