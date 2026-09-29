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

## Active increment: v0.14 Agent lifecycle and project binding

The v0.14 registry foundation is merged through PR #47 at:

`8609ee583513f24d312785b7158a90d40a8458ef`

The active branch is:

`feat/v0.14-agent-lifecycle-project-binding`

This second bounded Agent Factory slice turns trusted AgentSpec templates into
durable Core-owned instances without adding generic agent execution.

Current slice behavior:

- create a project-bound instance from only a trusted active `agent_id` and
  existing active `project_id`;
- persist the exact AgentSpec snapshot, agent version and registry fingerprint;
- keep agent identity, spec snapshot and Project binding immutable;
- explicit `active` / `paused` / terminal `retired` lifecycle;
- fail closed when a stale persisted registry/spec snapshot attempts to resume;
- append-only lifecycle event history with database-enforced sequence/order rules;
- database-enforced valid lifecycle transitions and no instance deletion;
- per-Project live-instance limits checked inside the serialized write transaction;
- bounded read-only memory context composed only from the owning Project scope and
  stable specialist Agent scope;
- no implicit personal-memory, other-Project or other-Agent contamination.

Migration `0020_agent_instances` adds `agent_instances` and
`agent_instance_events`.

Authenticated management/inspection routes are under `/agents`. They do not
execute Coding, Research or worker tasks in this slice.

This slice does not add generic Agent Factory task execution, parent-child
delegation, recursive subagents, manager runtime behavior, agent-to-agent
messaging, dynamic/model-authored AgentSpecs, automatic memory writes, new tools,
new worker authority or approval/budget/promotion/release bypasses.

## v0.14 planned continuation

After lifecycle/project binding is verified and merged:

1. explicit bounded parent-child delegation contracts;
2. Project/Campaign budget and depth binding for delegation;
3. Coding/Research execution through generic agent identity snapshots;
4. bounded subagent creation under Core-owned limits;
5. activation/evaluation gates before additional specialist templates.

Every slice must preserve Core task authority, ToolPolicy, Worker Supervisor,
approvals, budgets, audit, promotion and release boundaries.
