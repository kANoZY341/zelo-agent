# Zelo live project status

Verified: 29 September 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Published release

- Zelo Core v0.11.0 — Self-Improvement Seed remains the latest published release
  until the v0.12 release-prep gate is merged and tagged.
- v0.11.0 release commit:
  `b2c4a7d13fd8e64c6badfbae89d2a29629fd3c12`.
- Release tag: `v0.11.0`.

## v0.12 release candidate — implementation complete

The v0.12 implementation is complete on `main`.

Current main commit:

`7f72f8191a1b235e683bde6eaeef65281495f04b`

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

## Active increment: v0.13 Memory foundation

Zelo Core v0.12.0 release preparation merged to `main` at:

`9f4d1c76b555fdca912e47ec8f5f90efd31217fb`

The active implementation branch is:

`feat/v0.13-memory-foundation`

This first v0.13 slice is intentionally narrow:

- durable immutable memory records;
- explicit personal, project and agent scopes;
- typed fact, preference, decision and experience records;
- provenance metadata;
- optional expiry;
- correction and supersession through append-only replacement records;
- authenticated create/get/list Core APIs;
- no automatic memory writes from conversations;
- no memory injection into Mind context yet;
- no embeddings or vector database;
- no Agent Factory or new execution authority.

The GitHub `v0.12.0` tag/release publication remains a separate release
operation. Repository version metadata is already `0.12.0`.

## Later v0.13 slices

After the foundation is verified:

1. bounded scope-aware retrieval and context assembly;
2. correction/expiry-aware memory selection for Mind;
3. evaluation cases for memory quality and contamination boundaries;
4. worker/model performance and cost evidence;
5. storage abstraction for future backend swaps.

Do not advance into v0.14 Agent Factory work from this branch.
