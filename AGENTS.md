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
- Zelo is the persistent personal AI. Zelo Mind is the reasoning/conversation
  identity; Zelo Core is the trusted authority and execution boundary. Models
  think, agents specialize, workers work, tools act, and Zelo decides.
- Map newly discovered frameworks/repositories to the canonical architecture
  before integrating them. Do not reshape Zelo around a third-party project.
- Reuse before rebuild: inspect mature open-source implementations first, verify
  license/provenance/security, and prefer narrow adapters or focused vendoring.
  Borrowed code must never bypass Zelo-owned permissions, approvals, secrets,
  routing, audit, memory, validation, or promotion boundaries.

## Current scope: v0.14 Agent lifecycle and project binding

Zelo Core v0.13.0 is published from
`adc43d856a29fdcacdba80a273566aefaa0b98f0`.

The v0.14 registry foundation is merged through PR #47 at:

`8609ee583513f24d312785b7158a90d40a8458ef`

The active branch is:

`feat/v0.14-agent-lifecycle-project-binding`

This second v0.14 slice persists Core-owned agent instances while preserving the
existing task, worker, approval, scheduler and release authority boundaries.

Rules for this slice:

- create agent instances only from trusted active AgentSpec registry bindings;
- accept only agent ID and existing Project ID from the operator at creation;
- persist exact AgentSpec snapshot, agent version and registry fingerprint;
- make agent identity, spec snapshot and Project binding immutable after creation;
- keep lifecycle explicit: active, paused and terminal retired;
- require exact current registry match before a paused instance can resume;
- allow stale instances to pause/retire so they can always be shut down safely;
- persist append-only lifecycle events with contiguous sequence numbers;
- enforce instance identity/lifecycle invariants in the database as well as the service;
- enforce per-Project live-instance limits inside the serialized write transaction;
- assemble bounded read-only memory context from Project scope + stable specialist
  Agent scope only;
- do not include personal memory or other Project/Agent scopes implicitly;
- do not add generic agent task execution, parent-child delegation, subagents,
  manager runtime behavior or agent-to-agent messaging in this slice;
- do not add dynamic/model-authored AgentSpecs, automatic memory writes, new
  tools, new worker authority or approval/budget/promotion/release bypasses;
- run diff check, Ruff lint, Ruff format and the full pytest suite;
- require independent/adversarial review before merge.

After this slice, continue v0.14 with explicit bounded parent-child delegation
and then route existing Coding/Research execution through generic agent identity
snapshots before enabling subagent creation.

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
