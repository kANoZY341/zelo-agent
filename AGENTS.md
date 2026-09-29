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

## Current scope: v0.14 Agent registry foundation

Zelo Core v0.13.0 is published from
`adc43d856a29fdcacdba80a273566aefaa0b98f0`.

The active branch is:

`feat/v0.14-agent-registry-foundation`

This first v0.14 slice establishes trusted agent and department identity
contracts before generic specialist execution is enabled.

Rules for this slice:

- define frozen, versioned `AgentSpec` and `DepartmentSpec` contracts;
- keep agent IDs, memory namespaces, task/tool upper bounds and worker
  requirements canonical and deterministic;
- keep worker requirements vendor-neutral; never name concrete workers, models,
  executables or credentials in generic agent contracts;
- make registry ordering and fingerprinting deterministic;
- fail closed for unknown, inactive or invalidly assigned agents/departments;
- validate active task/tool references against trusted Core surfaces at startup;
- describe existing Coding and Research specialists without replacing their
  current execution paths yet;
- do not add a new task kind or database migration in this slice;
- do not add dynamic prompt-created agents, persisted agent instances, recursive
  subagents, new tools or new worker authority;
- do not let Agent Factory bypass Core tasks, ToolPolicy, Supervisor selection,
  approvals, budgets, audit, promotion, merge or release boundaries;
- inspect the parked `feat/v1.0-agent-factory` branch for compatible patterns
  only; do not merge it wholesale;
- run diff check, Ruff lint, Ruff format and the full pytest suite;
- require independent/adversarial review before merge.

After this slice, continue v0.14 with persisted lifecycle/project binding and
bounded parent-child delegation before enabling generic subagent creation.

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
