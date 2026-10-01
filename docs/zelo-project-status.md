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

## Active increment: v0.15 Capability Registry foundation

The active branch is:

`feat/v0.15-capability-registry-foundation`

This first v0.15 slice creates Zelo's trusted capability vocabulary before new
execution surfaces are introduced.

Current implementation on the branch includes:

- immutable versioned `CapabilitySpec` definitions;
- deterministic immutable `CapabilityRegistry`;
- explicit agent/worker subject compatibility;
- draft/active/retired capability lifecycle;
- fail-closed unknown, inactive and subject-mismatch validation;
- validation of active AgentSpec capabilities;
- validation of WorkerRequirement and configured WorkerDescriptor capabilities;
- Capability Registry identity bound into Agent Factory activation evidence;
- Agent Factory activation policy advanced to
  `zelo-agent-activation-policy-v2`;
- operator-authenticated read-only `GET /capabilities`;
- bounded project context now points at the active v0.15 roadmap section.

The first built-in registry describes only already-published v0.14 vocabulary:
`code_change`, `code_planning`, `code_review`, `evidence_synthesis`,
`research`, and `zelo_core_development`.

Capability metadata does not grant execution authority. Supervisor routing,
WorkerAuthority, ToolPolicy, task validation, approvals, budgets and audit remain
authoritative.

## v0.15 foundation boundaries

This slice does **not** add:

- Browser, Computer, Python, API, Shell, Research or Media worker execution;
- new task kinds or tools;
- external network, filesystem, subprocess or credential authority;
- Skill Registry execution;
- MCP/plugin adapters;
- Ruflo/swarm execution;
- dynamic/model-authored capability registration;
- database migrations;
- new approval, scheduler, merge or release authority.

## v0.15 continuation

After this Capability Registry foundation passes full verification and merges:

1. add the trusted Skill Registry on top of capability identities;
2. then introduce new worker/tool surfaces one bounded capability at a time;
3. keep every adapter behind Zelo-owned policy, authority and audit boundaries;
4. require focused tests, full pytest, adversarial review and exact-head CI for
   every slice.
