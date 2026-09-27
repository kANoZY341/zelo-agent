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

## Current scope: v0.12 Push-to-talk voice

The released base remains Zelo Core v0.11.0. Early Zelo Chat, the Zelo-owned
workspace foundation, Project/Goal/Campaign ownership, Campaign controls, and the
bounded scheduler/Mind wake cycle are complete on main. PR #32 merged the
scheduler at `1ad8d7582713651dd5b2804ef88f8f78bba7f934` with green main CI.

The fixed Research specialist is complete on main at
`1fc2970f88fe205d7c2ee4ea5e8f87b160af305a`. Its design remains documented in
`docs/v0.12-research-specialist.md`.

PR #34 merged the v0.12 Workspace project/progress projection on main at
`247516866db4dd9906fe326bc302e99db7d6c658`.

PR #36 completed Workspace hardening on main at
`f4f9603d1feda8d4ecc96640b5538ecc0c72b306` with green Core and Workspace CI
and no known remaining review findings.

The active slice is **v0.12 Push-to-talk voice**. Voice is an I/O adapter only:
browser microphone -> local speech adapter -> transcript -> the same existing
conversation-turn endpoint -> assistant text -> local speech adapter -> playback.
Keep `ZELO_VOICE_TOKEN` server-side, bind speech services to loopback only, do
not persist raw audio, and do not create a voice-specific Mind, conversation,
task, approval, scheduler, memory, or authority path. STT/TTS providers must
remain replaceable so changing Zelo's voice does not affect Core or Mind.

Do not add project/campaign mutation controls, Company World runtime, public-web
search, broad worker capabilities, specialist memory, AgentSpec/DepartmentSpec
generalization, external messaging/call channels, or new merge/release authority
in this slice. Persistent memory remains v0.13, Agent Factory v0.14, broad
capabilities/external retrieval workers v0.15, and external channels v0.16.

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
