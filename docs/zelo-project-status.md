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

## Active increment: v0.14 Organizational hierarchy

The first four Agent Factory slices are merged:

- PR #47 → `8609ee583513f24d312785b7158a90d40a8458ef`;
- PR #48 → `8259141834869c09241dc22ea1fb5cbf89615565`;
- PR #49 → `d787c9cc86ea8ad680d2981e8d18b1e6b73d18a4`;
- PR #50 → `6554dd402ca4553efed6032f448bbdf8bb813239`.

The active branch is:

`feat/v0.14-organizational-hierarchy`

This fifth bounded slice persists the company reporting hierarchy without
widening task, worker or tool authority.

Current slice behavior:

- AgentSpec includes trusted `organizational_level`:
  `vp`, `director`, `manager`, or `specialist`;
- Coding and Research remain Specialists;
- durable organization units model divisions, departments and teams;
- durable assignments enforce Zelo CEO -> VP -> Director -> Manager -> Specialist;
- Managing Director and Zelo CEO remain reserved principals, not AgentInstances;
- assignment level must exactly match the holder's trusted AgentSpec;
- reporting assignments and temporary task delegations remain separate graphs;
- active leaders/reporting parents and unit ownership are validated;
- assignments are immutable historical records and cannot be deleted;
- AgentInstances cannot pause/retire while actively assigned;
- authenticated organization routes expose units, assignments and the canonical
  organization snapshot;
- migration `0023_organizational_hierarchy` enforces the same invariants in SQLite.

This slice does **not** automatically create agents, widen tools/workers based on
title, merge reporting with delegation, add new task kinds, write memory
automatically, or bypass approvals/budgets/scheduler/promotion/merge/release.

## Canonical organizational authority update

The 1 October 2026 operator decision establishes the company hierarchy:

```text
Human Managing Director
  -> Zelo Group CEO
    -> VP
      -> Director
        -> Manager
          -> Specialist Agent
            -> Worker
              -> Tool
```

The Managing Director and Zelo CEO are reserved principals. Divisions,
departments and teams are containers, not authority levels. Durable reporting
relationships are separate from temporary task-delegation contracts.

Canonical details live in `docs/zelo-organizational-authority-model.md`.

## v0.14 planned continuation

After organizational hierarchy is verified and merged:

1. add hierarchy-aware bounded child-agent creation behind explicit
   organization/delegation/depth/budget contracts;
2. add activation/evaluation gates for executive and specialist templates;
3. complete v0.14 release preparation.

Every slice must preserve Core task authority, ToolPolicy, Worker Supervisor,
approvals, budgets, audit, promotion and release boundaries.
