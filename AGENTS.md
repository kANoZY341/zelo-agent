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
- Keep reusable application assembly in `app/application.py`, the normal ASGI
  entry point in `app/main.py`, HTTP routes in `app/api.py`, and environment-backed
  settings in `app/config.py`. Explicitly configured factory imports must not
  instantiate default Settings or parse live environment/.env.
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
The active work is v0.15 Media fixed local-index qualification after PR84 merge
070f820bb90e493d2287ad6b0b96fd33e0dc4db6. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: second fixed local-index qualification gate

PR85 original60f0199 passed source/tests/CI but actual index1 refused changed
updates size. Preserve index-proof-qe5u08n9 SHA0030fb0a8a4e87aaa2d5f762b94b3a232f7f2a0925d3cf49fc0eb8b12fc27e3b.
Refreshed origin source1d533c35d38d6ce7d155b2de54275573e85061a9 and its actual
fourth18case proof received FULL source/actual acceptance. Read archived source
and final reviews in docs/reviews/media-origin-refresh-*.md.
New origin proof origin-proof-o5l0ydws SHAfaab51dce2a95073a251049a559ceddb027ff01a36f3219e5236ca0c1bd6cd52;
all18cases and cleanup passed,36entries/772054bytes,receipt binds pending proof.
Origin4/4 EXHAUSTED: no fifth run/reset. Version2/4,loader4/4 unchanged.

Only accepted signed raw-index declarations have now been promoted into the
wrapper: updates7375495/SHA1a82e1177b4a1eefab5f4a985c79911fac514e7ad6c5c31ebf780c1983663185;
noble/security declarations unchanged. Exact8package/version/amd64 targets and
pure selector unchanged. Metadata-only preflight precedes admission/allocation;
all immutable bytes still hash-authenticated before parsing with held-FD checks.

Synthetic tests only until new frozen FULLsource review explicitly clears at most
ONE second index attempt. Scope remains1/4, with first failure preserved. Do not
rerun origin or read/parse actual index contents yet. Clean exacthead/tree, full
source metadata preflight and empty canonical marker must precede cleared attempt.
Any refusal preserves evidence and returns to design; no automatic third attempt,
source/pin/grammar widening or quota reset. Single cleanup/release path unchanged.

PR85 remains draft pending full finalsource/actual review, matching latest-head CI,
focused/causal checks and fourstandalone supplements. Prior CI3617passes belongs
60f0199, not later revisions. Historical conditional indexmetadata is not package-
member provenance/platform/ABI/freshness/current-key/runtimeauthority.
No network/download/decompression, package/member inspection, Pillow/decoder,
ctypes/filter execution, Docker/cgroup/daemon/system/updater changes, live data/
config/model calls/listeners, Core/Mind/Agent/route binding or enabled flags.
Original resource/filter gates precede decoder. Continuev15thenv16->v17->v18;
preservev0.14.0andparkedUI61. Shared ownership/admission helpers unchanged.

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
