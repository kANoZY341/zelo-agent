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

## Active increment: v0.15 Python Worker

The active branch is:

`feat/v0.15-python-worker`

This fifth v0.15 slice adds bounded isolated Python compute behind the shared
Supervisor. Current implementation includes:

- `python_execution` capability for agent and worker subjects;
- `python_execution` skill with no new default AgentSpec or department;
- trusted `python_core` WorkerDescriptor;
- `isolated-compute-no-network-v1` WorkerAuthority;
- migration `0027_python_worker_selection`;
- exact persisted Python `WorkerSelectionSnapshot` and action-hash binding;
- fresh retry selection and claim/Runner fail-closed revalidation;
- a fixed Bubblewrap Python sandbox with no Zelo workspace, network or credentials;
- read-only empty workspace/tmp mounts, no shell binary and no package install surface;
- memory, CPU, process, open-file, file-size and output limits bound into authority policy.

Live sandbox probes and automated tests prove workspace/tmp writes, process creation,
shell execution and outbound network access are denied while standard-library compute
remains available.

## v0.15 Python Worker boundaries

This slice does **not** add workspace access, public web/API access, credentials,
Shell Worker authority, Browser/Computer control, arbitrary executables, package
installation or Zelo Core internals to submitted Python code.

## v0.15 continuation

After Python Worker passes full verification and merges, introduce the next bounded
worker surface independently with explicit capability, skill, authority, network,
filesystem, credential and approval boundaries.
