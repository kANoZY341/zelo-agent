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

## Current scope: Core v0.11 development

The released base is Zelo Core v0.10.0. It already includes durable tasks and
attempts, SQLite persistence/migrations, authentication, approvals/audit,
localhost model routing, typed native tools, a bounded Agent Runtime, an
approval-bound Developer Runtime, trusted validation, independent review,
promotion/merge/release boundaries, a Worker Registry/Supervisor, enforceable
worker authority, concurrency/recovery controls, and Codex, Claude Code and
OpenCode worker adapters.

The active milestone is v0.11 Self-Improvement Seed. Its scope is one bounded
cycle: trusted self-inspection -> one strict policy-checked proposal -> existing
approval-bound developer delegation -> validation/review -> evaluation -> stop.
v0.11 must not autonomously merge, tag, release, deploy, widen authority, or
start repeated background self-modification cycles.

Agent Factory work is intentionally deferred to the canonical v0.14 milestone.
After v0.11, the roadmap calls for an early workspace increment proving one
Talk-to-Zelo -> delegated work -> real progress -> verified result path. Mission
Control and Agent Town are references/candidates, not mandatory dependencies.
OpenClaw, Ruflo, ECC, Harness, OpenMAIC, Context Mode, Supabase and other
reference projects may be studied now but integrated only when their canonical
roadmap capability is active or an explicit operator decision changes scope.
