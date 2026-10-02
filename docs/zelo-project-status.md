# Zelo live project status

Verified: 2 October 2026

This file records current implementation state. Architecture and roadmap intent
live in `docs/zelo-canonical-architecture-roadmap.md`; workspace direction lives
in `docs/zelo-workspace-decision.md`.

## Published release

- Zelo Core v0.14.0 — Agent Factory is published.
- Release commit:
  `3b87bbe4dc9956fbb8b11108faa911c4b23de5c9`.
- Release tag: `v0.14.0`.
- GitHub Release published on 1 October 2026.
- Release preparation is PR #54.
- Independent release clone: 882 passed.
- Post-merge main CI run #36913099766 passed on the release commit.

## v0.14 released behavior

v0.14 added the bounded Agent Factory on top of v0.13 memory/context:
- trusted/versioned AgentSpec templates and deterministic activation evidence;
- durable project-bound AgentInstances with explicit lifecycle;
- scoped agent/project memory assembly;
- bounded parent-child delegation contracts;
- exact AgentSpec/delegation execution identity on Core tasks;
- durable divisions, departments, teams and reporting assignments;
- hierarchy-aware permanent child creation;
- depth, budget, compatibility and direct-report limits;
- fail-closed stale/paused/retired-agent and revoked-delegation handling.

The canonical authority chain is:

`Managing Director -> Zelo CEO -> VP -> Director -> Manager -> Specialist -> Worker -> Tool`

Organizational reporting and temporary task delegation remain separate graphs.
Titles never widen tool or worker authority.

Merged v0.14 implementation work is PRs #47 through #53.
Release preparation is PR #54.

## v0.15 merged foundations

Capability Registry is merged through PR #55 at:

`4dcc5bf03dfe761d64df49f848762a163158d8df`

Skill Registry is merged through PR #56 at:

`7e0936750967be79ec49ee5858a508935d7ad3e0`

Together they provide trusted capability identity, agent/worker subject checks,
activation policy v2 evidence binding, immutable skill/procedure compatibility,
and operator-authenticated read-only `GET /capabilities` and `GET /skills`.

Capability and skill metadata remain non-authoritative. Supervisor routing,
WorkerAuthority, ToolPolicy, task validation, approvals, budgets and audit remain
authoritative.

Generic Worker Registry is merged through PR #57 at:

`9d2e897a6c8bebf63fc52dec7b6442c24cc9f8aa`

## Active increment: v0.15 Research Worker

The active branch is:

`feat/v0.15-research-worker`

This fourth v0.15 slice turns the existing bounded ResearchRuntime into the first
separately registered non-developer worker behind the shared Supervisor.

Current implementation includes:

- `research` capability v2 for agent and worker subjects;
- Research AgentSpec v2 with an explicit `research` WorkerRequirement;
- trusted `research_core` WorkerDescriptor;
- workspace-read/no-network WorkerAuthority;
- migration `0026_research_worker_selection`;
- exact persisted Research `WorkerSelectionSnapshot` on Core tasks;
- worker identity bound into action hashing and task audit events;
- fresh Supervisor selection on retry;
- claim-time registry/requirement revalidation;
- Runner resolution of the exact selected Research backend;
- unchanged evidence/citation and read-only retrieval semantics.

## v0.15 Research Worker boundaries

This slice does **not** add:

- public web browsing or arbitrary network access;
- Browser, Computer, Python, API, Shell or Media workers;
- new Research tools or task kinds;
- write-capable filesystem authority;
- subprocess or credential authority;
- dynamic/model-authored workers, capabilities or skills;
- new approval, scheduler, merge or release authority.

The Research Worker continues to use only `repo_status`,
`list_workspace_files`, and `read_workspace_file` through ToolPolicy.

## v0.15 continuation

After this Research Worker slice passes full verification and merges:

1. choose the next bounded worker/tool surface independently;
2. add only the minimum capability/skill/authority contract required for it;
3. keep network, filesystem, subprocess and credential permissions explicit;
4. require focused tests, full pytest, adversarial review and exact-head CI for
   every slice.
