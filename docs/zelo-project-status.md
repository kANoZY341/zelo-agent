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

## v0.15 merged foundation

The first v0.15 Capability Registry foundation is merged through PR #55 at:

`4dcc5bf03dfe761d64df49f848762a163158d8df`

It provides immutable capability identity, agent/worker subject validation,
fail-closed AgentSpec/WorkerRequirement/WorkerDescriptor checks, activation
policy v2 evidence binding, and operator-authenticated read-only
`GET /capabilities`.

Capability metadata remains non-authoritative. Supervisor routing,
WorkerAuthority, ToolPolicy, task validation, approvals, budgets and audit remain
authoritative.

## Active increment: v0.15 Skill Registry foundation

The active branch is:

`feat/v0.15-skill-registry`

This second v0.15 slice adds trusted procedure metadata on top of capability
identities without adding an execution path.

Current implementation includes:

- immutable versioned `SkillSpec` definitions;
- deterministic immutable `SkillRegistry`;
- required capability identities;
- procedure, compatible task-kind and tool identities;
- draft/active/retired skill lifecycle;
- fail-closed validation against active agent capabilities;
- fail-closed validation against current task/tool surfaces;
- validation that each active default AgentSpec procedure has a compatible
  active trusted skill;
- operator-authenticated read-only `GET /skills`;
- explicit tests that `SkillRegistry` has no `run` or `execute` method and
  that `POST /skills` is rejected.

Initial built-ins describe only published Coding/Research procedures:
`core_implementation`, `core_review`, and `evidence_retrieval`.

## v0.15 Skill Registry boundaries

This slice does **not** add:

- skill execution or model-authored/dynamic skill registration;
- AgentSpec mutation from skills;
- Browser, Computer, Python, API, Shell, Research or Media worker execution;
- new task kinds or tools;
- external network, filesystem, subprocess or credential authority;
- MCP/plugin or Ruflo/swarm adapters;
- database migrations;
- new approval, scheduler, merge or release authority.

## v0.15 continuation

After the Skill Registry foundation passes full verification and merges:

1. introduce new worker/tool surfaces one bounded capability at a time;
2. require capability identity plus compatible trusted skill metadata;
3. separately enforce WorkerAuthority, ToolPolicy, sandbox/network/filesystem
   boundaries, approvals and audit for every execution surface;
4. keep focused tests, full pytest, adversarial review and exact-head CI as
   mandatory gates for each slice.
