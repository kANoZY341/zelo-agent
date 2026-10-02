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

## Research Worker merged

Research Worker is merged through PR #58 at:

`c16f21381a2316435091f35faa82b609534cefa3`

It provides the trusted `research_core` backend, Research AgentSpec v2, capability
v2 worker subject, exact persisted selection, claim/Runner revalidation and unchanged
workspace-read/no-network evidence retrieval.

## Python Worker merged

Python Worker is merged through PR #59 at:

`c8aab211b52cbc576d8457b61d01134fcab645e5`

It provides the trusted `python_core` backend, isolated-compute authority, migration
`0027_python_worker_selection`, exact persisted selection, claim/Runner
revalidation, and bounded no-workspace/no-network Python computation.

## API Worker merged

API Worker is merged through PR #60 at
`8d33d563070c58c487dd06e91fd9af617744dab3`. Its reviewed JSON-only HTTPS
authority and execution identity remain unchanged by UI integration.

## Active increment: v0.15 live conversation UI

Branch: `feat/v0.15-live-conversation-ui`. Ahmad accepted the conversation
redesign and refined Z mark on 2 October 2026 and authorized import.

The accepted presentation is imported onto current Core without importing its
old prototype backend. `/talk` uses the existing authenticated server proxies
for text, durable Core history and task/result projection. `/` remains fallback.
Unsupported live voice, uploads and approval actions are disabled explicitly.

The next user-facing gate is U2 in `zelo-live-ui-delivery-plan.md`, not completion
of Browser Worker or v0.18. The implementation has 27 passing host Workspace
tests, passing typecheck/build, and a local beta backed by an isolated database.
Real-model acceptance, independent review, exact-head CI and production-data
rollout are separate gates. Existing user history has not been moved.

## v0.15 continuation

Browser Worker qualification remains on its separate branch. Continue it after
the live UI first-use checkpoint, with explicit capability, authority, network,
filesystem and credential boundaries; do not delay text-beta use behind it.
