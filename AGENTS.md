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

## Latest priority — Core first; UI parked (2 October 2026)

Ahmad reports that the UI still needs major work and directs Core development to
continue now, with UI redesign/integration revisited at the end. This supersedes
the earlier U2-first delivery plan and provisional design acceptance.

Preserve UI PR #61 as a draft and do not merge it automatically, even if CI passes.
Preserve `feat/v0.15-live-conversation-ui` at `95f35cf` and the independent original
design worktree. Do not delete UI code, chats, backups or local beta data.
The active work is v0.15 Browser Worker Core task binding after renderer
integration PR #66 at `f728df7`. Continue the established Core roadmap; later UI
rework requires fresh acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Current scope: v0.15 Browser Worker Core binding

Renderer integration PR #66 merged at `f728df7e87e3c4a9b5cdb23fc1e2c60281a810ef`.
Reviewed/tested head `ba188c3` passed exact-head CI (1538 tests, 2 installed-Codex
host-only skips), 121 focused renderer/protocol/harness tests, ten real controlled
Chromium/container/owner cases and independent Astra Medium static review. The
merge tree exactly matches that head. The existing installed-Codex host supplement
passed on the prior implementation checkpoint; do not describe duplicate stopped
local full-suite attempts as complete verification.

Read `docs/v0.15-browser-core-binding.md` before implementation. This branch binds
the qualified renderer to Core task/worker/Agent identity, migration guards,
action hashing, retry, claim and Runner. Default state remains disabled. Trusted
operator configuration must establish verified artifacts/image/profile/origins and
one canonical persistent private admission directory before any worker is enabled.
Do not modify live Core settings/databases, install services, publish a release or
activate the production Browser Worker in this development slice.

Reuse qualified broker/renderer/owner/admission modules through a narrow adapter.
No raw CDP, arbitrary evaluation/navigation/interaction, custom flags/headers/cookies,
credentials, caller-selected URL/image/mount/profile, screenshots or downloads.
Keep native browser sandboxing, network-none, private profile/pipe, nonroot, NNP,
cap-drop ALL, cgroups and truthful cleanup. Unknown cleanup stays fenced; retry,
restart, TTL and stale PID inference cannot reconcile a marker automatically.

Work in this dedicated worktree. Preserve original UI worktrees and parked draft
PR #61, beta conversations/data and published v0.14.0. Use Astra Medium. Preserve
existing API/Research/Python authority, Agent hierarchy and canonical v0.16/v0.17/
v0.18 ordering. No default AgentSpec, new department or Mind routing in this slice.
Require focused tests, full pytest, independent exact-head review and exact-head
CI before merging runtime changes. Docker host tests remain explicit opt-ins;
automated CI is offline and deterministic. Separate static, fixture and actual
host evidence. Read `docs/v0.15-browser-renderer-integration.md` for qualified scope
and trust/availability limitations.

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
