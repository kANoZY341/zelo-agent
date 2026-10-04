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

## Current scope: v0.15 Media fixed local-index qualification

PR84 matched its reviewed tree, passed3580 CI tests,164synthetic selector tests,
1763Media tests,4standalone checks and7causal controls/14intended failures.
Read docs/reviews/media-package-index-{source-review,final-gates}.md.
PR83 origin proof remains accepted; origin3/4, ELFversion2/4, loader4/4 retained.
No further origin/ELF run, quota reset or changes to shared ownership helpers.

Astra Medium cleared docs/v0.15-media-local-index.md for OFFLINE implementation
only; see docs/reviews/media-local-index-design-review.md. Three local raw Packages
files have observed sizes/root IDs/modes matching the signed declaration plan,
but their contents have NOT yet been read, hashed or parsed. No network or decoder
is necessary if immutable bytes match the accepted signed uncompressed hashes.

Implement a fixed operator-only wrapper. Authenticate all three immutable byte
strings before any parser call. Preserve exact held-FD root/GID/mode/type/nlink/
size/hash identity, fixed selectors, capacity-one admission and a canonical new
four-attempt semantic scope across policy revisions. Reuse helpers unchanged.
Use ONE support cleanup/release path; never preliminary close_all then _release.
Interrupted close or Lease.finish must not retry original descriptor numbers.
Failure may leave only partial evidence; no receipt or universal fencing claim.
Pending proof precedes release; no subsequent proof-file I/O.

Only synthetic offline tests now. Actual local index read/hash/parse requires a
new frozen full source review explicitly clearing at most ONE fixed attempt,
then preflight and that attempt. No automatic retry or pin/grammar widening.
Final source/actual-evidence review, matching CI, focused/causal tests and standalone
supplements precede normal exact-tree merge. Index evidence remains conditional
historical attribution; package-member origin, platform/ABI/runtime authority,
freshness and current-key validity remain false.

No index download/decompression, package/member inspection, Pillow/decoder,
ctypes/libseccomp/filter execution, Docker/cgroup/host/daemon changes, model calls,
Core/Mind/Agent/task/route/registry binding, signing/OAuth, live env/data/config/
listener changes or enabled flags. Notices/import/frozen/builtin/dlopen/search/
ABI closure, materializer/build/resources/startup/installer/filter/decoder remain
separate; original resource/filter gates precede decoder. Preserve v0.14.0, UI61,
old proof scopes and all user data. Continue v0.15 then v0.16 -> v0.17 -> v0.18.

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
