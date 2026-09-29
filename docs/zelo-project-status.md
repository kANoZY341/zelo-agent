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

## Active increment: v0.13 Memory retrieval and context

The first v0.13 Memory foundation slice merged to `main` at:

`e4172464d6279ff1cd7e639ce3c4bed1ef255c8f`

It established immutable typed personal/project/agent memory records, provenance,
expiry, correction/supersession chains, database append-only guards and
authenticated create/get/list APIs.

The active implementation branch is:

`feat/v0.13-memory-retrieval-context`

This second v0.13 slice adds:

- deterministic bounded lexical retrieval over active memory;
- explicit multi-scope retrieval for operator/API use;
- correction/expiry-aware selection;
- bounded snippets, candidate counts and item limits;
- personal-memory context assembly for Zelo Mind;
- provenance/scope-preserving `zelo_context` evidence;
- strict prevention of implicit project/agent memory injection;
- continued exclusion of context from Core task construction.

There are still no automatic memory writes, embeddings/vector database,
model-decided retention rules, Agent Factory, or new execution authority.

## Later v0.13 slices

After retrieval/context is verified:

1. baseline evaluation cases and memory contamination tests;
2. worker/model performance and cost evidence;
3. storage abstraction for future Postgres/Supabase/vector backends.

Do not advance into v0.14 Agent Factory work from this branch.
