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

## Release preparation

The release-prep slice is intentionally non-functional:

1. bump runtime/package-facing version metadata to `0.12.0`;
2. align README, roadmap and live-status documentation with the implemented state;
3. run diff, Ruff and full pytest gates;
4. run connected Codex review;
5. merge only after those gates are clean;
6. publish/tag `v0.12.0` from the resulting main commit.

## Next milestone: v0.13 — Memory, context and evidence

The first v0.13 slice should add typed durable memory records with:

- explicit personal, project and agent scopes;
- provenance;
- timestamps and optional expiry;
- correction and supersession links;
- clear separation from operational task/campaign state.

Later v0.13 slices add bounded context assembly and retrieval, evaluation cases,
worker/model performance and cost evidence, and a storage abstraction suitable
for later backend swaps.

Do not begin v0.14 Agent Factory work as part of the memory foundation. The
parked `feat/v1.0-agent-factory` branch remains a reference only.
