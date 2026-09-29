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

## Current scope: v0.13 Storage abstraction

Zelo Core v0.12.0 is published from `9f4d1c76b555fdca912e47ec8f5f90efd31217fb`.
The first four v0.13 slices are merged on `main`:

- Memory foundation: `e4172464d6279ff1cd7e639ce3c4bed1ef255c8f`;
- bounded retrieval/context: `22f9ff24070ead799511657b656463bdaef54f2a`;
- memory evaluation/contamination: `0ac35db90445a0a97201d584710faed90b7b6633`;
- worker/model performance and cost evidence:
  `25bb6f1c5cba5da0b0a416cc465eced9058f966e`.

The active and final v0.13 slice is **storage abstraction**. Introduce a
Zelo-owned backend-neutral persistence contract while preserving current SQLite
behavior exactly.

Rules for this slice:

- Core services depend on a minimal transactional session-store contract rather
  than SQLite-specific implementation details;
- application assembly selects storage through a Zelo-owned factory;
- SQLite remains the only enabled backend in v0.13;
- preserve current SQLite privacy, WAL, foreign-key, transaction, migration and
  single-runner guarantees;
- configuration must fail closed rather than pretending an unimplemented
  Postgres, Supabase or vector backend exists;
- do not migrate data or add Postgres/Supabase/vector dependencies in this slice;
- do not change memory retrieval, automatic memory retention, routing, task,
  approval, scheduler, budget, merge or release authority;
- do not begin v0.14 Agent Factory work from this branch.

After this slice is verified and merged, v0.13 implementation is complete and
the next step is v0.13 release preparation.

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
