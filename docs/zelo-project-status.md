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

## Active increment: v0.13 Memory evaluation and contamination boundaries

The second v0.13 bounded retrieval/context slice merged to `main` at:

`22f9ff24070ead799511657b656463bdaef54f2a`

The active implementation branch is:

`feat/v0.13-memory-evaluation`

This third v0.13 slice adds deterministic baseline evidence for memory quality
and contamination boundaries:

- exact expected/actual retrieval case results;
- precision/recall evidence tied to exact memory IDs;
- contamination and unexpected-hit counts;
- current-vs-corrected/expired selection checks;
- unrelated-query rejection;
- Unicode/Arabic recall;
- explicit project and agent scope isolation;
- automatic Mind-context personal-only isolation;
- memory-instruction trust-boundary checks;
- canonical project/runtime precedence over conflicting remembered context;
- irrelevant-memory non-injection.

The evaluation path performs no model calls and grants no new runtime authority.
There are still no automatic memory writes, embeddings/vector database,
model-decided retention rules, Agent Factory, or approval/merge/release bypasses.

## Later v0.13 slices

After evaluation/contamination evidence is verified:

1. worker/model performance and cost evidence;
2. storage abstraction for future Postgres/Supabase/vector backends.

Do not advance into v0.14 Agent Factory work from this branch.
