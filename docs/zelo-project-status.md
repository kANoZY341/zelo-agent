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

## Active increment: v0.13 Release preparation

The complete v0.13 implementation is merged to `main` at:

`fc244b59e0be1a10f838e668bd749aa7fb904a67`

The release-preparation branch is:

`chore/v0.13-release-prep`

v0.13 — Memory, Context and Evidence now includes:

- scoped personal, project and agent memory records;
- provenance, expiry, correction and supersession;
- deterministic bounded lexical retrieval with explicit scopes;
- bounded personal-memory injection into Zelo Mind with untrusted-instruction handling;
- deterministic memory evaluation and contamination-boundary coverage;
- worker/model performance and reported-cost evidence with no invented pricing;
- Zelo-owned storage contracts and backend selection;
- SQLite retained as the only enabled backend, with current transaction, migration,
  WAL, privacy and single-runner guarantees preserved.

Merged v0.13 work is PRs #41, #42, #43, #44 and #45.

The final storage slice was verified on head
`3a6ddbbddfe613862d043f0c9b1e497595ba2267` by CI #1590 before merge.

This release-preparation slice changes version metadata and documentation only.
It does not add runtime authority, automatic memory writes, embeddings/vector
search, a Postgres/Supabase backend, Agent Factory behavior, approval bypasses,
or new merge/release authority.

## v0.13 release gate

Before publishing `v0.13.0`:

1. diff check;
2. Ruff lint;
3. Ruff format;
4. full pytest;
5. independent/adversarial review;
6. merge the exact reviewed release-prep head;
7. tag and publish `v0.13.0` from the resulting `main` commit.

Only after v0.13.0 is published does the active roadmap move to v0.14 Agent Factory.
