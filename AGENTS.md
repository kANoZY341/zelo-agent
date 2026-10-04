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
The active work is v0.15 Media package archive acquisition implementation after PR85
merge 19e0be24cb0a60bac1f540f08a27d82082895e9d. Continue Core; UI needs fresh acceptance.
Do not polish, deploy or acceptance-test UI in this phase.

## Current scope: offline package acquisition implementation and source review

PR85 merged at 19e0be24cb0a60bac1f540f08a27d82082895e9d, matching its reviewed
tree. CI passed 3,640 tests, 209 selector/operator tests and 1,823 Media checks;
four standalone checks and ten causal controls (14 intended failures) passed.
Read docs/reviews/media-local-index-{final-review,final-gates}.md.
Accepted index proof SHA256:
199b3b16ee247497b782d78356bb88f3f67ed717401d5cb39ec7a8fc2c0b210d.
It authenticates three indexes and selects eight fixed package records. It does
not authenticate package members or grant platform/runtime authority.

Retain all scopes: origin 4/4 exhausted, index 2/4 (first refused, second passed),
ELF version 2/4, old loader 4/4. No further origin/index/ELF attempt or quota reset.
Shared ownership/admission helpers stay unchanged. Preserve every old proof.

The next acquisition proposal is runtime/media-package-acquisition-next-design.md.
Its preliminary review is archived in docs/reviews/media-package-acquisition-design-review-r1.md.
Astra Medium cleared the refined design for offline implementation; read
docs/v0.15-media-package-acquisition.md and the R2 design review. The source
references and license are retained under runtime/media-package-http-reference.
The signed eight-record lock is pinned at
0c7b800977288ea8beb88f765718d8df42e0a40729ef5bbc6c3d41c2f4f298d6.

Implement only the closed acquisition adapter and fake-only tests. No real DNS,
socket, TLS context/CA loading, endpoint probe or download is cleared. Real
stdlib HTTPResponse may parse synthetic BytesIO. Preserve exact ownership
transitions, pre-parser header bounds, direct verified TLS context, immutable
CA data, framed-body-only claims and explicitly cooperative deadlines. Hold
archive/CA/support FDs; JSON closes inside _Store. Count both read-back copies.

A frozen full source review must explicitly clear at most one eight-file batch;
actual review, matching CI and supplements precede normal merge. No automatic retry.

No package/member tool execution, Pillow/decoder, ctypes/filter, Docker/cgroup/
daemon/system/updater changes, live data/config/model calls/listeners, Core/Mind/
Agent/route binding or enabled flags. Original resource/filter gates precede image
decoder. Conditional historical attribution does not establish freshness, current
key validity, package-member provenance, platform/ABI or runtime authority.
Continue v0.15, then v0.16 -> v0.17 -> v0.18. Preserve v0.14.0 and parked UI #61.

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
