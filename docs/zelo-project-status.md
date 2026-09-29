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

## Active increment: v0.14 Parent-child delegation contracts

The registry foundation and durable lifecycle/project-binding slices are merged:

- PR #47 → `8609ee583513f24d312785b7158a90d40a8458ef`;
- PR #48 → `8259141834869c09241dc22ea1fb5cbf89615565`.

The active branch is:

`feat/v0.14-parent-child-delegation`

This third bounded Agent Factory slice adds Core-owned delegation contracts while
keeping execution disabled.

Current slice behavior:

- immutable parent/child delegation identity and objective;
- same-Project and active-Campaign binding;
- active/current-registry parent and child requirements;
- explicit parent `allow_subagents` + `max_subagents` authority;
- delegated task kinds must fit both parent and child task surfaces;
- child capability requirements must be satisfied;
- one active parent per child;
- inherited depth contracts with hard maximum depth 4;
- service-level and database-level cycle prevention;
- Campaign budget reservation proof owned by the parent instance;
- idempotent creation with request fingerprinting;
- terminal revocation with unused reserved budget released through existing
  Campaign control authority.

Migration `0021_agent_delegations` adds `agent_delegations` and database
guards for immutability, valid relations, parent authority, budget proof, depth,
cycles and deletion prevention.

This slice does **not** execute delegated tasks, automatically create child
instances, allow recursive model-authored subagents, add agent-to-agent
messaging, add tools/worker authority, or bypass approvals, budgets, scheduler,
promotion, merge or release authority.

## v0.14 planned continuation

After bounded delegation contracts are verified and merged:

1. bind existing Coding/Research execution to persisted generic agent identity;
2. carry exact delegation identity into child task authority/evidence;
3. enable bounded subagent creation only behind explicit depth/budget limits;
4. add activation/evaluation gates before additional specialist templates.

Every slice must preserve Core task authority, ToolPolicy, Worker Supervisor,
approvals, budgets, audit, promotion and release boundaries.
