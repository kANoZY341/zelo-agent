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

## Current scope: Early Zelo Chat increment

The released base is Zelo Core v0.11.0. It includes durable tasks and attempts,
SQLite persistence/migrations, authentication, approvals/audit, local model
routing, typed native tools, bounded Agent and Developer runtimes, trusted
validation, independent review, promotion/merge/release boundaries, the Worker
Registry/Supervisor, enforceable worker authority, multi-worker delegation, and
the bounded v0.11 self-improvement seed.

The active increment is the first post-v0.11 Talk-to-Zelo path toward v0.12.
Add persistent text conversation for Zelo Mind and prove one conversation ->
typed Core task -> real task progress -> authoritative result projection flow.
Zelo Mind may discuss and plan, but model output must not receive execution
authority or bypass TaskService, approvals, policy, workers, validation, or audit.

Do not pull v0.12 durability/wake cycles/campaigns forward wholesale. Memory
remains v0.13, Agent Factory remains v0.14, broad tools/capabilities remain
v0.15, and WhatsApp/email/live voice remain v0.16. Mission Control and Agent
Town are references/candidates until the complete conversation-to-result flow
is proven as required by the workspace decision.

## Workspace decision

- The current workspace product direction is one Zelo experience with three
  connected views: Talk to Zelo, Company World, and Control Desk.
- Mission Control is a strong candidate/reference, not a mandatory dependency.
  Agent Town is a visual-world reference/adaptation candidate.
- Choose Mission Control, Agent Town, a hybrid, or a Zelo-owned workspace only
  after proving one complete conversation -> Core task -> progress -> approval ->
  verified result flow.
- UI code must remain a projection/adapter over canonical Core state. It must not
  create a parallel task, approval, scheduler, memory, or authority system.
