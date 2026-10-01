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

## Active increment: v0.15 Generic Worker Registry foundation

The active branch is:

`feat/v0.15-generic-worker-registry`

This third v0.15 slice separates trusted worker selection metadata from the
DeveloperWorker backend type without changing runtime execution.

Current implementation includes:

- PEP 695 generic `RegisteredWorker[Backend]`;
- generic `WorkerRegistry[Backend]`;
- generic `Supervisor[Backend]`;
- unchanged WorkerDescriptor and authority-profile storage;
- unchanged registry fingerprint and routing policy semantics;
- DeveloperRuntime explicitly typed as `WorkerRegistry[DeveloperWorker]`;
- an opaque-backend regression test proving selection does not invoke
  DeveloperWorker methods.

The current worker registry schema remains `zelo-worker-registry-v2` because
its serialized trusted descriptor/authority fingerprint is unchanged.

## v0.15 Generic Worker Registry boundaries

This slice does **not** add:

- Research, Browser, Computer, Python, API, Shell or Media workers;
- new task kinds, tools, capabilities, skills or AgentSpecs;
- new WorkerAuthority profiles or sandbox behavior;
- external network, filesystem, subprocess or credential authority;
- execution-interface unification;
- database migrations;
- new approval, scheduler, merge or release authority.

## v0.15 continuation

After this generic selection foundation passes full verification and merges:

1. add a separately bounded read-only Research Worker adapter behind Supervisor;
2. define its trusted descriptor, capability and skill compatibility;
3. preserve workspace-read/no-network authority and existing Research evidence
   semantics;
4. require focused tests, real-clone full pytest, adversarial review and
   exact-head CI before merge.
