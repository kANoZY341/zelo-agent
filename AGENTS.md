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

## Current scope: v0.15 Capability Registry foundation

Zelo Core v0.14.0 — Agent Factory is published from
`3b87bbe4dc9956fbb8b11108faa911c4b23de5c9` with tag `v0.14.0`.

The active branch is:

`feat/v0.15-capability-registry-foundation`

This first v0.15 slice creates the trusted capability vocabulary that later
Browser, Computer, Python, API, Shell, Research, Media, skill, MCP/plugin and
swarm adapters must integrate through.

Rules for this slice:

- capability definitions are trusted immutable Core configuration, never model-authored;
- capability metadata describes satisfiable functions but never grants execution authority;
- validate active AgentSpec capabilities and WorkerRequirement capabilities against
  the registry;
- validate configured WorkerDescriptor capabilities before Supervisor routing;
- bind Capability Registry identity into Agent Factory activation policy/evidence;
- expose only an operator-authenticated read-only capability snapshot;
- describe only capabilities already present in the published v0.14 runtime;
- do not add browser, computer, Python, shell, API, media or external-network execution;
- do not add Skill Registry execution, MCP/plugin adapters, Ruflo or dynamic registration yet;
- do not add database migrations or runtime capability mutation;
- preserve Managing Director -> Zelo CEO -> VP -> Director -> Manager ->
  Specialist -> Worker -> Tool;
- preserve task, tool, worker, approval, scheduler, budget, promotion, merge and
  release authority boundaries;
- run diff check, Ruff lint, Ruff format, focused tests and the full pytest suite;
- require adversarial review and exact-head GitHub CI before merge.

After this slice, the next bounded v0.15 slice is the trusted Skill Registry.

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
