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
The active work is v0.15 qualified Shell operator/full application proof after Core
binding PR #71 at `fd686a9`. Continue the established Core roadmap; later UI
rework requires fresh acceptance. Do not spend this phase polishing, deploying or acceptance-testing UI.

## Current scope: v0.15 qualified Shell operator/application proof

Core binding PR #71 merged at `fd686a9044ddee01e1221d35117e72220c9af0ec`
from reviewed/tested head `484257bc40abd2c62b8372d892b3d7dfc1a7908f`.
Full CI passed 1,792 tests with two separately passed installed-Codex supplements.
255 focused regressions, four standalone supplemental/clean-source checks, nine
real Core/native cases under both normal and invalid ambient settings, seventeen
owned Shell cases and eight Browser lifecycle cases passed. Two negative controls
caught discarded script/validation identity. Astra Medium full review and exact-head
followup found no remaining blockers; merged/tested trees match. See
`docs/reviews/shell-core-binding-final-gates.md`.

Read `docs/v0.15-shell-operator-qualification.md` before implementation. Qualify
unmodified create_app/lifespan, authenticated in-process ASGI routes, background
Runner, persisted selection/spec/action/audit/evidence, readmission and application
shutdown in a private diagnostic instance. Use init-only Settings, a fixed diagnostic
token, private SQLite/admission/workspace, scheduler disabled and a rejecting counted
provider that verifies zero model requests. No listener or external network.

Real acceptance must use default qualified runtime/owner/native factories and
approved fixed sources/policy; no fake runtime standing in for native execution.
Prove disabled assembly and explicit enabled prerequisite/fence refusal without
resetting markers. Execute only fixed harmless scripts from the proof, not caller
script/path/runtime overrides. Preserve any deliberate private diagnostic fence.
No installed source mutation, arbitrary host process/filesystem surface, default
AgentSpec/Mind route, production acceptance, live settings/database change or release.

Retain qualified identity, script/spec/action hashes, one absolute deadline, no
workspace/writes/network/credentials/forks, bounded untrusted output and explicit
source/native/pin/durable cleanup. Unknown ownership remains retained/fenced; no
TTL, PID-age/error inference or reconciliation. Preserve approvals, scheduler,
budgets, audit, Agent WorkerRequirements and migrations. Do not change backend
semantics merely to make the proof pass; scope any discovered runtime fix explicitly.

Use this dedicated worktree and Astra Medium. Require offline focused tests,
normal/invalid-ambient actual application/native proofs, independent exact-head
static review, exact-head full CI and installed-Codex non-model supplements before
merge. Keep host/kernel/process visibility/operator/provenance assumptions explicit.
No system installs, sudo, services, public listeners, model requests, user data or
unrelated resource deletion. Production Browser and Shell remain disabled, v0.14.0
and parked UI #61 remain unchanged. Computer/Media and external adapter slices stay
separate; v0.16 channels -> v0.17 organizations -> v0.18 autonomy stays ordered.

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
