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

## Active increment: v0.14 Hierarchy-aware child creation

The first five Agent Factory slices are merged:

- PR #47 → `8609ee583513f24d312785b7158a90d40a8458ef`;
- PR #48 → `8259141834869c09241dc22ea1fb5cbf89615565`;
- PR #49 → `d787c9cc86ea8ad680d2981e8d18b1e6b73d18a4`;
- PR #50 → `6554dd402ca4553efed6032f448bbdf8bb813239`;
- PR #51 → `86315a5b61924b36cc04ae1e964ebe1a496ede0b`.

The active branch is:

`feat/v0.14-hierarchy-child-creation`

This sixth bounded Agent Factory slice adds the first Core-owned permanent
subordinate creation path under the canonical organization hierarchy.

Current slice behavior:

- parent must be active/current and have an active organization assignment;
- permanent creation requires explicit trusted `organization_hire` permission
  plus bounded subagent authority;
- only next-level creation is allowed:
  VP -> Director -> Manager -> Specialist;
- Specialist Agents cannot create permanent reports;
- target organization unit must remain within the parent's reporting scope;
- child AgentSpec must be trusted, active, current and match the next level;
- child remains in the same Project as the parent;
- active Campaign and exact parent-owned reserved budget proof are required;
- task kinds and required capabilities are narrowed against trusted parent/child
  AgentSpecs;
- nested delegation depth is inherited and bounded to four;
- AgentInstance + reporting assignment + task delegation + immutable creation
  receipt are persisted atomically;
- idempotent replay returns the same creation receipt;
- migration `0025_hierarchy_child_creation` protects receipt relationships,
  immutability and no-delete history.

This slice does **not** permit model-authored AgentSpecs, hierarchy jumps,
title-based worker/tool authority, unrestricted spawning, new Core task kinds,
automatic memory writes, or approval/budget/scheduler/promotion/release bypass.

## v0.14 planned continuation

After hierarchy-aware child creation is verified and merged:

1. add activation/evaluation gates for executive and specialist templates;
2. complete v0.14 release preparation;
3. publish v0.14 only after exact-head CI and release verification.

Every slice must preserve Core task authority, ToolPolicy, Worker Supervisor,
approvals, budgets, audit, promotion and release boundaries.
