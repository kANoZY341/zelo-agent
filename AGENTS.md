# Zelo Agent development rules

## Scope and safety

- Work only inside `~/zelo/core` (the repository root). Ask the user before
  modifying anything outside it, using sudo, changing system configuration, or
  installing system-wide services.
- Bind development services to `127.0.0.1`. Ask before exposing services publicly,
  binding to other interfaces, or changing firewall/network settings.
- Never delete user data or repositories without explicit approval. Preserve
  `workspaces/`, `memory/`, and `secrets/` and their contents.
- Never commit API keys, tokens, credentials, or other secrets. Keep local secrets
  in ignored files; examples must contain placeholders only. Avoid logging secrets.
- Do not commit or push changes unless the user asks.

## Architecture

- Keep a modular architecture with replaceable, loosely coupled components.
- Avoid vendor lock-in. Future LLM integrations must go through a model-router
  abstraction so providers can be swapped without rewriting agents.
- Agents and workers must not receive unrestricted system access by default. Grant narrowly
  scoped capabilities and use the existing isolated sandbox/workspace boundaries for execution.
- Future sensitive, external, or destructive agent actions must pass through an
  explicit approval layer before execution.
- Keep a clear audit trail of meaningful actions and approvals where practical,
  without recording credentials or unnecessary private data.
- Do not over-engineer early versions or implement future components speculatively.
  Add orchestration, agents, storage, authentication, and other subsystems only
  when their scope is explicitly requested.

## Implementation and verification

- Write maintainable, typed Python with small functions and clear boundaries.
- Keep application assembly in `app/main.py`, HTTP routes in `app/api.py`, and
  environment-backed settings in `app/config.py`.
- Keep runtime dependencies minimal and separate test dependencies.
- Add automated tests for important behavior and regressions. Run relevant tests
  after changes; verify service changes locally and stop temporary servers.
- Update the README and credential-free `.env.example` when usage or settings
  change. Explain material decisions, test results, and unresolved errors.

## Canonical roadmap

- Treat `docs/zelo-canonical-architecture-roadmap.md` as the project north star
  and `docs/zelo-workspace-decision.md` as the current workspace decision.
  Feature designs may refine the active milestone but must not silently redefine
  Zelo's identity or reorder future milestones.
- Zelo is the persistent personal AI and Group CEO under the human Managing
  Director. Zelo Mind is the reasoning/conversation identity; Zelo Core is the
  trusted authority and execution boundary. Factory-managed authority descends
  VP -> Director -> Manager -> Specialist. Models think, agents specialize,
  workers work, tools act, and Zelo decides.
- Map newly discovered frameworks/repositories to the canonical architecture
  before integrating them. Do not reshape Zelo around a third-party project.
- Reuse before rebuild: inspect mature open-source implementations first, verify
  license/provenance/security, and prefer narrow adapters or focused vendoring.
  Borrowed code must never bypass Zelo-owned permissions, approvals, secrets,
  routing, audit, memory, validation, or promotion boundaries.

## Current scope: v0.15 Research Worker

Zelo Core v0.14.0 — Agent Factory is published from
`3b87bbe4dc9956fbb8b11108faa911c4b23de5c9` with tag `v0.14.0`.

Merged v0.15 foundations:

- Capability Registry: PR #55 at
  `4dcc5bf03dfe761d64df49f848762a163158d8df`;
- Skill Registry: PR #56 at
  `7e0936750967be79ec49ee5858a508935d7ad3e0`;
- Generic Worker Registry: PR #57 at
  `9d2e897a6c8bebf63fc52dec7b6442c24cc9f8aa`.

The active branch is:

`feat/v0.15-research-worker`

This fourth v0.15 slice moves existing bounded `research_run` execution behind
the shared Supervisor without widening the Research surface.

Rules for this slice:

- version the trusted `research` capability for both agent and worker subjects;
- make the default Research AgentSpec explicitly require role `research` and
  capability `research`;
- register one trusted `research_core` backend with workspace-read/no-network
  authority;
- persist the exact Supervisor `WorkerSelectionSnapshot` on each research task;
- bind Research Worker identity into action hashing, retry and audit evidence;
- revalidate the exact worker selection at claim time and fail closed if stale;
- Runner must invoke the exact registered backend selected for the task;
- preserve the existing ResearchRuntime, evidence/citation protocol and read-only
  ToolGateway policy;
- do not add public web browsing, API calls, shell, subprocess, computer control,
  write tools or credentials;
- do not add new task kinds, model authority, approval bypasses or AgentSpec
  mutation;
- preserve Managing Director -> Zelo CEO -> VP -> Director -> Manager ->
  Specialist -> Worker -> Tool;
- run diff check, Ruff lint, Ruff format, focused tests and full pytest;
- require adversarial review and exact-head GitHub CI before merge.

After this slice, the next v0.15 execution surface must be introduced separately
with its own capability, skill, worker authority and sandbox/network boundaries.

## Workspace decision

- The current workspace product direction is one Zelo experience with three
  connected views: Talk to Zelo, Company World, and Control Desk.
- The proof gate is complete. The selected direction is a Zelo-owned workspace
  with Mission-Control-derived Control Desk UX/components and a later
  Agent-Town-derived Company World.
- Mission Control and Agent Town are MIT-licensed reference/reuse sources, not
  Zelo's runtime or authority boundary. Preserve required attribution for copied
  or materially adapted code.
- UI code must remain a projection/adapter over canonical Core state. It must not
  create a parallel task, approval, scheduler, memory, or authority system.
