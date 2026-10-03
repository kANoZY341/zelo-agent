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
The active work is v0.15 Media publisher attribution qualification after private
file preparation PR #75 at `5f6c678`. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: v0.15 Media publisher attribution qualification

PR #75 merged with matching tested/reviewed tree, exact full CI 2,261 passed, two
supplemented skips and all gates in its receipt. Read
`docs/v0.15-media-publisher-qualification.md` before work. Astra Medium design
review recommends mature public PEP740 conversion/Sigstore verification under an
explicit hash-locked operator-trusted production root snapshot, offline and confined.
This is conditional historical attribution, not current TUF/trust freshness.

Phase one: bounded fixed provenance/tool-wheel metadata/acquisition and mature
binary-only dependency resolution in new private write-confined diagnostics. Freeze
all hashes, notices, hooks/path shape, root member and provenance cardinalities before
implementation acceptance. No new tool import/install until that inspection closes
the complete lock. Then record exact constants, implement/review a narrow optional
operator verifier/launcher and qualify actual crypto/confinement/failure cases.
All tools/dependencies/install/temp/cache/outputs remain private under this dedicated
repository. Never alter shared/system venv or caches. No arbitrary caller roots/pins,
unsigned publisher-derived policy, signing/OAuth, credentials or source-build hooks.

No Pillow import/install/execution, Media image/decoder/runtime/task/registry/
capability/Core/Agent/Mind route or live state change. Existing copy receipt remains
attribution-unverified; new conditional attribution evidence stays separate, and
combined current tree evidence requires a fresh unchanged scanner comparison.
No current root freshness, source/build/ABI/codec/license/runtime assurance follows.

Use the dedicated worktree and Astra Medium, adversarial offline fixtures, actual
closed-wheel crypto/confinement proof, exact full static review/CI and standalone
supplements before merge. No sudo, services/listeners, model requests, user data or
unrelated deletion. Production flags stay disabled; v0.14.0/UI #61 preserved.
Computer/external adapters and v0.16 -> v0.17 -> v0.18 remain separate/ordered.

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
