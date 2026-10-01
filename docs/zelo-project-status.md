# Zelo live project status

Verified: 1 October 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Published release

- Zelo Core v0.13.0 — Memory, Context and Evidence is published.
- Release commit:
  `adc43d856a29fdcacdba80a273566aefaa0b98f0`.
- Release tag: `v0.13.0`.
- GitHub Release published on 30 September 2026 local time.
- Post-merge main CI #1601 passed.

## v0.13 released behavior

v0.13 added the durable context and evidence foundation on top of v0.12:

- scoped personal, project and agent memory records;
- provenance, expiry, correction and supersession;
- deterministic bounded lexical retrieval with explicit scopes;
- bounded personal-memory injection into Zelo Mind with untrusted-instruction handling;
- deterministic memory evaluation and contamination-boundary coverage;
- worker/model performance and reported-cost evidence with no invented pricing;
- Zelo-owned storage contracts and backend selection;
- SQLite retained as the enabled backend with current transaction, migration,
  WAL, privacy and single-runner guarantees preserved.

Merged v0.13 implementation work is PRs #41, #42, #43, #44 and #45.
Release preparation is PR #46.

v0.13 does not add automatic memory writes, embeddings/vector retrieval,
Postgres/Supabase storage, Agent Factory execution, approval bypasses or new
merge/release authority.

## Active increment: v0.14 Agent activation and evaluation gates

The first six Agent Factory implementation slices are merged:

- PR #47 → registry foundation;
- PR #48 → project-bound lifecycle and scoped memory;
- PR #49 → bounded parent-child delegation;
- PR #50 → generic Coding/Research execution identity;
- PR #51 → durable organizational hierarchy;
- PR #52 → hierarchy-aware permanent child creation,
  merge `91e23172a303786d02d196ed5c6c13cc80e9f217`.

The active branch is:

`feat/v0.14-agent-activation-evaluation`

This final bounded implementation slice adds deterministic activation evidence
before an active AgentSpec is allowed to enter the runtime.

Current behavior:

- active AgentSpecs are evaluated against available Core task kinds;
- declared Zelo tools must exist;
- worker-neutral WorkerRequirements must be satisfiable by configured enabled
  WorkerDescriptors;
- `developer_run` requires an explicit worker requirement;
- review-required templates must declare the developer/review contract;
- VP/Director/Manager templates must carry explicit bounded
  `organization_hire` authority;
- Specialist templates cannot carry permanent `organization_hire`;
- draft trusted AgentSpecs can be evaluated before their lifecycle is changed;
- evaluation evidence is deterministic and bound to policy version, registry
  version and an exact task/tool/worker surface digest;
- rejected active templates fail application startup;
- the current accepted report is inspectable through
  `GET /agent-factory/activation` with operator authentication.

This slice does **not** create new templates, run model judgment, perform worker
preflight execution, widen title-based authority, create children, or bypass
Core task/tool/worker/approval/budget/scheduler/promotion/release boundaries.

## v0.14 planned continuation

After this activation/evaluation slice is verified and merged:

1. prepare the v0.14.0 release only;
2. run exact-head release CI and independent/adversarial release review;
3. publish v0.14.0;
4. begin v0.15 only from the published v0.14 baseline.

Every release gate must preserve the Managing Director -> Zelo CEO -> VP ->
Director -> Manager -> Specialist hierarchy and the separation between durable
reporting and temporary delegation.
