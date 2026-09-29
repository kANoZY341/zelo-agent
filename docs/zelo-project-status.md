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

## Active increment: v0.13 Storage abstraction

The fourth v0.13 performance/cost evidence slice merged to `main` at:

`25bb6f1c5cba5da0b0a416cc465eced9058f966e`

The active implementation branch is:

`feat/v0.13-storage-abstraction`

This final v0.13 slice introduces a Zelo-owned storage boundary:

- `SessionStore` for Core service read/write sessions;
- `CoreStorage` for schema, runner ownership, descriptor and lifecycle;
- backend selection through `build_storage(settings)`;
- explicit `ZELO_STORAGE_BACKEND` configuration;
- the existing SQLite implementation as the only enabled v0.13 adapter;
- backend metadata declaring relational support and no vector-search support;
- tests proving Core service behavior through the interface rather than the
  concrete `Database` type.

SQLite semantics remain unchanged. The slice does not migrate data, add a
Postgres/Supabase driver, add vector retrieval, or change Core authority.

## v0.13 completion path

After storage abstraction is verified and merged:

1. prepare the v0.13 release;
2. run the full release gates;
3. publish v0.13;
4. only then start v0.14 Agent Factory.

Do not advance into v0.14 Agent Factory work from this branch.
