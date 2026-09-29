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

## Current scope: v0.14 Parent-child delegation contracts

Zelo Core v0.13.0 is published from
`adc43d856a29fdcacdba80a273566aefaa0b98f0`.

Merged v0.14 slices:

- registry foundation: PR #47, merge `8609ee583513f24d312785b7158a90d40a8458ef`;
- agent lifecycle and project binding: PR #48, merge
  `8259141834869c09241dc22ea1fb5cbf89615565`.

The active branch is:

`feat/v0.14-parent-child-delegation`

This third v0.14 slice establishes explicit bounded parent-child delegation
contracts without enabling generic child-agent execution.

Rules for this slice:

- persist immutable parent/child delegation contracts;
- require both instances to be active, current-registry and Project-bound;
- keep every delegation inside the same Project and an active Campaign;
- require explicit parent `allow_subagents` authority and `max_subagents` capacity;
- narrow delegated task kinds to both parent and child allowed-task surfaces;
- require declared child capabilities before delegation;
- bind every delegation to an existing reserved Campaign budget owned by the parent;
- enforce one active parent per child;
- enforce bounded inherited depth with a hard maximum of four;
- reject cycles at the service and database boundaries;
- make delegation revocation terminal and preserve immutable contract history;
- release unused reserved budget through the existing Campaign control authority;
- do not execute child tasks, create child instances automatically, recurse from
  model output, or add agent-to-agent messaging in this slice;
- do not add new tools, worker authority, scheduler authority, automatic memory
  writes or approval/budget/promotion/release bypasses;
- run diff check, Ruff lint, Ruff format and the full pytest suite;
- require independent/adversarial review before merge.

After this slice, route the existing Coding/Research specialist execution through
generic agent identity snapshots before enabling bounded subagent creation.

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
