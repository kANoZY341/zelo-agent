# Zelo live project status

Verified: 30 September 2026

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

## Active increment: v0.14 Agent registry foundation

The active branch is:

`feat/v0.14-agent-registry-foundation`

This is the first bounded slice of v0.14 Agent Factory. It introduces trusted,
versioned identity contracts before generic agent execution is enabled:

- frozen `AgentSpec` templates;
- frozen `DepartmentSpec` records;
- deterministic registry fingerprints;
- explicit `draft` / `active` / `retired` lifecycle state;
- manager/producer department assignments;
- canonical agent memory namespaces;
- worker-neutral role/capability requirements;
- task/tool/permission/procedure upper bounds;
- review and bounded lifecycle metadata;
- fail-closed activation validation;
- exact registry bindings for later execution snapshots;
- built-in Coding and Research templates describing the existing specialist
  runtimes without replacing their current task paths.

The parked `feat/v1.0-agent-factory` experiment was inspected for compatible
patterns only. It diverged from the v0.10 baseline and is not being merged.

This slice does not add a task kind, database migration, persisted agent
instances, recursive subagents, dynamic prompt-created agents, new tools,
worker authority, automatic memory writes, or agent-owned approval/release
authority.

## v0.14 planned continuation

After the registry foundation is verified and merged:

1. persisted agent instances and lifecycle transitions;
2. project binding and project-scoped memory assembly;
3. explicit bounded parent-child delegation contracts;
4. Coding/Research execution through the generic agent identity layer;
5. bounded subagent creation with budgets and depth limits;
6. activation/evaluation gates before adding more specialist templates.

Every slice must preserve Core task authority, ToolPolicy, Worker Supervisor,
approvals, budgets, audit, promotion and release boundaries.
