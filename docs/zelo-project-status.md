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

## Active increment: v0.14 Generic agent execution identity

The first three Agent Factory slices are merged:

- PR #47 → `8609ee583513f24d312785b7158a90d40a8458ef`;
- PR #48 → `8259141834869c09241dc22ea1fb5cbf89615565`;
- PR #49 → `d787c9cc86ea8ad680d2981e8d18b1e6b73d18a4`.

The active branch is:

`feat/v0.14-agent-execution-identity`

This fourth bounded slice connects persisted Agent Factory identity to the
existing trusted Coding and Research task paths.

Current slice behavior:

- authenticated `POST /agents/{instance_id}/tasks` submits only
  `developer_run` or `research_run`;
- callers provide specialist work and an optional existing delegation ID, never
  worker IDs, model IDs, AgentSpec snapshots or authority metadata;
- Core constructs and persists an exact immutable execution binding containing
  the AgentSpec snapshot, registry fingerprint, Project and optional delegation;
- the binding is included in request idempotency and task action hashes;
- approval, retry and claim boundaries revalidate current active agent identity;
- delegated child tasks require the exact still-active delegation, same Campaign,
  current parent/child identities and still-reserved parent-owned budget proof;
- Coding capability routing comes from its worker-neutral AgentSpec requirement;
- Coding's independent-review requirement fails closed when no eligible reviewer
  exists;
- Research keeps the existing bounded read-only specialist runtime;
- migration `0022_agent_execution_identity` adds the immutable task binding and
  database identity/kind/delegation guards while preserving legacy unbound tasks.

This slice does **not** add a new Core task kind, automatically create child
instances, enable recursive/model-authored subagents, widen tools/workers,
write memory automatically, or bypass approvals, budgets, scheduler, promotion,
merge or release authority.

## v0.14 planned continuation

After generic execution identity is verified and merged:

1. add bounded child-agent creation behind explicit parent/delegation/depth/budget
   contracts;
2. add activation/evaluation gates for AgentSpec/DepartmentSpec rollout;
3. complete v0.14 release preparation.

Every slice must preserve Core task authority, ToolPolicy, Worker Supervisor,
approvals, budgets, audit, promotion and release boundaries.
